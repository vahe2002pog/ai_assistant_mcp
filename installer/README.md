# Компас Windows installer

This folder contains the online Windows x64 installer flow:

- `compass.spec` builds the app with PyInstaller in `onedir` mode.
- `compass.iss` builds the Inno Setup installer and adds the model selection page.
- `prepare_components.ps1` runs during installation and downloads runtime components.
- `build_installer.ps1` runs PyInstaller and then Inno Setup.

## What the installer downloads

The installer always prepares:

- portable Ollama from `https://github.com/ollama/ollama/releases/download/v0.30.0-rc31/ollama-windows-amd64.zip`;
- whisper.cpp from `https://github.com/ggml-org/whisper.cpp/releases/download/v1.8.5/whisper-bin-x64.zip`;
- Whisper `ggml-small.bin`;
- Silero TTS model through the app preload command.
- RAG embedding model `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.

During installation the user chooses one LLM option:

- `qwen3.5:2b` - 2.7GB;
- `qwen3.5:4b` - 3.4GB, default/recommended;
- `qwen3.5:9b` - 6.6GB;
- `Без локальной модели / API key` - skip `ollama pull`; the app starts with the built-in Ollama provider and no selected model, so the user can switch to an API provider in the UI.

Default runtime layout:

```text
utils/ollama/ollama.exe
%ProgramData%/Compass/ollama/models/
%ProgramData%/Compass/hf_cache/
utils/whisper/whisper-cli.exe
voice/models/ggml-small.bin
llm_config.json
```

## Build

Install Inno Setup 6 first, then run from the repository root:

```powershell
powershell.exe -ExecutionPolicy Bypass -File installer\build_installer.ps1
```

If `ISCC.exe` is not in `PATH`:

```powershell
powershell.exe -ExecutionPolicy Bypass -File installer\build_installer.ps1 -InnoCompilerPath "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```

The output is:

```text
dist/installer/KompasSetup.exe
```

Debug build with a visible console:

```powershell
powershell.exe -ExecutionPolicy Bypass -File installer\build_installer.ps1 -DebugConsole -InnoCompilerPath "C:\Users\vahep\AppData\Local\Programs\Inno Setup 6\ISCC.exe"
```

The debug installer output is:

```text
dist/installer/KompasSetupDebug.exe
```

## Manual component preparation

Use this for testing without running the full installer:

```powershell
powershell.exe -ExecutionPolicy Bypass -File installer\prepare_components.ps1 -OllamaModel qwen3.5:4b
```

Skip local LLM download and leave the app in provider setup mode:

```powershell
powershell.exe -ExecutionPolicy Bypass -File installer\prepare_components.ps1 -SkipOllamaModel
```

Allowed `-OllamaModel` values are `qwen3.5:2b`, `qwen3.5:4b`, and `qwen3.5:9b`.
## Debug-сборка

Установщик собирается тем же скриптом и тем же `compass.iss`.

Обычная сборка:

```powershell
powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1
```

Debug-сборка с консолью у `Compass.exe`:

```powershell
powershell -ExecutionPolicy Bypass -File installer\build_installer.ps1 -Debug
```

В обоих случаях создается один и тот же установщик `KompasSetup.exe`; флаг `-Debug` влияет только на режим PyInstaller-приложения внутри него.
