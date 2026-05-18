import time
import queue
import pathlib
import numpy as np
import tensorflow as tf
import sounddevice as sd

# =========================
# Настройки
# =========================

MODEL_DIR = pathlib.Path("model")

SAMPLE_RATE = 16000
WINDOW_SECONDS = 1
WINDOW_SAMPLES = SAMPLE_RATE * WINDOW_SECONDS

STEP_MS = 250
BLOCK_SAMPLES = int(SAMPLE_RATE * STEP_MS / 1000)

THRESHOLD = 0.6
COOLDOWN_SEC = 1.5

COMMANDS = np.array(["negative", "positive"])
POSITIVE_INDEX = 1

audio_queue = queue.Queue()


# =========================
# Spectrogram как при обучении
# =========================

def get_spectrogram(waveform):
    waveform = waveform[:WINDOW_SAMPLES]

    zero_padding = tf.zeros(
        [WINDOW_SAMPLES] - tf.shape(waveform),
        dtype=tf.float32
    )

    waveform = tf.cast(waveform, tf.float32)
    equal_length = tf.concat([waveform, zero_padding], axis=0)

    spectrogram = tf.signal.stft(
        equal_length,
        frame_length=255,
        frame_step=128
    )

    spectrogram = tf.abs(spectrogram)
    spectrogram = spectrogram[..., tf.newaxis]

    return spectrogram


def waveform_to_model_input(waveform_np):
    waveform = tf.convert_to_tensor(waveform_np, dtype=tf.float32)
    spectrogram = get_spectrogram(waveform)
    spectrogram = spectrogram.numpy().astype(np.float32)

    # [124, 129, 1] -> [1, 124, 129, 1]
    return np.expand_dims(spectrogram, axis=0)


# =========================
# Загрузка модели
# =========================

def load_model_from_folder():
    keras_models = list(MODEL_DIR.glob("*.keras"))
    tflite_models = list(MODEL_DIR.glob("*.tflite"))

    if keras_models:
        model_path = keras_models[0]
        print(f"Loaded Keras model: {model_path}")
        model = tf.keras.models.load_model(model_path)
        return "keras", model

    if tflite_models:
        model_path = tflite_models[0]
        print(f"Loaded TFLite model: {model_path}")

        interpreter = tf.lite.Interpreter(model_path=str(model_path))
        interpreter.allocate_tensors()

        return "tflite", interpreter

    raise FileNotFoundError("В папке model не найдено .keras или .tflite модели")


def softmax(x):
    x = x - np.max(x)
    e = np.exp(x)
    return e / np.sum(e)


MODEL_TYPE, MODEL = load_model_from_folder()


# =========================
# Predict
# =========================

def predict_window(waveform_np):
    x = waveform_to_model_input(waveform_np)

    if MODEL_TYPE == "keras":
        logits = MODEL(x, training=False).numpy()[0]
        probs = softmax(logits)
        return probs

    if MODEL_TYPE == "tflite":
        interpreter = MODEL

        input_details = interpreter.get_input_details()
        output_details = interpreter.get_output_details()

        input_index = input_details[0]["index"]
        output_index = output_details[0]["index"]

        input_dtype = input_details[0]["dtype"]

        if input_dtype != np.float32:
            scale, zero_point = input_details[0]["quantization"]
            x = x / scale + zero_point
            x = x.astype(input_dtype)
        else:
            x = x.astype(np.float32)

        interpreter.set_tensor(input_index, x)
        interpreter.invoke()

        logits = interpreter.get_tensor(output_index)[0]

        output_dtype = output_details[0]["dtype"]
        if output_dtype != np.float32:
            scale, zero_point = output_details[0]["quantization"]
            logits = scale * (logits.astype(np.float32) - zero_point)

        probs = softmax(logits)
        return probs


# =========================
# Микрофон
# =========================

def audio_callback(indata, frames, time_info, status):
    if status:
        print(status)

    audio = indata[:, 0].copy()
    audio_queue.put(audio)


def main():
    print("Start microphone wake-word detection")
    print("Press Ctrl+C to stop")
    print(f"Threshold: {THRESHOLD}")

    audio_buffer = np.zeros(WINDOW_SAMPLES, dtype=np.float32)
    filled_samples = 0
    last_detection_time = 0

    with sd.InputStream(
        channels=1,
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SAMPLES,
        dtype="float32",
        callback=audio_callback
    ):
        while True:
            chunk = audio_queue.get()

            chunk_len = len(chunk)

            audio_buffer = np.roll(audio_buffer, -chunk_len)
            audio_buffer[-chunk_len:] = chunk

            filled_samples = min(WINDOW_SAMPLES, filled_samples + chunk_len)

            if filled_samples < WINDOW_SAMPLES:
                continue

            probs = predict_window(audio_buffer)

            negative_prob = probs[0]
            positive_prob = probs[1]

            print(
                f"\rpositive={positive_prob:.3f} negative={negative_prob:.3f}",
                end="",
                flush=True
            )

            now = time.time()

            if positive_prob >= THRESHOLD:
                if now - last_detection_time >= COOLDOWN_SEC:
                    print(f"\nWAKE WORD DETECTED! confidence={positive_prob:.3f}")
                    last_detection_time = now


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped")