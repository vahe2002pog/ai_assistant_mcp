from __future__ import annotations

import base64
import ctypes
import json
import os
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import warnings
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE_DIR = os.path.join(ROOT, "voice")
WHISPER_DIR = os.path.join(ROOT, "utils", "whisper")
WHISPER_CLI = os.path.join(WHISPER_DIR, "whisper-cli.exe")
WHISPER_MODEL = os.path.join(VOICE_DIR, "models", "ggml-small.bin")
WAKE_WORD_DIR = os.path.join(VOICE_DIR, "wake-word")
WAKE_WORD_TFLITE = os.path.join(WAKE_WORD_DIR, "wake_word_model.tflite")
WAKE_WORD_KERAS = os.path.join(WAKE_WORD_DIR, "wake_word_model.keras")
WAKE_WORD_PREPROCESSING_CONFIG = os.path.join(WAKE_WORD_DIR, "preprocessing_config.txt")
ACTIVATION_AUDIO = os.path.join(ROOT, "src", "audio", "activation.MP3")
DEACTIVATION_AUDIO = os.path.join(ROOT, "src", "audio", "deactivation.MP3")

DEFAULT_SAMPLE_RATE = 16000
VOICE_PORT = int(os.environ.get("COMPASS_VOICE_PORT") or "8766")
VOICE_DAEMON_API_VERSION = 31
WAKE_WINDOW_SECONDS = 1.0
WAKE_WINDOW_SAMPLES = int(DEFAULT_SAMPLE_RATE * WAKE_WINDOW_SECONDS)
WAKE_STEP_MS = 250
WAKE_BLOCK_SAMPLES = int(DEFAULT_SAMPLE_RATE * WAKE_STEP_MS / 1000)
WAKE_THRESHOLD = float(os.environ.get("COMPASS_WAKE_THRESHOLD") or "0.7")
WAKE_COOLDOWN_SEC = float(os.environ.get("COMPASS_WAKE_COOLDOWN") or "1.5")
WAKE_REQUIRED_HITS = int(os.environ.get("COMPASS_WAKE_REQUIRED_HITS") or "2")
WAKE_SMOOTHING_WINDOWS = int(os.environ.get("COMPASS_WAKE_SMOOTHING_WINDOWS") or "2")
WAKE_FRAME_LENGTH = int(0.025 * DEFAULT_SAMPLE_RATE)
WAKE_FRAME_STEP = int(0.010 * DEFAULT_SAMPLE_RATE)
WAKE_FFT_LENGTH = 512
WAKE_N_MELS = 40
WAKE_N_MFCC = 13
WAKE_NUM_FRAMES = 1 + (WAKE_WINDOW_SAMPLES - WAKE_FRAME_LENGTH) // WAKE_FRAME_STEP
VOICE_SESSION_IDLE_TIMEOUT = float(os.environ.get("COMPASS_VOICE_SESSION_IDLE_TIMEOUT") or "120")
MIN_SPEECH_SECONDS = float(os.environ.get("COMPASS_MIN_SPEECH_SECONDS") or "0.65")
VOICE_RMS_THRESHOLD = float(os.environ.get("COMPASS_VOICE_RMS_THRESHOLD") or "0.003")
VOICE_NOISE_MULTIPLIER = float(os.environ.get("COMPASS_VOICE_NOISE_MULTIPLIER") or "2.0")
VOICE_NOISE_MARGIN = float(os.environ.get("COMPASS_VOICE_NOISE_MARGIN") or "0.0015")
VOICE_START_BLOCKS = int(os.environ.get("COMPASS_VOICE_START_BLOCKS") or "1")
VOICE_MIN_BAND_RATIO = float(os.environ.get("COMPASS_VOICE_MIN_BAND_RATIO") or "0.18")
VOICE_MAX_ZCR = float(os.environ.get("COMPASS_VOICE_MAX_ZCR") or "0.48")
VOICE_INPUT_GAIN = float(os.environ.get("COMPASS_VOICE_INPUT_GAIN") or "8.0")

EMPTY_TRANSCRIPT_PATTERNS = (
    "\u0440\u0435\u0434\u0430\u043a\u0442\u043e\u0440 \u0441\u0443\u0431\u0442\u0438\u0442\u0440\u043e\u0432",
    "\u0441\u0443\u0431\u0442\u0438\u0442\u0440",
    "\u043a\u043e\u0440\u0440\u0435\u043a\u0442\u043e\u0440",
    "\u0441\u0438\u043d\u0435\u0446\u043a\u0430\u044f",
    "\u0435\u0433\u043e\u0440\u043e\u0432\u0430",
    "\u0441\u043f\u0430\u0441\u0438\u0431\u043e \u0437\u0430 \u043f\u0440\u043e\u0441\u043c\u043e\u0442\u0440",
    "\u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0435\u043d\u0438\u0435 \u0441\u043b\u0435\u0434\u0443\u0435\u0442",
    "\u043f\u043e\u0434\u043f\u0438\u0441\u044b\u0432\u0430\u0439\u0442\u0435\u0441\u044c",
)


class EventBroker:
    def __init__(self) -> None:
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=128)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def emit(self, event: str, data: dict) -> None:
        payload = json.dumps({"event": event, "data": data}, ensure_ascii=False)
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass


BROKER = EventBroker()
SERVICE: Optional["VoiceService"] = None


def _exit_process_soon(delay: float = 0.25) -> None:
    time.sleep(delay)
    os._exit(0)


def _json_dumps(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _safe_unlink(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except Exception:
        pass


def _play_audio(path: str) -> None:
    if not os.path.isfile(path):
        return
    if os.name == "nt":
        alias = f"compass_{uuid.uuid4().hex}"
        winmm = ctypes.windll.winmm
        cmd_open = f'open "{path}" type mpegvideo alias {alias}'
        cmd_play = f"play {alias} wait"
        cmd_close = f"close {alias}"
        try:
            winmm.mciSendStringW(cmd_open, None, 0, None)
            winmm.mciSendStringW(cmd_play, None, 0, None)
        finally:
            winmm.mciSendStringW(cmd_close, None, 0, None)


def _write_wav(path: str, samples, sample_rate: int, gain: float = 1.0) -> None:
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    if gain and gain != 1.0:
        arr = arr * float(gain)
    arr = np.clip(arr, -1.0, 1.0)
    pcm = (arr * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())


def _temp_audio_path(suffix: str = ".wav") -> str:
    fd, path = tempfile.mkstemp(prefix="compass_voice_", suffix=suffix)
    os.close(fd)
    return path


def _record_until_silence(stop_event: threading.Event, silence_s: float = 1.3,
                          max_s: float = 30.0, initial_timeout_s: float = 10.0,
                          threshold: float = VOICE_RMS_THRESHOLD,
                          on_recording_start=None) -> tuple[Optional[str], bool]:
    import numpy as np
    import sounddevice as sd

    sample_rate = DEFAULT_SAMPLE_RATE
    block_size = int(sample_rate * 0.1)
    audio_q: queue.Queue = queue.Queue()
    chunks = []
    pre_roll = []
    noise_rms = []
    pre_roll_blocks = max(1, int(0.5 / (block_size / sample_rate)))

    def block_metrics(block) -> tuple[float, float, float]:  # noqa: ANN001
        flat = block.reshape(-1)
        if not flat.size:
            return 0.0, 0.0, 1.0
        if VOICE_INPUT_GAIN and VOICE_INPUT_GAIN != 1.0:
            flat = np.clip(flat * float(VOICE_INPUT_GAIN), -1.0, 1.0)
        rms = float(np.sqrt(np.mean(np.square(flat))))
        if rms <= 1e-6:
            return rms, 0.0, 1.0
        signs = np.signbit(flat)
        zcr = float(np.mean(signs[1:] != signs[:-1])) if flat.size > 1 else 0.0
        windowed = flat * np.hanning(flat.size)
        spectrum = np.abs(np.fft.rfft(windowed))
        power = spectrum * spectrum
        total = float(np.sum(power)) + 1e-12
        freqs = np.fft.rfftfreq(flat.size, 1.0 / sample_rate)
        speech_band = (freqs >= 85.0) & (freqs <= 3600.0)
        band_ratio = float(np.sum(power[speech_band]) / total)
        return rms, band_ratio, zcr

    def current_threshold() -> float:
        if len(noise_rms) < 4:
            return threshold
        floor = float(np.percentile(noise_rms[-40:], 85))
        return max(threshold, floor * VOICE_NOISE_MULTIPLIER, floor + VOICE_NOISE_MARGIN)

    def callback(indata, frames, time_info, status):  # noqa: ANN001
        if status:
            pass
        audio_q.put(indata.copy())

    started_at = time.monotonic()
    last_voice_at: Optional[float] = None
    speech_started = False
    voiced_blocks = 0
    candidate_run = 0
    block_seconds = block_size / sample_rate
    with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32",
                        blocksize=block_size, callback=callback):
        while not stop_event.is_set():
            now = time.monotonic()
            if now - started_at >= max_s:
                break
            if not speech_started and now - started_at >= initial_timeout_s:
                break
            try:
                block = audio_q.get(timeout=0.25)
            except queue.Empty:
                continue
            rms, band_ratio, zcr = block_metrics(block)
            gate = current_threshold()
            speech_like = (
                rms >= gate
                and band_ratio >= VOICE_MIN_BAND_RATIO
                and zcr <= VOICE_MAX_ZCR
            )
            if not speech_started:
                pre_roll.append(block)
                if len(pre_roll) > pre_roll_blocks:
                    pre_roll.pop(0)
                if speech_like:
                    candidate_run += 1
                    if candidate_run >= VOICE_START_BLOCKS:
                        speech_started = True
                        if on_recording_start is not None:
                            try:
                                on_recording_start()
                            except Exception:
                                pass
                        chunks.extend(pre_roll)
                        pre_roll.clear()
                        voiced_blocks += candidate_run
                        last_voice_at = now
                else:
                    candidate_run = 0
                    noise_rms.append(rms)
                    if len(noise_rms) > 80:
                        noise_rms = noise_rms[-80:]
                continue

            chunks.append(block)
            if speech_like:
                last_voice_at = now
                voiced_blocks += 1
            elif speech_started and last_voice_at is not None and now - last_voice_at >= silence_s:
                break

    if not chunks:
        return None, False
    if stop_event.is_set():
        return None, False
    if not speech_started:
        return None, False
    if voiced_blocks * block_seconds < MIN_SPEECH_SECONDS:
        return None, False
    path = _temp_audio_path(".wav")
    _write_wav(path, np.concatenate(chunks, axis=0), sample_rate, gain=VOICE_INPUT_GAIN)
    return path, True


def _transcribe(path: str) -> str:
    if not os.path.isfile(WHISPER_CLI):
        raise RuntimeError(f"whisper-cli not found: {WHISPER_CLI}")
    if not os.path.isfile(WHISPER_MODEL):
        raise RuntimeError(f"Whisper model not found: {WHISPER_MODEL}")

    base_cmd = [
        WHISPER_CLI,
        "-m", WHISPER_MODEL,
        "-l", "ru",
        "-nt",
        "-np",
        "-f", path,
    ]
    for cmd in (base_cmd + ["--vad"], base_cmd):
        proc = subprocess.run(
            cmd,
            cwd=WHISPER_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        out = (proc.stdout or "").strip()
        if proc.returncode == 0 and out:
            text = _clean_transcript(out)
            if _is_likely_empty_transcript(text):
                return ""
            return text
    err = (proc.stderr or proc.stdout or "Whisper failed").strip()
    raise RuntimeError(err[-800:])


def _clean_transcript(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*\[[^\]]+\]\s*", "", line).strip()
        if line:
            lines.append(line)
    return " ".join(lines).strip()


def _is_likely_empty_transcript(text: str) -> bool:
    norm = (text or "").casefold().replace("\u0451", "\u0435")
    norm = re.sub(r"\s+", " ", norm).strip(" \t\r\n.,!?;:\"'()[]{}")
    if not norm:
        return True
    return any(pattern in norm for pattern in EMPTY_TRANSCRIPT_PATTERNS)


class WakeWordDetector:
    def __init__(self, threshold: float = WAKE_THRESHOLD) -> None:
        self._threshold = threshold
        self._model_type = ""
        self._model = None
        self._input_details = None
        self._output_details = None
        self._np = None
        self._tf = None
        self._mfcc_mean = 0.0
        self._mfcc_std = 1.0
        self._load()

    def listen_until_detected(self, stop_event: threading.Event,
                              enabled_event: threading.Event,
                              manual_pending: threading.Event,
                              can_listen) -> bool:
        import sounddevice as sd

        np = self._np
        audio_q: queue.Queue = queue.Queue(maxsize=16)
        audio_buffer = np.zeros(WAKE_WINDOW_SAMPLES, dtype=np.float32)
        filled_samples = 0
        last_detection_time = 0.0
        positive_history = []
        consecutive_hits = 0

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                pass
            try:
                audio_q.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass

        with sd.InputStream(
            channels=1,
            samplerate=DEFAULT_SAMPLE_RATE,
            blocksize=WAKE_BLOCK_SAMPLES,
            dtype="float32",
            callback=callback,
        ):
            while (
                not stop_event.is_set()
                and enabled_event.is_set()
                and not manual_pending.is_set()
            ):
                if not can_listen():
                    return False
                try:
                    chunk = audio_q.get(timeout=0.25)
                except queue.Empty:
                    continue

                chunk_len = len(chunk)
                audio_buffer = np.roll(audio_buffer, -chunk_len)
                audio_buffer[-chunk_len:] = chunk
                filled_samples = min(WAKE_WINDOW_SAMPLES, filled_samples + chunk_len)
                if filled_samples < WAKE_WINDOW_SAMPLES:
                    continue

                probs = self.predict(audio_buffer)
                positive_prob = float(probs[1])
                positive_history.append(positive_prob)
                positive_history = positive_history[-WAKE_SMOOTHING_WINDOWS:]
                avg_positive = float(np.mean(positive_history))
                now = time.monotonic()

                if avg_positive >= self._threshold:
                    consecutive_hits += 1
                else:
                    consecutive_hits = 0

                if consecutive_hits >= WAKE_REQUIRED_HITS and now - last_detection_time >= WAKE_COOLDOWN_SEC:
                    BROKER.emit("wake_detected", {"confidence": avg_positive})
                    last_detection_time = now
                    consecutive_hits = 0
                    return True
        return False

    def predict(self, waveform_np):
        np = self._np
        x = self._waveform_to_model_input(waveform_np)
        if self._model_type == "keras":
            outputs = self._model(x, training=False).numpy()
            return self._to_probs(outputs)

        interpreter = self._model
        input_details = self._input_details
        output_details = self._output_details
        input_index = input_details[0]["index"]
        output_index = output_details[0]["index"]
        input_dtype = input_details[0]["dtype"]

        if input_dtype != np.float32:
            scale, zero_point = input_details[0]["quantization"]
            if scale == 0:
                raise RuntimeError("Invalid wake-word TFLite input quantization scale")
            x = x / scale + zero_point
            x = x.astype(input_dtype)
        else:
            x = x.astype(np.float32)

        interpreter.set_tensor(input_index, x)
        interpreter.invoke()
        outputs = interpreter.get_tensor(output_index)[0]

        output_dtype = output_details[0]["dtype"]
        if output_dtype != np.float32:
            scale, zero_point = output_details[0]["quantization"]
            if scale == 0:
                raise RuntimeError("Invalid wake-word TFLite output quantization scale")
            outputs = scale * (outputs.astype(np.float32) - zero_point)
        return self._to_probs(outputs)

    def _load(self) -> None:
        import numpy as np

        try:
            import tensorflow as tf
        except Exception as e:
            raise RuntimeError(
                "Wake-word model requires TensorFlow. Install requirements first."
            ) from e

        self._np = np
        self._tf = tf
        self._load_preprocessing_config()

        model_path = self._find_model_path((".tflite",), [WAKE_WORD_TFLITE])
        if model_path:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=".*tf\\.lite\\.Interpreter is deprecated.*",
                    category=UserWarning,
                )
                interpreter = tf.lite.Interpreter(model_path=model_path)
            interpreter.allocate_tensors()
            self._model_type = "tflite"
            self._model = interpreter
            self._input_details = interpreter.get_input_details()
            self._output_details = interpreter.get_output_details()
            return

        model_path = self._find_model_path((".keras", ".h5"), [WAKE_WORD_KERAS])
        if model_path:
            self._model_type = "keras"
            self._model = tf.keras.models.load_model(model_path)
            return

        raise FileNotFoundError(
            f"Wake-word model not found in {WAKE_WORD_DIR}"
        )

    def _find_model_path(self, suffixes: tuple[str, ...], preferred: list[str]) -> Optional[str]:
        for path in preferred:
            if os.path.isfile(path):
                return path
        try:
            names = sorted(os.listdir(WAKE_WORD_DIR))
        except OSError:
            return None
        for name in names:
            path = os.path.join(WAKE_WORD_DIR, name)
            if os.path.isfile(path) and name.lower().endswith(suffixes):
                return path
        return None

    def _load_preprocessing_config(self) -> None:
        if not os.path.isfile(WAKE_WORD_PREPROCESSING_CONFIG):
            return
        values = {}
        with open(WAKE_WORD_PREPROCESSING_CONFIG, "r", encoding="utf-8") as f:
            for line in f:
                if "=" not in line:
                    continue
                key, value = line.strip().split("=", 1)
                values[key] = value
        if "MEAN" in values:
            self._mfcc_mean = float(values["MEAN"])
        if "STD" in values:
            self._mfcc_std = float(values["STD"]) or 1.0

    def _waveform_to_model_input(self, waveform_np):
        tf = self._tf
        np = self._np
        waveform = tf.convert_to_tensor(waveform_np, dtype=tf.float32)
        mfcc = self._get_mfcc(waveform)
        mfcc = mfcc.numpy().astype(np.float32)
        return np.expand_dims(mfcc, axis=0)

    def _get_mfcc(self, waveform):
        tf = self._tf
        waveform = waveform[:WAKE_WINDOW_SAMPLES]
        padding_size = WAKE_WINDOW_SAMPLES - tf.shape(waveform)[0]
        zero_padding = tf.zeros([padding_size], dtype=tf.float32)
        waveform = tf.cast(waveform, tf.float32)
        waveform = tf.concat([waveform, zero_padding], axis=0)
        waveform.set_shape([WAKE_WINDOW_SAMPLES])
        waveform = waveform / (tf.reduce_max(tf.abs(waveform)) + 1e-9)

        spectrogram = tf.signal.stft(
            waveform,
            frame_length=WAKE_FRAME_LENGTH,
            frame_step=WAKE_FRAME_STEP,
            fft_length=WAKE_FFT_LENGTH,
            window_fn=tf.signal.hann_window,
        )
        spectrogram = tf.abs(spectrogram)
        mel_matrix = tf.signal.linear_to_mel_weight_matrix(
            num_mel_bins=WAKE_N_MELS,
            num_spectrogram_bins=WAKE_FFT_LENGTH // 2 + 1,
            sample_rate=DEFAULT_SAMPLE_RATE,
            lower_edge_hertz=20.0,
            upper_edge_hertz=4000.0,
        )
        mel_spectrogram = tf.matmul(tf.square(spectrogram), mel_matrix)
        log_mel = tf.math.log(mel_spectrogram + 1e-6)
        mfcc = tf.signal.mfccs_from_log_mel_spectrograms(log_mel)
        mfcc = mfcc[..., :WAKE_N_MFCC]
        mfcc = (mfcc - self._mfcc_mean) / self._mfcc_std
        mfcc = mfcc[..., tf.newaxis]
        mfcc.set_shape([WAKE_NUM_FRAMES, WAKE_N_MFCC, 1])
        return mfcc

    def _to_probs(self, outputs):
        np = self._np
        outputs = np.array(outputs)
        if outputs.ndim == 1:
            outputs = outputs[np.newaxis, :]
        if np.all(outputs >= 0) and np.all(outputs <= 1):
            sums = np.sum(outputs, axis=1)
            if np.allclose(sums, 1.0, atol=1e-3):
                return outputs[0]
        outputs = outputs - np.max(outputs, axis=1, keepdims=True)
        exp = np.exp(outputs)
        probs = exp / np.sum(exp, axis=1, keepdims=True)
        return probs[0]


class VoiceSessionPanel:
    WIDTH = 400
    HEIGHT = 132
    PANEL_RADIUS = 14
    DARK_BG = "#111213"
    LIGHT_BG = "#f7f5f2"

    def __init__(self, port: int) -> None:
        self._port = port
        self._q: queue.Queue = queue.Queue()
        self._window = None
        self._ready = threading.Event()
        self._tk_root = None
        self._tk_canvas = None
        self._tk_text_var = None
        self._tk_stop_btn = None
        self._tk_frame = None
        self._tk_text = None
        self._tk_close_btn = None
        self._tk_open_btn = None
        self._tk_thread = None
        self._tk_payload: dict = {}
        self._tk_tick = 0

    def update(self, payload: dict) -> None:
        try:
            self._q.put_nowait(dict(payload))
        except queue.Full:
            pass
        self._apply(payload)

    def run(self) -> bool:
        panel_backend = os.environ.get("COMPASS_VOICE_PANEL", "webview").strip().lower()
        if panel_backend == "tk" and self._run_tk():
            return True

        try:
            import webview  # type: ignore
        except Exception:
            return self._run_tk()

        x, y = self._screen_position(self.WIDTH, self.HEIGHT)
        try:
            self._window = webview.create_window(
                "Compass voice",
                html=self._panel_html(),
                width=self.WIDTH,
                height=self.HEIGHT,
                x=x,
                y=y,
                resizable=False,
                frameless=True,
                hidden=True,
                easy_drag=False,
                shadow=False,
                focus=False,
                on_top=True,
                transparent=False,
                background_color=self.DARK_BG,
            )
        except Exception:
            try:
                self._window = webview.create_window(
                    "Compass voice",
                    html=self._panel_html(),
                    width=self.WIDTH,
                    height=self.HEIGHT,
                    x=x,
                    y=y,
                    resizable=False,
                    frameless=True,
                    hidden=True,
                    easy_drag=False,
                    on_top=True,
                    transparent=False,
                    background_color=self.DARK_BG,
                )
            except Exception:
                try:
                    self._window = webview.create_window(
                        "Compass voice",
                        html=self._panel_html(),
                        width=self.WIDTH,
                        height=self.HEIGHT,
                        x=x,
                        y=y,
                        resizable=False,
                        frameless=True,
                        hidden=True,
                        easy_drag=False,
                        on_top=True,
                        background_color=self.DARK_BG,
                    )
                except Exception:
                    return self._run_tk()

        def after_start() -> None:
            self._ready.set()
            threading.Thread(target=self._hide_from_taskbar_retry, daemon=True,
                             name="voice-overlay-taskbar").start()
            threading.Thread(target=pump_states, daemon=True,
                             name="voice-overlay-pump").start()

        def pump_states() -> None:
            while True:
                try:
                    payload = self._q.get(timeout=0.2)
                except queue.Empty:
                    continue
                self._apply(payload)

        try:
            webview.start(after_start, private_mode=True)
            return True
        except TypeError:
            try:
                webview.start(after_start)
                return True
            except Exception:
                return self._run_tk()
        except Exception:
            return self._run_tk()

    def _apply(self, payload: dict) -> None:
        if self._tk_root is not None:
            if threading.current_thread() is self._tk_thread:
                self._apply_tk(payload)
            return
        if not self._window or not self._ready.is_set():
            return
        try:
            visible = bool(payload.get("session_active"))
            script = f"window.updateCompassVoicePanel({json.dumps(payload, ensure_ascii=False)});"
            if visible:
                self._hide_from_taskbar()
                try:
                    self._window.evaluate_js(script)
                except Exception:
                    pass
                try:
                    self._window.show()
                except Exception:
                    pass
                self._hide_from_taskbar()
            else:
                try:
                    self._window.evaluate_js(script)
                except Exception:
                    pass
                try:
                    self._window.hide()
                except Exception:
                    pass
        except Exception:
            pass

    def _native_hwnd(self) -> Optional[int]:
        if not self._window:
            return None
        native = getattr(self._window, "native", None)
        if native is None:
            return None
        for attr in ("Handle", "handle", "hwnd"):
            value = getattr(native, attr, None)
            if value is None:
                continue
            try:
                if hasattr(value, "ToInt64"):
                    return int(value.ToInt64())
                if hasattr(value, "ToInt32"):
                    return int(value.ToInt32())
                return int(value)
            except Exception:
                continue
        return None

    def _hide_from_taskbar_retry(self) -> None:
        for _ in range(25):
            if self._hide_from_taskbar():
                return
            time.sleep(0.1)

    def _hide_from_taskbar(self) -> bool:
        if sys.platform != "win32":
            return False
        hwnd = self._native_hwnd()
        if not hwnd:
            return False
        try:
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            gwl_exstyle = -20
            ws_ex_toolwindow = 0x00000080
            ws_ex_appwindow = 0x00040000
            swp_nosize = 0x0001
            swp_nomove = 0x0002
            swp_nozorder = 0x0004
            swp_framechanged = 0x0020
            hwnd_arg = wintypes.HWND(hwnd)
            get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
            set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
            get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_window_long.restype = ctypes.c_ssize_t
            set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            set_window_long.restype = ctypes.c_ssize_t
            user32.SetWindowPos.argtypes = [
                wintypes.HWND,
                wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.UINT,
            ]
            user32.SetWindowPos.restype = wintypes.BOOL
            exstyle = get_window_long(hwnd_arg, gwl_exstyle)
            exstyle = (exstyle | ws_ex_toolwindow) & ~ws_ex_appwindow
            set_window_long(hwnd_arg, gwl_exstyle, exstyle)
            self._apply_rounded_window_region(hwnd_arg, user32, wintypes)
            try:
                dwmapi = ctypes.windll.dwmapi
                corner_preference = ctypes.c_int(2)  # DWMWCP_ROUND
                dwmapi.DwmSetWindowAttribute(
                    hwnd_arg,
                    33,  # DWMWA_WINDOW_CORNER_PREFERENCE
                    ctypes.byref(corner_preference),
                    ctypes.sizeof(corner_preference),
                )
            except Exception:
                pass
            user32.SetWindowPos(
                hwnd_arg,
                None,
                0,
                0,
                0,
                0,
                swp_nosize | swp_nomove | swp_nozorder | swp_framechanged,
            )
            return True
        except Exception:
            return False

    def _apply_managed_rounded_region(self) -> bool:
        native = getattr(self._window, "native", None) if self._window else None
        if native is None:
            return False
        try:
            from System import Action  # type: ignore
            from System.Drawing import Region  # type: ignore
            from System.Drawing.Drawing2D import GraphicsPath  # type: ignore

            def apply_region() -> None:
                client_size = getattr(native, "ClientSize", None)
                width = int(getattr(client_size, "Width", 0) or getattr(native, "Width", 0) or self.WIDTH)
                height = int(getattr(client_size, "Height", 0) or getattr(native, "Height", 0) or self.HEIGHT)
                width = max(1, width)
                height = max(1, height)
                scale = max(width / max(1, self.WIDTH), height / max(1, self.HEIGHT), 1.0)
                diameter = max(2, int(self.PANEL_RADIUS * 2 * scale))
                diameter = min(diameter, width, height)
                path = GraphicsPath()
                path.AddArc(0, 0, diameter, diameter, 180, 90)
                path.AddArc(width - diameter - 1, 0, diameter, diameter, 270, 90)
                path.AddArc(width - diameter - 1, height - diameter - 1, diameter, diameter, 0, 90)
                path.AddArc(0, height - diameter - 1, diameter, diameter, 90, 90)
                path.CloseFigure()
                old_region = getattr(native, "Region", None)
                native.Region = Region(path)
                path.Dispose()
                if old_region is not None:
                    try:
                        old_region.Dispose()
                    except Exception:
                        pass

            if bool(getattr(native, "InvokeRequired", False)):
                native.Invoke(Action(apply_region))
            else:
                apply_region()
            return True
        except Exception:
            return False

    def _apply_rounded_window_region(self, hwnd_arg, user32, wintypes) -> None:  # noqa: ANN001
        self._apply_managed_rounded_region()
        try:
            user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            user32.GetWindowRect.restype = wintypes.BOOL
            handles = self._rounded_region_handles(hwnd_arg, user32, wintypes)
            for handle in handles:
                rect = wintypes.RECT()
                width = self.WIDTH
                height = self.HEIGHT
                if user32.GetWindowRect(handle, ctypes.byref(rect)):
                    width = max(1, int(rect.right - rect.left))
                    height = max(1, int(rect.bottom - rect.top))
                self._apply_win32_rounded_region(handle, user32, wintypes, width, height)
        except Exception:
            pass

    def _rounded_region_handles(self, hwnd_arg, user32, wintypes) -> list:  # noqa: ANN001
        handles = []
        seen: set[int] = set()

        def add(handle) -> None:  # noqa: ANN001
            try:
                value = int(handle.value if hasattr(handle, "value") else handle)
            except Exception:
                return
            if not value or value in seen:
                return
            seen.add(value)
            handles.append(wintypes.HWND(value))

        add(hwnd_arg)
        try:
            ga_root = 2
            user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetAncestor.restype = wintypes.HWND
            add(user32.GetAncestor(hwnd_arg, ga_root))
        except Exception:
            pass
        try:
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            current = hwnd_arg
            for _ in range(6):
                parent = user32.GetParent(current)
                if not parent:
                    break
                add(parent)
                current = parent
        except Exception:
            pass
        return handles

    def _apply_win32_rounded_region(self, hwnd_arg, user32, wintypes, width: int, height: int) -> None:  # noqa: ANN001
        dpi_scale = max(width / max(1, self.WIDTH), height / max(1, self.HEIGHT), 1.0)
        diameter = max(2, int(self.PANEL_RADIUS * 2 * dpi_scale))
        gdi32 = ctypes.windll.gdi32
        gdi32.CreateRoundRectRgn.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE
        gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        gdi32.DeleteObject.restype = wintypes.BOOL
        user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
        user32.SetWindowRgn.restype = ctypes.c_int
        region = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, diameter, diameter)
        if region and not user32.SetWindowRgn(hwnd_arg, region, True):
            gdi32.DeleteObject(region)

    def _apply_tk_rounded_region(self) -> None:
        if sys.platform != "win32" or self._tk_root is None:
            return
        try:
            from ctypes import wintypes

            root = self._tk_root
            root.update_idletasks()
            hwnd_arg = wintypes.HWND(int(root.winfo_id()))
            user32 = ctypes.windll.user32
            self._apply_win32_rounded_region(
                hwnd_arg,
                user32,
                wintypes,
                int(root.winfo_width() or self.WIDTH),
                int(root.winfo_height() or self.HEIGHT),
            )
        except Exception:
            pass

    def _run_tk(self) -> bool:
        try:
            import tkinter as tk
        except Exception:
            return False

        root = tk.Tk()
        self._tk_root = root
        self._tk_thread = threading.current_thread()
        self._tk_text_var = tk.StringVar(value=self._state_text("idle"))
        x, y = self._screen_position(self.WIDTH, self.HEIGHT)
        colors = self._tk_theme_colors("dark")

        root.overrideredirect(True)
        root.configure(bg=colors["bg"])
        root.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        try:
            root.attributes("-toolwindow", True)
        except Exception:
            pass
        self._apply_tk_rounded_region()

        frame = tk.Frame(root, bg=colors["bg"], highlightthickness=1,
                         highlightbackground=colors["border"])
        frame.place(x=0, y=0, width=self.WIDTH, height=self.HEIGHT)
        self._tk_frame = frame
        canvas = tk.Canvas(frame, width=82, height=88, bg=colors["bg"],
                           highlightthickness=0, bd=0)
        canvas.place(x=14, y=24)
        self._tk_canvas = canvas
        text = tk.Label(frame, textvariable=self._tk_text_var,
                        bg=colors["bg"], fg=colors["fg"],
                        justify="left", anchor="w", wraplength=244,
                        font=("Segoe UI", 9))
        text.place(x=112, y=22, width=244, height=48)
        self._tk_text = text

        close_btn = tk.Button(frame, text="x", command=lambda: self._request_async("/session/close"),
                              bg=colors["button"], fg=colors["fg"],
                              activebackground=colors["button_hover"],
                              activeforeground=colors["fg"], relief="flat", bd=0,
                              font=("Segoe UI", 9), cursor="hand2")
        close_btn.place(x=self.WIDTH - 34, y=10, width=26, height=26)
        self._tk_close_btn = close_btn
        open_btn = tk.Button(frame, text="\u041f\u0435\u0440\u0435\u0439\u0442\u0438 \u0432 \u0447\u0430\u0442",
                             command=lambda: self._request_async("/open-chat"),
                             bg=colors["accent"], fg=colors["accent_fg"], activebackground=colors["accent_hover"],
                             activeforeground="#16110d", relief="flat", bd=0,
                             font=("Segoe UI", 8, "bold"), cursor="hand2")
        open_btn.place(x=112, y=88, width=126, height=30)
        self._tk_open_btn = open_btn
        stop_btn = tk.Button(frame, text="\u041e\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c",
                             command=lambda: self._request_async("/session/stop"),
                             bg=colors["button"], fg=colors["muted"],
                             activebackground=colors["button_hover"],
                             activeforeground=colors["fg"], relief="flat", bd=0,
                             font=("Segoe UI", 8), cursor="hand2")
        self._tk_stop_btn = stop_btn
        stop_btn.place(x=248, y=88, width=126, height=30)

        drag = {"x": 0, "y": 0}

        def drag_start(event):  # noqa: ANN001
            drag["x"] = event.x
            drag["y"] = event.y

        def drag_move(event):  # noqa: ANN001
            root.geometry(f"+{event.x_root - drag['x']}+{event.y_root - drag['y']}")

        for widget in (frame, canvas, text):
            widget.bind("<ButtonPress-1>", drag_start)
            widget.bind("<B1-Motion>", drag_move)

        def pump() -> None:
            while True:
                try:
                    payload = self._q.get_nowait()
                except queue.Empty:
                    break
                self._apply_tk(payload)
            self._draw_tk_icon()
            root.after(90, pump)

        def keep_on_top() -> None:
            try:
                root.attributes("-topmost", True)
                if bool(self._tk_payload.get("session_active")):
                    root.lift()
            except Exception:
                pass
            root.after(1000, keep_on_top)

        self._ready.set()
        root.after(0, pump)
        root.after(500, keep_on_top)
        try:
            root.mainloop()
            return True
        except Exception:
            return False

    def _apply_tk(self, payload: dict) -> None:
        root = self._tk_root
        if root is None:
            return
        self._tk_payload = dict(payload)
        self._apply_tk_theme(str(payload.get("ui_theme") or "dark"))
        visible = bool(payload.get("session_active"))
        try:
            if visible:
                root.deiconify()
                self._apply_tk_rounded_region()
                root.lift()
            else:
                root.withdraw()
        except Exception:
            pass
        text = payload.get("reply") or payload.get("text") or payload.get("status_text")
        if not text:
            text = self._state_text(str(payload.get("state") or "idle"))
        try:
            if self._tk_text_var is not None:
                self._tk_text_var.set(str(text))
        except Exception:
            pass
        try:
            if self._tk_stop_btn is not None:
                can_stop = str(payload.get("state") or "") in ("recording", "transcribing", "thinking", "speaking")
                self._tk_stop_btn.configure(state=("normal" if can_stop else "disabled"))
        except Exception:
            pass

    @staticmethod
    def _normalize_theme(theme: str) -> str:
        return "light" if theme == "light" else "dark"

    def _tk_theme_colors(self, theme: str) -> dict[str, str]:
        if self._normalize_theme(theme) == "light":
            return {
                "bg": self.LIGHT_BG,
                "fg": "#1d1d1d",
                "muted": "#6f6860",
                "border": "#ddd8cf",
                "button": "#e9e5de",
                "button_hover": "#ded8cf",
                "accent": "#ff6a1a",
                "accent_hover": "#ff7a30",
                "accent_fg": "#16110d",
            }
        return {
            "bg": self.DARK_BG,
            "fg": "#ebebeb",
            "muted": "#6b7175",
            "border": "#303235",
            "button": "#252829",
            "button_hover": "#303335",
            "accent": "#ff6a1a",
            "accent_hover": "#ff7a30",
            "accent_fg": "#16110d",
        }

    def _apply_tk_theme(self, theme: str) -> None:
        colors = self._tk_theme_colors(theme)
        try:
            if self._tk_root is not None:
                self._tk_root.configure(bg=colors["bg"])
            if self._tk_frame is not None:
                self._tk_frame.configure(bg=colors["bg"], highlightbackground=colors["border"])
            if self._tk_canvas is not None:
                self._tk_canvas.configure(bg=colors["bg"])
            if self._tk_text is not None:
                self._tk_text.configure(bg=colors["bg"], fg=colors["fg"])
            if self._tk_close_btn is not None:
                self._tk_close_btn.configure(
                    bg=colors["button"], fg=colors["fg"],
                    activebackground=colors["button_hover"], activeforeground=colors["fg"],
                )
            if self._tk_open_btn is not None:
                self._tk_open_btn.configure(
                    bg=colors["accent"], fg=colors["accent_fg"],
                    activebackground=colors["accent_hover"], activeforeground=colors["accent_fg"],
                )
            if self._tk_stop_btn is not None:
                self._tk_stop_btn.configure(
                    bg=colors["button"], fg=colors["muted"],
                    activebackground=colors["button_hover"], activeforeground=colors["fg"],
                )
        except Exception:
            pass

    def _draw_tk_icon(self) -> None:
        canvas = self._tk_canvas
        if canvas is None:
            return
        self._tk_tick += 1
        payload = self._tk_payload or {}
        state = str(payload.get("state") or "idle")
        visible = bool(payload.get("session_active"))
        try:
            canvas.delete("all")
            if not visible:
                return
            import math
            tick = self._tk_tick
            cx, cy = 41, 44
            main = "#f8fafc"
            accent = "#ff842d"
            dim = "#46505f"
            pulse = (math.sin(tick / 4.0) + 1.0) / 2.0
            if state in ("activated", "wake_listening"):
                r = 25 + pulse * 10
                canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                   fill=accent, outline="", stipple="gray50")
            elif state in ("listening", "recording"):
                r = 29 + pulse * 5
                canvas.create_oval(cx - r, cy - r, cx + r, cy + r,
                                   outline=accent, width=3)
            elif state in ("transcribing", "thinking"):
                for i in range(3):
                    a = tick / 5.0 + i * 2.09
                    x = cx + math.cos(a) * 30
                    y = cy + math.sin(a) * 30
                    canvas.create_oval(x - 5, y - 5, x + 5, y + 5,
                                       fill=accent, outline="")
            elif state == "speaking":
                for i in range(3):
                    r = 19 + i * 9 + pulse * 5
                    canvas.create_arc(cx - r, cy - r, cx + r, cy + r,
                                      start=-38, extent=76, outline=accent,
                                      width=2, style="arc")
            else:
                canvas.create_oval(cx - 30, cy - 30, cx + 30, cy + 30,
                                   outline=dim, width=2)

            canvas.create_oval(cx - 28, cy - 28, cx + 28, cy + 28,
                               outline=main, width=2)
            canvas.create_polygon(cx, cy - 23, cx + 11, cy, cx, cy + 23, cx - 11, cy,
                                  fill=main, outline="")
            canvas.create_polygon(cx, cy - 23, cx + 11, cy, cx, cy,
                                  fill=accent, outline="")
            star_y = 11 - pulse * 3 if state in ("listening", "recording") else 13
            canvas.create_polygon(cx, star_y - 8, cx + 3, star_y - 3,
                                  cx + 8, star_y, cx + 3, star_y + 3,
                                  cx, star_y + 8, cx - 3, star_y + 3,
                                  cx - 8, star_y, cx - 3, star_y - 3,
                                  fill=accent, outline="")
        except Exception:
            pass

    def _state_text(self, state: str) -> str:
        return {
            "activated": "\u0410\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u043d\u043e",
            "listening": "\u0421\u043b\u0443\u0448\u0430\u044e...",
            "recording": "\u0421\u043b\u0443\u0448\u0430\u044e...",
            "transcribing": "\u0420\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u044e...",
            "thinking": "\u0414\u0443\u043c\u0430\u044e...",
            "speaking": "\u0413\u043e\u0432\u043e\u0440\u044e...",
            "error": "\u041e\u0448\u0438\u0431\u043a\u0430 \u0433\u043e\u043b\u043e\u0441\u0430",
        }.get(state, "\u0413\u043e\u0442\u043e\u0432\u043e")

    def _request_async(self, path: str) -> None:
        threading.Thread(target=self._request, args=(path,), daemon=True).start()

    def _request(self, path: str) -> None:
        try:
            import urllib.request
            req = urllib.request.Request(
                f"http://127.0.0.1:{self._port}{path}",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=1.5).close()
        except Exception:
            pass

    def _panel_html(self) -> str:
        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    :root {{
      --bg: {self.DARK_BG};
      --text: #ebebeb;
      --muted: #6b7175;
      --border: rgba(255, 255, 255, 0.13);
      --button: rgba(255, 255, 255, 0.08);
      --accent: #ff6a1a;
      --accent-fg: #16110d;
    }}
    .light {{
      --bg: {self.LIGHT_BG};
      --text: #1d1d1d;
      --muted: #6f6860;
      --border: rgba(18, 18, 18, 0.18);
      --button: rgba(18, 18, 18, 0.08);
      --accent: #ff6a1a;
      --accent-fg: #16110d;
    }}
    html, body {{
      margin: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: var(--bg);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    body {{
      cursor: default;
    }}
    body.hidden #panel {{
      opacity: 0;
      pointer-events: none;
      transform: translateY(10px);
    }}
    #panel {{
      box-sizing: border-box;
      position: relative;
      width: 100%;
      height: 100%;
      color: var(--text);
      background: var(--bg);
      border-radius: 14px;
      overflow: hidden;
      box-shadow: inset 0 0 0 1px var(--border);
      transition: opacity 140ms ease, transform 140ms ease;
    }}
    #icon {{
      position: absolute;
      left: 14px;
      top: 50%;
      width: 72px;
      height: 72px;
      display: block;
      background: transparent;
      border: 0;
      object-fit: contain;
      transform: translateY(-50%) scale(1.15);
      transform-origin: center;
    }}
    #text {{
      position: absolute;
      left: 106px;
      right: 44px;
      top: 22px;
      bottom: 52px;
      min-width: 0;
      font-size: 14px;
      line-height: 1.3;
      overflow: hidden;
      overflow-wrap: anywhere;
      color: var(--text);
    }}
    #close, button {{
      border: 0;
      color: inherit;
      cursor: pointer;
      font: inherit;
    }}
    #close {{
      position: absolute;
      top: 10px;
      right: 10px;
      width: 26px;
      height: 26px;
      padding: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      background: var(--button);
      line-height: 1;
      text-align: center;
      font-size: 0;
      font-weight: 650;
    }}
    #close::before {{
      content: "\\00d7";
      display: block;
      font-size: 18px;
      line-height: 1;
      transform: translateY(-1px);
    }}
    #actions {{
      position: absolute;
      left: 106px;
      right: 16px;
      bottom: 10px;
      display: flex;
      gap: 10px;
      min-width: 0;
    }}
    button.action {{
      box-sizing: border-box;
      flex: 0 0 126px;
      width: 126px;
      height: 30px;
      padding: 0 10px;
      border-radius: 8px;
      background: var(--button);
      font-size: 12px;
      white-space: nowrap;
      display: flex;
      align-items: center;
      justify-content: center;
      line-height: 1;
    }}
    button.action.primary {{
      background: var(--accent);
      color: var(--accent-fg);
      font-weight: 650;
    }}
    button:hover {{
      filter: brightness(1.12);
    }}
    button:disabled {{
      cursor: default;
      opacity: .38;
      filter: none;
    }}
    #icon, #text {{
      pointer-events: none;
    }}
  </style>
</head>
<body class="hidden">
  <div id="panel">
    <img id="icon" alt="">
    <div id="text">Слушаю...</div>
    <button id="close" title="Закрыть">×</button>
    <div id="actions">
      <button id="open-chat" class="action primary" type="button">Перейти в чат</button>
      <button id="stop" class="action" type="button">Остановить</button>
    </div>
  </div>
  <script>
    const port = {self._port};
    const icon = document.getElementById("icon");
    const text = document.getElementById("text");
    const stopButton = document.getElementById("stop");
    const stateMap = {{
      activated: "compass-wake-pulse",
      wake_listening: "compass-wake-pulse",
      listening: "compass-listen-pulse",
      recording: "compass-listen-pulse",
      transcribing: "compass-think-signal",
      thinking: "compass-think-signal",
      speaking: "compass-speak-signal",
      error: "compass-wake-pulse"
    }};
    let currentTheme = "dark";
    function theme(data) {{
      const next = data && data.ui_theme === "light" ? "light" : "dark";
      currentTheme = next;
      document.documentElement.classList.toggle("light", next === "light");
      document.documentElement.classList.toggle("dark", next !== "light");
      return next;
    }}
    function request(path) {{
      return fetch(`http://127.0.0.1:${{port}}${{path}}`, {{ method: "POST" }}).catch(() => {{}});
    }}
    function stateText(state) {{
      if (state === "activated") return "Активировано";
      if (state === "listening" || state === "recording") return "Слушаю...";
      if (state === "transcribing") return "Распознаю...";
      if (state === "thinking") return "Думаю...";
      if (state === "speaking") return "Говорю...";
      if (state === "error") return "Ошибка голосового режима";
      return "Готово";
    }}
    window.updateCompassVoicePanel = function(data) {{
      const state = data.state || "wake_listening";
      const visible = !!data.session_active;
      const nextTheme = theme(data);
      document.body.classList.toggle("hidden", !visible);
      const base = stateMap[state] || stateMap.wake_listening;
      icon.src = `http://127.0.0.1:${{port}}/animation/${{base}}-${{nextTheme}}.svg`;
      stopButton.disabled = !["recording", "transcribing", "thinking", "speaking"].includes(state);
      text.textContent = data.reply
        ? data.reply
        : (data.text || data.status_text || stateText(state));
    }};
    document.getElementById("open-chat").addEventListener("click", () => request("/open-chat"));
    document.getElementById("stop").addEventListener("click", () => request("/session/stop"));
    document.getElementById("close").addEventListener("click", () => request("/session/close"));
    updateCompassVoicePanel({{ session_active: false, state: "idle" }});
  </script>
</body>
</html>"""

    def _html(self) -> str:
        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    html, body {{
      margin: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: transparent;
    }}
    #hit {{
      width: 100vw;
      height: 100vh;
      display: grid;
      place-items: center;
      background: transparent;
      cursor: pointer;
      -webkit-app-region: drag;
    }}
    img {{
      width: 100px;
      height: 100px;
      display: block;
      background: transparent;
      border: 0;
      -webkit-app-region: no-drag;
    }}
  </style>
</head>
<body>
  <div id="hit" title="Открыть голосовой чат">
    <img id="icon" alt="">
  </div>
  <script>
    const port = {self._port};
    const icon = document.getElementById("icon");
    const stateMap = {{
      idle: "compass-wake-pulse",
      wake_listening: "compass-wake-pulse",
      recording: "compass-listen-pulse",
      transcribing: "compass-think-signal",
      thinking: "compass-think-signal",
      speaking: "compass-speak-signal",
      error: "compass-wake-pulse"
    }};
    function theme() {{
      return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
        ? "light"
        : "dark";
    }}
    window.setCompassState = function(state) {{
      const base = stateMap[state] || stateMap.wake_listening;
      icon.src = `http://127.0.0.1:${{port}}/animation/${{base}}-${{theme()}}.svg`;
      document.body.style.opacity = state === "idle" ? "0.68" : "1";
    }};
    document.getElementById("hit").addEventListener("click", async () => {{
      try {{
        await fetch(`http://127.0.0.1:${{port}}/open-chat`, {{ method: "POST" }});
      }} catch (_) {{}}
    }});
    setCompassState("wake_listening");
  </script>
</body>
</html>"""

    @staticmethod
    def _screen_position(width: int, height: int) -> tuple[int, int]:
        if os.name != "nt":
            return 30, 30
        try:
            user32 = ctypes.windll.user32
            sw = int(user32.GetSystemMetrics(0))
            sh = int(user32.GetSystemMetrics(1))
            return max(0, (sw - width) // 2), max(0, sh - height - 54)
        except Exception:
            return 30, 30


class SileroTTS:
    _CONFIG = {
        "hub_language": "ru",
        "hub_speaker": "v4_ru",
        "speaker": "baya",
        "sample_rate": 48000,
    }
    _WORD_OVERRIDES = {
        "ai": "\u044d\u0439 \u0430\u0439",
        "api": "\u044d\u0439 \u043f\u0438 \u0430\u0439",
        "app": "\u044d\u043f",
        "apps": "\u044d\u043f\u0441",
        "assistant": "\u0430\u0441\u0441\u0438\u0441\u0442\u0435\u043d\u0442",
        "browser": "\u0431\u0440\u0430\u0443\u0437\u0435\u0440",
        "cache": "\u043a\u044d\u0448",
        "chat": "\u0447\u0430\u0442",
        "click": "\u043a\u043b\u0438\u043a",
        "codex": "\u043a\u043e\u0434\u0435\u043a\u0441",
        "desktop": "\u0434\u0435\u0441\u043a\u0442\u043e\u043f",
        "docker": "\u0434\u043e\u043a\u0435\u0440",
        "download": "\u0434\u0430\u0443\u043d\u043b\u043e\u0434",
        "edge": "\u044d\u0434\u0436",
        "email": "\u0438\u043c\u0435\u0439\u043b",
        "file": "\u0444\u0430\u0439\u043b",
        "github": "\u0433\u0438\u0442\u0445\u0430\u0431",
        "google": "\u0433\u0443\u0433\u043b",
        "hostagent": "\u0445\u043e\u0441\u0442 \u044d\u0439\u0434\u0436\u0435\u043d\u0442",
        "html": "\u044d\u0439\u0447 \u0442\u0438 \u044d\u043c \u044d\u043b",
        "http": "\u044d\u0439\u0447 \u0442\u0438 \u0442\u0438 \u043f\u0438",
        "https": "\u044d\u0439\u0447 \u0442\u0438 \u0442\u0438 \u043f\u0438 \u044d\u0441",
        "json": "\u0434\u0436\u0435\u0439\u0441\u043e\u043d",
        "linux": "\u043b\u0438\u043d\u0443\u043a\u0441",
        "microsoft": "\u043c\u0430\u0439\u043a\u0440\u043e\u0441\u043e\u0444\u0442",
        "openai": "\u043e\u0443\u043f\u0435\u043d \u044d\u0439 \u0430\u0439",
        "password": "\u043f\u0430\u0441\u0441\u0432\u043e\u0440\u0434",
        "python": "\u043f\u0430\u0439\u0442\u043e\u043d",
        "server": "\u0441\u0435\u0440\u0432\u0435\u0440",
        "silero": "\u0441\u0438\u043b\u0435\u0440\u043e",
        "telegram": "\u0442\u0435\u043b\u0435\u0433\u0440\u0430\u043c",
        "token": "\u0442\u043e\u043a\u0435\u043d",
        "tts": "\u0442\u0438 \u0442\u0438 \u044d\u0441",
        "url": "\u0443 \u044d\u0440 \u044d\u043b",
        "voice": "\u0432\u043e\u0439\u0441",
        "web": "\u0432\u0435\u0431",
        "webview": "\u0432\u0435\u0431 \u0432\u044c\u044e",
        "whisper": "\u0432\u0438\u0441\u043f\u0435\u0440",
        "windows": "\u0432\u0438\u043d\u0434\u043e\u0443\u0441",
    }
    _LETTER_NAMES = {
        "a": "\u044d\u0439",
        "b": "\u0431\u0438",
        "c": "\u0441\u0438",
        "d": "\u0434\u0438",
        "e": "\u0438",
        "f": "\u044d\u0444",
        "g": "\u0434\u0436\u0438",
        "h": "\u044d\u0439\u0447",
        "i": "\u0430\u0439",
        "j": "\u0434\u0436\u0435\u0439",
        "k": "\u043a\u0435\u0439",
        "l": "\u044d\u043b",
        "m": "\u044d\u043c",
        "n": "\u044d\u043d",
        "o": "\u043e\u0443",
        "p": "\u043f\u0438",
        "q": "\u043a\u044c\u044e",
        "r": "\u0430\u0440",
        "s": "\u044d\u0441",
        "t": "\u0442\u0438",
        "u": "\u044e",
        "v": "\u0432\u0438",
        "w": "\u0434\u0430\u0431\u043b \u044e",
        "x": "\u044d\u043a\u0441",
        "y": "\u0443\u0430\u0439",
        "z": "\u0437\u0435\u0434",
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._models = {}

    def preload(self, languages: Optional[tuple[str, ...]] = None) -> None:
        with self._lock:
            self._ensure_model()

    def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        with self._lock:
            import numpy as np
            import sounddevice as sd

            for chunk in self._speech_chunks(self._prepare_tts_text(text)):
                model, speaker, sample_rate = self._ensure_model()
                audio = model.apply_tts(
                    text=chunk[:1200],
                    speaker=speaker,
                    sample_rate=sample_rate,
                )
                if hasattr(audio, "detach"):
                    audio = audio.detach().cpu().numpy()
                audio = np.asarray(audio, dtype=np.float32)
                try:
                    sd.play(audio, sample_rate)
                    sd.wait()
                finally:
                    try:
                        sd.stop()
                    except Exception:
                        pass

    def stop(self) -> None:
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    def _ensure_model(self):
        if "ru" in self._models:
            return self._models["ru"]
        cfg = self._CONFIG
        import torch
        model, _example = torch.hub.load(
            repo_or_dir="snakers4/silero-models",
            model="silero_tts",
            language=cfg["hub_language"],
            speaker=cfg["hub_speaker"],
            trust_repo=True,
        )
        loaded = (model, cfg["speaker"], cfg["sample_rate"])
        self._models["ru"] = loaded
        return loaded

    def _speech_chunks(self, text: str) -> list[tuple[str, str]]:
        tokens = re.findall(
            r"[A-Za-z]+(?:[-'][A-Za-z]+)*|[А-Яа-яЁё]+|[0-9]+|[^A-Za-zА-Яа-яЁё0-9]+",
            text[:2400],
        )
        chunks: list[tuple[str, str]] = []
        current_lang: Optional[str] = None
        current = ""
        pending = ""

        for token in tokens:
            token_lang = self._token_language(token)
            if not token_lang:
                if current:
                    current += token
                else:
                    pending += token
                continue
            if current_lang is None:
                current_lang = token_lang
                current = (pending + token).strip()
                pending = ""
                continue
            if token_lang == current_lang:
                current += pending + token
                pending = ""
                continue
            self._append_tts_chunk(chunks, current_lang, current + pending)
            current_lang = token_lang
            current = token.strip()
            pending = ""

        if current_lang is None:
            self._append_tts_chunk(chunks, "ru", text[:1200])
        else:
            self._append_tts_chunk(chunks, current_lang, current + pending)
        return chunks

    @staticmethod
    def _token_language(token: str) -> str:
        latin = len(re.findall(r"[A-Za-z]", token))
        cyrillic = len(re.findall(r"[А-Яа-яЁё]", token))
        if latin > cyrillic:
            return "en"
        if cyrillic:
            return "ru"
        return ""

    @staticmethod
    def _append_tts_chunk(chunks: list[tuple[str, str]], language: str, text: str) -> None:
        clean = re.sub(r"\s+", " ", text).strip()
        if not clean:
            return
        while len(clean) > 1200:
            split_at = clean.rfind(" ", 0, 1100)
            if split_at < 200:
                split_at = 1100
            chunks.append((language, clean[:split_at].strip()))
            clean = clean[split_at:].strip()
        if clean:
            chunks.append((language, clean))

    def _speech_chunks(self, text: str) -> list[str]:
        chunks: list[str] = []
        self._append_tts_chunk(chunks, text[:2400])
        return chunks

    def _prepare_tts_text(self, text: str) -> str:
        text = re.sub(r"\[HostAgent\]\s*voice:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(
            r"[A-Za-z]+(?:[-'][A-Za-z]+)*",
            lambda match: self._latin_to_cyrillic(match.group(0)),
            text,
        )
        return re.sub(r"\s+", " ", text).strip()

    def _latin_to_cyrillic(self, word: str) -> str:
        clean = re.sub(r"[^A-Za-z]", "", word)
        if not clean:
            return word
        lower = clean.lower()
        if lower in self._WORD_OVERRIDES:
            return self._WORD_OVERRIDES[lower]
        if clean.isupper() or (len(clean) <= 4 and sum(ch.isupper() for ch in clean) >= 2):
            return " ".join(self._LETTER_NAMES.get(ch.lower(), ch) for ch in clean)
        return self._transliterate_word(lower)

    def _transliterate_word(self, word: str) -> str:
        rules = (
            ("tion", "\u0448\u0435\u043d"),
            ("sion", "\u0436\u0435\u043d"),
            ("ture", "\u0447\u0435\u0440"),
            ("ough", "\u043e\u0443"),
            ("eigh", "\u044d\u0439"),
            ("augh", "\u043e\u0444"),
            ("ph", "\u0444"),
            ("sh", "\u0448"),
            ("ch", "\u0447"),
            ("th", "\u0441"),
            ("ck", "\u043a"),
            ("qu", "\u043a\u0432"),
            ("ng", "\u043d\u0433"),
            ("ee", "\u0438"),
            ("ea", "\u0438"),
            ("oo", "\u0443"),
            ("ou", "\u0430\u0443"),
            ("ow", "\u0430\u0443"),
            ("ai", "\u044d\u0439"),
            ("ay", "\u044d\u0439"),
            ("ei", "\u0438"),
            ("ie", "\u0438"),
        )
        letters = {
            "a": "\u0430",
            "b": "\u0431",
            "c": "\u043a",
            "d": "\u0434",
            "e": "\u0435",
            "f": "\u0444",
            "g": "\u0433",
            "h": "\u0445",
            "i": "\u0438",
            "j": "\u0434\u0436",
            "k": "\u043a",
            "l": "\u043b",
            "m": "\u043c",
            "n": "\u043d",
            "o": "\u043e",
            "p": "\u043f",
            "q": "\u043a",
            "r": "\u0440",
            "s": "\u0441",
            "t": "\u0442",
            "u": "\u0430",
            "v": "\u0432",
            "w": "\u0432",
            "x": "\u043a\u0441",
            "y": "\u0438",
            "z": "\u0437",
        }
        out = []
        i = 0
        while i < len(word):
            matched = False
            for src, dst in rules:
                if word.startswith(src, i):
                    out.append(dst)
                    i += len(src)
                    matched = True
                    break
            if matched:
                continue
            ch = word[i]
            if ch == "c" and i + 1 < len(word) and word[i + 1] in "eiy":
                out.append("\u0441")
            elif ch == "g" and i + 1 < len(word) and word[i + 1] in "eiy":
                out.append("\u0434\u0436")
            elif ch == "e" and i == len(word) - 1 and len(word) > 2:
                pass
            else:
                out.append(letters.get(ch, ch))
            i += 1
        return "".join(out)

    @staticmethod
    def _append_tts_chunk(chunks: list[str], text: str) -> None:
        clean = re.sub(r"\s+", " ", text).strip()
        if not clean:
            return
        while len(clean) > 1200:
            split_at = clean.rfind(" ", 0, 1100)
            if split_at < 200:
                split_at = 1100
            chunks.append(clean[:split_at].strip())
            clean = clean[split_at:].strip()
        if clean:
            chunks.append(clean)


def preload_tts_model() -> None:
    SileroTTS().preload()


class VoiceService:
    def __init__(self, chat_url: str = "", overlay: bool = True,
                 wake_enabled: bool = False, port: int = VOICE_PORT) -> None:
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state = "idle"
        self._detail = ""
        self._chat_url = (chat_url or os.environ.get("COMPASS_CHAT_URL") or "").rstrip("/")
        self._ui_theme = self._normalize_theme(os.environ.get("COMPASS_UI_THEME") or "dark")
        self._record_stop = threading.Event()
        self._shutdown = threading.Event()
        self._wake_enabled = threading.Event()
        self._manual_pending = threading.Event()
        self._mic_lock = threading.Lock()
        self._session_lock = threading.RLock()
        self._session_active = False
        self._session_closed = False
        self._session_conv_id = None
        self._session_text = ""
        self._session_reply = ""
        self._session_status = ""
        self._session_last_done = 0.0
        self._current_stage = "idle"
        if wake_enabled:
            self._wake_enabled.set()
        self._wake_thread_started = False
        self._tts = SileroTTS()
        self._panel = VoiceSessionPanel(port) if overlay else None

    def start(self) -> None:
        if self._wake_enabled.is_set():
            self.enable_wake(True)
        else:
            self._set_state("idle")

    def enable_wake(self, enabled: bool) -> None:
        if enabled:
            self._wake_enabled.set()
            if not self._wake_thread_started:
                self._wake_thread_started = True
                threading.Thread(target=self._wake_loop, daemon=True, name="voice-wake-loop").start()
            if self.snapshot()["state"] == "idle":
                self._set_state("wake_listening")
        else:
            self._wake_enabled.clear()
            if self.snapshot()["state"] == "wake_listening":
                self._set_state("idle")

    def set_chat_url(self, url: str) -> None:
        if url:
            self._chat_url = url.rstrip("/")

    @staticmethod
    def _normalize_theme(theme: str) -> str:
        return "light" if theme == "light" else "dark"

    def set_config(self, chat_url: str = "", ui_theme: str = "") -> None:
        self.set_chat_url(chat_url)
        if ui_theme:
            self._ui_theme = self._normalize_theme(ui_theme)
            self._panel_update()

    def open_voice_chat(self) -> bool:
        if not self._chat_url or not self._session_conv_id:
            return False
        try:
            import urllib.request
            data = json.dumps(
                {"conversation_id": self._session_conv_id},
                ensure_ascii=False,
            ).encode("utf-8")
            req = urllib.request.Request(
                self._chat_url.rstrip("/") + "/api/open-voice-chat",
                data=data,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2.0):
                return True
        except Exception as e:
            BROKER.emit("open_chat_error", {"error": str(e)})
            return False

    def _post_chat_server(self, path: str, payload: dict,
                          timeout: float = 2.0) -> Optional[dict]:
        if not self._chat_url:
            return None
        try:
            import urllib.request
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                self._chat_url.rstrip("/") + path,
                data=data,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
        except Exception as e:
            BROKER.emit("chat_server_error", {"path": path, "error": str(e)})
            return None

    def snapshot(self) -> dict:
        with self._state_lock:
            return {
                "api_version": VOICE_DAEMON_API_VERSION,
                "state": self._state,
                "detail": self._detail,
                "chat_url": self._chat_url,
                "ui_theme": self._ui_theme,
                "wake_enabled": self._wake_enabled.is_set(),
                "conversation_id": self._session_conv_id,
                "session_active": self._session_active and not self._session_closed,
                "text": self._session_text,
                "reply": self._session_reply,
                "status_text": self._session_status,
            }

    def start_microphone_chat(self, conv_id=None, chat_url: str = "",
                              ui_theme: str = "", manual: bool = True) -> bool:
        if chat_url:
            self.set_chat_url(chat_url)
        if ui_theme:
            self._ui_theme = self._normalize_theme(ui_theme)
        return self._start_session(conv_id=conv_id, manual=manual)

    def stop_recording(self) -> None:
        self._record_stop.set()

    def stop_session_work(self) -> None:
        stage = self.snapshot().get("state")
        self._record_stop.set()
        if self._session_conv_id:
            self._post_chat_server("/api/cancel", {"conversation_id": self._session_conv_id})
        if stage in ("recording", "transcribing", "thinking", "speaking"):
            self._tts.stop()
        with self._session_lock:
            if self._session_active and not self._session_closed:
                self._session_status = "Остановлено"
        if stage in ("recording", "listening", "transcribing", "thinking", "speaking"):
            self._set_state("listening", status_text=self._session_status)
        else:
            self._panel_update(status_text=self._session_status)

    def close_session(self) -> None:
        with self._session_lock:
            self._session_closed = True
            self._session_active = False
            self._session_status = ""
        if self.snapshot().get("state") in ("recording", "listening", "transcribing"):
            self._record_stop.set()
        self._panel_update(state="idle", session_active=False)

    def shutdown(self) -> None:
        self._shutdown.set()
        self._wake_enabled.clear()
        self._record_stop.set()
        try:
            self._tts.stop()
        except Exception:
            pass
        self.close_session()

    def _start_session(self, conv_id=None, manual: bool = False) -> bool:
        with self._session_lock:
            if self._session_active and not self._session_closed:
                return True
            self._session_active = True
            self._session_closed = False
            self._session_conv_id = conv_id
            self._session_text = ""
            self._session_reply = ""
            self._session_status = ""
            self._session_last_done = time.monotonic()
        if manual:
            self._manual_pending.set()
        if not self._lock.acquire(blocking=False):
            with self._session_lock:
                self._session_active = False
                self._session_closed = True
            if manual:
                self._manual_pending.clear()
            return False
        threading.Thread(
            target=self._session_worker,
            args=(manual,),
            daemon=True,
            name="voice-session",
        ).start()
        return True

    def accept_audio(self, data_url_or_b64: str, mime: str = "",
                     conv_id=None, chat_url: str = "", ui_theme: str = "") -> bool:
        if chat_url:
            self.set_chat_url(chat_url)
        if ui_theme:
            self._ui_theme = self._normalize_theme(ui_theme)
        if not self._lock.acquire(blocking=False):
            return False
        try:
            path = self._save_upload(data_url_or_b64, mime)
        except Exception as e:
            self._lock.release()
            self._set_state("error", str(e))
            return False
        threading.Thread(
            target=self._uploaded_audio_worker,
            args=(path, conv_id),
            daemon=True,
            name="voice-upload-chat",
        ).start()
        return True

    def _set_state(self, state: str, detail: str = "", **extra) -> None:
        with self._state_lock:
            self._state = state
            self._detail = detail
            self._current_stage = state
            snap = {"state": state, "detail": detail, **extra}
        panel_payload = self._panel_payload(state, detail, extra)
        if self._panel:
            self._panel.update(panel_payload)
        BROKER.emit("state", {**snap, **panel_payload})

    def _panel_payload(self, state: str, detail: str = "", extra: Optional[dict] = None) -> dict:
        extra = extra or {}
        with self._session_lock:
            return {
                "state": state,
                "detail": detail,
                "text": extra.get("text") or self._session_text,
                "reply": extra.get("reply") or self._session_reply,
                "status_text": extra.get("status_text") or self._session_status,
                "conversation_id": self._session_conv_id,
                "session_active": self._session_active and not self._session_closed,
                "ui_theme": self._ui_theme,
            }

    def _panel_update(self, **payload) -> None:
        if not self._panel:
            return
        state = payload.get("state") or self.snapshot().get("state") or "idle"
        self._panel.update(self._panel_payload(state, payload.get("detail", ""), payload))

    def _ready_state(self) -> str:
        return "wake_listening" if self._wake_enabled.is_set() else "idle"

    def _wake_loop(self) -> None:
        detector: Optional[WakeWordDetector] = None
        while not self._shutdown.is_set():
            if not self._wake_enabled.is_set():
                if self.snapshot()["state"] == "wake_listening":
                    self._set_state("idle")
                time.sleep(0.5)
                continue
            if self.snapshot()["state"] not in ("idle", "wake_listening"):
                time.sleep(0.25)
                continue
            if self._manual_pending.is_set():
                time.sleep(0.1)
                continue
            self._set_state("wake_listening")
            try:
                if detector is None:
                    detector = WakeWordDetector()
                if not self._mic_lock.acquire(timeout=0.25):
                    time.sleep(0.1)
                    continue
                try:
                    if self._manual_pending.is_set():
                        continue
                    detected = detector.listen_until_detected(
                        stop_event=self._shutdown,
                        enabled_event=self._wake_enabled,
                        manual_pending=self._manual_pending,
                        can_listen=lambda: self.snapshot()["state"] in ("idle", "wake_listening"),
                    )
                finally:
                    self._mic_lock.release()
                if detected and not self._manual_pending.is_set():
                    self.start_microphone_chat(manual=False)
                    time.sleep(0.5)
            except Exception as e:
                detector = None
                self._set_state("error", str(e))
                time.sleep(4.0)
                self._set_state(self._ready_state())

    def _session_worker(self, manual: bool) -> None:
        try:
            if manual:
                self._manual_pending.clear()
            _play_audio(ACTIVATION_AUDIO)
            self._set_state("activated", text="Активировано")
            time.sleep(0.35)
            while not self._shutdown.is_set():
                with self._session_lock:
                    active = self._session_active and not self._session_closed
                    last_done = self._session_last_done
                if not active:
                    break
                if time.monotonic() - last_done >= VOICE_SESSION_IDLE_TIMEOUT:
                    self.close_session()
                    break

                path = None
                try:
                    self._record_stop.clear()
                    with self._mic_lock:
                        with self._session_lock:
                            if not (self._session_active and not self._session_closed):
                                break
                            self._session_status = ""
                            self._session_reply = ""
                            self._session_text = ""
                        self._set_state("listening", text="Слушаю...")
                        path, has_speech = _record_until_silence(
                            self._record_stop,
                            initial_timeout_s=8.0,
                            on_recording_start=lambda: self._set_state("recording", text="Слушаю..."),
                        )
                        if has_speech:
                            _play_audio(DEACTIVATION_AUDIO)
                    if not has_speech or not path:
                        continue
                    self._handle_audio_path(path, self._session_conv_id)
                    with self._session_lock:
                        self._session_last_done = time.monotonic()
                except Exception as e:
                    self._record_stop.clear()
                    BROKER.emit("session_error", {"error": str(e)})
                    with self._session_lock:
                        if not (self._session_active and not self._session_closed):
                            break
                        self._session_status = str(e)
                    self._set_state("error", str(e))
                    time.sleep(1.0)
                    continue
                finally:
                    _safe_unlink(path)
        except Exception as e:
            self._set_state("error", str(e))
        finally:
            self._record_stop.clear()
            self._manual_pending.clear()
            with self._session_lock:
                self._session_active = False
                self._session_closed = True
                self._session_status = ""
            self._set_state(self._ready_state())
            self._panel_update(state="idle", session_active=False)
            self._lock.release()

    def _microphone_worker(self, conv_id, manual: bool) -> None:
        path = None
        try:
            with self._mic_lock:
                if manual:
                    self._manual_pending.clear()
                _play_audio(ACTIVATION_AUDIO)
                self._set_state("listening", text="Слушаю...", manual=manual)
                path, has_speech = _record_until_silence(
                    self._record_stop,
                    on_recording_start=lambda: self._set_state("recording", text="Слушаю...", manual=manual),
                )
                if has_speech:
                    _play_audio(DEACTIVATION_AUDIO)
            if not has_speech or not path:
                self._set_state(self._ready_state())
                return
            self._handle_audio_path(path, conv_id)
        except Exception as e:
            self._set_state("error", str(e))
        finally:
            if manual:
                self._manual_pending.clear()
            _safe_unlink(path)
            self._record_stop.clear()
            self._set_state(self._ready_state())
            self._lock.release()

    def _uploaded_audio_worker(self, path: str, conv_id) -> None:
        try:
            self._handle_audio_path(path, conv_id)
        except Exception as e:
            self._set_state("error", str(e))
        finally:
            _safe_unlink(path)
            self._set_state(self._ready_state())
            self._lock.release()

    def _handle_audio_path(self, path: str, conv_id) -> None:
        self._set_state("transcribing")
        text = _transcribe(path)
        self._handle_transcript_text(text, conv_id)

    def _handle_transcript_text(self, text: str, conv_id) -> None:
        with self._session_lock:
            self._session_text = text
            self._session_reply = ""
            self._session_status = ""
        BROKER.emit("transcript", {"text": text, "conversation_id": self._session_conv_id})
        if self._record_stop.is_set():
            return
        if not text:
            return
        self._set_state("thinking", text=text)
        status_stop = threading.Event()
        status_thread = threading.Thread(
            target=self._stream_chat_status,
            args=(status_stop,),
            daemon=True,
            name="voice-chat-status",
        )
        status_thread.start()
        result = self._send_chat(text, conv_id)
        status_stop.set()
        if self._record_stop.is_set():
            return
        if result.get("cancelled"):
            with self._session_lock:
                self._session_status = "Остановлено"
            self._set_state("listening", status_text="Остановлено")
            return
        with self._session_lock:
            if result.get("conversation_id"):
                self._session_conv_id = result.get("conversation_id")
        reply = result.get("reply") or (result.get("response") or {}).get("voice") or ""
        display_reply = reply
        with self._session_lock:
            self._session_reply = display_reply
            self._session_status = ""
        BROKER.emit("chat_done", {
            "conversation_id": self._session_conv_id,
            "reply": reply,
            "display_reply": display_reply,
            "transcript": text,
        })
        if reply:
            self._set_state("speaking", reply=display_reply)
            try:
                self._tts.speak(reply)
            except Exception as e:
                BROKER.emit("tts_error", {"error": str(e)})

    def _stream_chat_status(self, stop_event: threading.Event) -> None:
        if not self._chat_url:
            return
        try:
            import urllib.request
            req = urllib.request.Request(self._chat_url.rstrip("/") + "/api/events")
            with urllib.request.urlopen(req, timeout=10) as resp:
                event = "message"
                while not stop_event.is_set():
                    raw = resp.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event = line.split(":", 1)[1].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    if event != "status":
                        continue
                    try:
                        data = json.loads(line.split(":", 1)[1].strip())
                    except Exception:
                        continue
                    text = (data.get("text") or "").strip()
                    if not text:
                        continue
                    with self._session_lock:
                        if not (self._session_active and not self._session_closed):
                            return
                        self._session_status = text
                    self._set_state("thinking", status_text=text)
        except Exception:
            return

    def _send_chat(self, text: str, conv_id) -> dict:
        conv_id = conv_id or self._ensure_session_conversation(text)
        payload = {"message": text, "conversation_id": conv_id, "source": "voice"}
        if self._chat_url:
            try:
                import urllib.request
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(
                    self._chat_url.rstrip("/") + "/api/chat",
                    data=data,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=300) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                BROKER.emit("chat_fallback", {"error": str(e)})
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import web_server
        return web_server.process_chat_request(payload, source="voice")

    def _ensure_session_conversation(self, title: str):
        with self._session_lock:
            if self._session_conv_id:
                return self._session_conv_id
        clean_title = (title or "Голосовой запрос").strip()[:120]
        created = self._post_chat_server(
            "/api/conversations",
            {"title": clean_title},
            timeout=5.0,
        )
        conv_id = (created or {}).get("id")
        if conv_id is not None:
            with self._session_lock:
                self._session_conv_id = conv_id
            self._panel_update(conversation_id=conv_id)
        return conv_id

    def _save_upload(self, data_url_or_b64: str, mime: str = "") -> str:
        data = data_url_or_b64 or ""
        if data.startswith("data:"):
            head, _, b64 = data.partition(",")
            m = re.match(r"data:([^;]+);base64", head)
            if m:
                mime = m.group(1)
        else:
            b64 = data
        raw = base64.b64decode(b64)
        suffix = ".wav"
        if "ogg" in (mime or ""):
            suffix = ".ogg"
        elif "mpeg" in (mime or "") or "mp3" in (mime or ""):
            suffix = ".mp3"
        elif "flac" in (mime or ""):
            suffix = ".flac"
        path = _temp_audio_path(suffix)
        with open(path, "wb") as f:
            f.write(raw)
        return path


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: ANN001
        return

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send_json(self, code: int, obj) -> None:
        data = _json_dumps(obj)
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> Optional[dict]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            return json.loads(body)
        except Exception:
            return None

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):  # noqa: N802
        if SERVICE is None:
            self._send_json(503, {"ok": False, "error": "service not ready"})
            return
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send_json(200, {"ok": True, **SERVICE.snapshot()})
            return
        if path == "/state":
            self._send_json(200, SERVICE.snapshot())
            return
        if path.startswith("/animation/"):
            self._send_animation(path[len("/animation/"):])
            return
        if path == "/events":
            self._sse_stream()
            return
        if path == "/open-chat":
            ok = SERVICE.open_voice_chat()
            self._send_json(200 if ok else 503, {"ok": ok})
            return
        self.send_error(404)

    def do_POST(self):  # noqa: N802
        if SERVICE is None:
            self._send_json(503, {"ok": False, "error": "service not ready"})
            return
        path = self.path.split("?", 1)[0]
        req = self._read_json() or {}
        if path == "/config":
            SERVICE.set_config(
                chat_url=req.get("chat_url") or "",
                ui_theme=req.get("ui_theme") or "",
            )
            self._send_json(200, {"ok": True, **SERVICE.snapshot()})
            return
        if path == "/open-chat":
            ok = SERVICE.open_voice_chat()
            self._send_json(200 if ok else 503, {"ok": ok})
            return
        if path == "/listen/start":
            ok = SERVICE.start_microphone_chat(
                conv_id=req.get("conversation_id"),
                chat_url=req.get("chat_url") or "",
                ui_theme=req.get("ui_theme") or "",
                manual=True,
            )
            self._send_json(200 if ok else 409, {"ok": ok, **SERVICE.snapshot()})
            return
        if path == "/listen/stop":
            SERVICE.stop_recording()
            self._send_json(200, {"ok": True, **SERVICE.snapshot()})
            return
        if path == "/session/stop":
            SERVICE.stop_session_work()
            self._send_json(200, {"ok": True, **SERVICE.snapshot()})
            return
        if path == "/session/close":
            SERVICE.close_session()
            self._send_json(200, {"ok": True, **SERVICE.snapshot()})
            return
        if path == "/wake/start":
            SERVICE.enable_wake(True)
            self._send_json(200, {"ok": True, **SERVICE.snapshot()})
            return
        if path == "/wake/stop":
            SERVICE.enable_wake(False)
            self._send_json(200, {"ok": True, **SERVICE.snapshot()})
            return
        if path == "/shutdown":
            SERVICE.shutdown()
            self._send_json(200, {"ok": True})
            threading.Thread(target=_exit_process_soon, daemon=True,
                             name="voice-daemon-exit").start()
            return
        if path == "/audio/upload":
            ok = SERVICE.accept_audio(
                req.get("data") or "",
                mime=req.get("mime") or "",
                conv_id=req.get("conversation_id"),
                chat_url=req.get("chat_url") or "",
                ui_theme=req.get("ui_theme") or "",
            )
            self._send_json(200 if ok else 409, {"ok": ok, **SERVICE.snapshot()})
            return
        self.send_error(404)

    def _send_animation(self, filename: str) -> None:
        safe = os.path.basename(filename)
        allowed = {
            "compass-wake-pulse-dark.svg",
            "compass-wake-pulse-light.svg",
            "compass-listen-pulse-dark.svg",
            "compass-listen-pulse-light.svg",
            "compass-think-signal-dark.svg",
            "compass-think-signal-light.svg",
            "compass-speak-signal-dark.svg",
            "compass-speak-signal-light.svg",
        }
        if safe not in allowed:
            self.send_error(404)
            return
        target = os.path.join(ROOT, "src", "animation", safe)
        try:
            with open(target, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def _sse_stream(self) -> None:
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        q = BROKER.subscribe()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            BROKER.emit("state", SERVICE.snapshot() if SERVICE else {"state": "error"})
            last_ping = time.time()
            while True:
                try:
                    payload = q.get(timeout=15)
                    obj = json.loads(payload)
                    evt = obj.get("event", "state")
                    data_str = json.dumps(obj.get("data", {}), ensure_ascii=False)
                    msg = f"event: {evt}\ndata: {data_str}\n\n".encode("utf-8")
                    self.wfile.write(msg)
                    self.wfile.flush()
                except queue.Empty:
                    if time.time() - last_ping > 10:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_ping = time.time()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            BROKER.unsubscribe(q)


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def run_daemon(port: int = VOICE_PORT, chat_url: str = "", overlay: bool = True,
               wake_enabled: bool = False) -> None:
    global SERVICE
    if not _port_available(port):
        print(f"[voice] daemon already running or port busy: {port}")
        return
    SERVICE = VoiceService(chat_url=chat_url, overlay=overlay,
                           wake_enabled=wake_enabled, port=port)
    SERVICE.start()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[voice] daemon listening on http://127.0.0.1:{port}")
    try:
        if overlay and SERVICE._panel is not None:
            threading.Thread(target=httpd.serve_forever, daemon=True,
                             name="voice-http").start()
            try:
                ran_overlay = SERVICE._panel.run()
            except Exception as e:
                ran_overlay = False
                print(f"[voice] overlay disabled: {e}", file=sys.stderr)
            if not ran_overlay:
                print("[voice] overlay disabled; daemon stays online", file=sys.stderr)
            while True:
                time.sleep(3600)
        else:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[voice] stopped.")
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass


if __name__ == "__main__":
    run_daemon()
