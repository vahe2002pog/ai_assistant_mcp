param(
    [string]$InstallDir = "",
    [string]$OllamaModel = "qwen3.5:4b",
    [switch]$SkipOllamaModel
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $InstallDir = Split-Path -Parent $PSScriptRoot
}
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)

$OllamaZipUrl = "https://github.com/ollama/ollama/releases/download/v0.30.0-rc31/ollama-windows-amd64.zip"
$WhisperZipUrl = "https://github.com/ggml-org/whisper.cpp/releases/download/v1.8.5/whisper-bin-x64.zip"
$WhisperModelUrl = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin"

$TempDir = Join-Path $env:TEMP "compass-installer-components"
$OllamaDir = Join-Path $InstallDir "utils\ollama"
$WhisperDir = Join-Path $InstallDir "utils\whisper"
$WhisperModelDir = Join-Path $InstallDir "voice\models"
$WhisperModelPath = Join-Path $WhisperModelDir "ggml-small.bin"
$CompassDataDir = Join-Path $env:ProgramData "Compass"
$OllamaModelsDir = Join-Path $CompassDataDir "ollama\models"
$OllamaExe = Join-Path $OllamaDir "ollama.exe"
$ConfigPath = Join-Path $InstallDir "llm_config.json"
$LogPath = Join-Path $InstallDir "installer\install-components.log"
$AllowedOllamaModels = @("qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b")
$TranscriptStarted = $false

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Invoke-Download {
    param([string]$Url, [string]$OutFile)
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null
    if (Test-Path $OutFile) {
        Remove-Item -LiteralPath $OutFile -Force
    }
    $curl = Join-Path $env:SystemRoot "System32\curl.exe"
    if (Test-Path $curl) {
        & $curl -L --fail --retry 3 --retry-delay 2 -o $OutFile $Url
        if ($LASTEXITCODE -ne 0) {
            throw "Download failed: $Url"
        }
    } else {
        Invoke-WebRequest -Uri $Url -OutFile $OutFile
    }
}

function Test-FileMinSize {
    param([string]$Path, [int64]$MinBytes)
    if (-not (Test-Path $Path)) {
        return $false
    }
    $item = Get-Item -LiteralPath $Path
    return $item.Length -ge $MinBytes
}

function Test-ExecutableRuns {
    param(
        [string]$Path,
        [string[]]$Arguments = @("--version")
    )
    if (-not (Test-Path $Path)) {
        return $false
    }
    try {
        $stamp = [System.Guid]::NewGuid().ToString("N")
        $stdout = Join-Path $env:TEMP "compass-check-$stamp.out"
        $stderr = Join-Path $env:TEMP "compass-check-$stamp.err"
        $process = Start-Process -FilePath $Path -ArgumentList $Arguments -NoNewWindow -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
        return $process.ExitCode -eq 0
    } catch {
        return $false
    }
}

function Expand-ZipClean {
    param([string]$ZipPath, [string]$Destination)
    if (Test-Path $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $Destination -Force
}

function Normalize-WhisperLayout {
    $targetExe = Join-Path $WhisperDir "whisper-cli.exe"
    if (Test-Path $targetExe) {
        return
    }

    $releaseDir = Join-Path $WhisperDir "Release"
    $releaseExe = Join-Path $releaseDir "whisper-cli.exe"
    if (-not (Test-Path $releaseExe)) {
        return
    }

    Get-ChildItem -LiteralPath $releaseDir -Force | ForEach-Object {
        Move-Item -LiteralPath $_.FullName -Destination $WhisperDir -Force
    }
    Remove-Item -LiteralPath $releaseDir -Recurse -Force -ErrorAction SilentlyContinue
}

function Test-OllamaInstall {
    if (-not (Test-FileMinSize $OllamaExe 1048576)) {
        return $false
    }
    return Test-ExecutableRuns $OllamaExe @("--version")
}

function Test-WhisperInstall {
    Normalize-WhisperLayout
    $whisperExe = Join-Path $WhisperDir "whisper-cli.exe"
    if (-not (Test-FileMinSize $whisperExe 1048576)) {
        return $false
    }
    return Test-ExecutableRuns $whisperExe @("--help")
}

function Wait-Ollama {
    param([string]$Url = "http://127.0.0.1:11435/api/tags", [int]$TimeoutSeconds = 45)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod -Uri $Url -TimeoutSec 2 | Out-Null
            return $true
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    return $false
}

function Test-OllamaModelInstalled {
    param([string]$Model)
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:11435/api/tags" -TimeoutSec 5
        foreach ($item in @($response.models)) {
            if ($item.name -eq $Model) {
                return $true
            }
        }
    } catch {
        return $false
    }
    return $false
}

function Grant-UsersModify {
    param([string]$Path)
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
    & icacls $Path /grant "*S-1-5-32-545:(OI)(CI)M" /T /C | Out-Null
}

function Write-LlmConfig {
    param(
        [string]$Model,
        [switch]$SkipModel
    )

    $selectedModel = ""
    if (-not $SkipModel) {
        $selectedModel = $Model
    }

    $config = [ordered]@{
        provider = "ollama"
        model = $selectedModel
        api_key = "ollama"
        base_url = "http://127.0.0.1:11435/v1"
        vision_provider = ""
        vision_model = ""
        vision_api_key = ""
        vision_base_url = ""
        provider_settings = [ordered]@{
            ollama = [ordered]@{
                api_key = "ollama"
                base_url = "http://127.0.0.1:11435/v1"
                model = $selectedModel
            }
        }
    }

    $json = $config | ConvertTo-Json -Depth 8
    Set-Content -LiteralPath $ConfigPath -Value $json -Encoding UTF8
}

function Invoke-SileroPreload {
    $exe = Join-Path $InstallDir "Compass.exe"
    if (-not (Test-Path $exe)) {
        Write-Step "Compass.exe not found, skipping Silero preload"
        return
    }

    Write-Step "Preloading Silero TTS model"
    $process = Start-Process -FilePath $exe -ArgumentList @("--voice-preload-tts") -WorkingDirectory $InstallDir -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Silero TTS preload failed with exit code $($process.ExitCode)"
    }
}

function Invoke-RagEmbeddingPreload {
    $exe = Join-Path $InstallDir "Compass.exe"
    if (-not (Test-Path $exe)) {
        Write-Step "Compass.exe not found, skipping RAG embedding preload"
        return
    }

    Write-Step "Preloading RAG embedding model"
    $env:COMPASS_HF_HOME = Join-Path $CompassDataDir "hf_cache"
    New-Item -ItemType Directory -Force -Path $env:COMPASS_HF_HOME | Out-Null
    $process = Start-Process -FilePath $exe -ArgumentList @("--rag-preload-embeddings") -WorkingDirectory $InstallDir -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "RAG embedding preload failed with exit code $($process.ExitCode)"
    }
}

$ollamaProcess = $null
try {
    New-Item -ItemType Directory -Force -Path $TempDir, $OllamaDir, $WhisperDir, $WhisperModelDir, (Split-Path -Parent $LogPath) | Out-Null
    Start-Transcript -LiteralPath $LogPath -Append | Out-Null
    $TranscriptStarted = $true

    if (-not $SkipOllamaModel -and $AllowedOllamaModels -notcontains $OllamaModel) {
        throw "Unsupported Ollama model '$OllamaModel'. Allowed values: $($AllowedOllamaModels -join ', ')"
    }

    Grant-UsersModify $CompassDataDir
    Grant-UsersModify $OllamaModelsDir

    Write-Step "Checking portable Ollama"
    if (Test-OllamaInstall) {
        Write-Step "Portable Ollama already installed and valid, skipping download"
    } else {
        Write-Step "Installing portable Ollama"
        $ollamaZip = Join-Path $TempDir "ollama-windows-amd64.zip"
        Invoke-Download $OllamaZipUrl $ollamaZip
        Expand-ZipClean $ollamaZip $OllamaDir
        if (-not (Test-OllamaInstall)) {
            throw "Ollama executable is missing or invalid after extraction: $OllamaExe"
        }
    }

    Write-Step "Checking whisper.cpp"
    if (Test-WhisperInstall) {
        Write-Step "whisper.cpp already installed and valid, skipping download"
    } else {
        Write-Step "Installing whisper.cpp"
        $whisperZip = Join-Path $TempDir "whisper-bin-x64.zip"
        Invoke-Download $WhisperZipUrl $whisperZip
        Expand-ZipClean $whisperZip $WhisperDir
        Normalize-WhisperLayout
        if (-not (Test-WhisperInstall)) {
            throw "whisper-cli.exe is missing or invalid after extraction in $WhisperDir"
        }
    }

    if (Test-FileMinSize $WhisperModelPath 104857600) {
        Write-Step "Whisper model already installed and valid, skipping download"
    } else {
        Write-Step "Downloading Whisper model"
        Invoke-Download $WhisperModelUrl $WhisperModelPath
        if (-not (Test-FileMinSize $WhisperModelPath 104857600)) {
            throw "Whisper model is missing or too small after download: $WhisperModelPath"
        }
    }

    if ($SkipOllamaModel) {
        Write-Step "Skipping local Ollama LLM model download"
    } else {
        Write-Step "Downloading Ollama model $OllamaModel"
        $env:OLLAMA_HOST = "127.0.0.1:11435"
        $env:OLLAMA_MODELS = $OllamaModelsDir
        $ollamaProcess = Start-Process -FilePath $OllamaExe -ArgumentList @("serve") -WorkingDirectory $OllamaDir -WindowStyle Hidden -PassThru
        if (-not (Wait-Ollama)) {
            throw "Portable Ollama did not start on 127.0.0.1:11435"
        }
        if (Test-OllamaModelInstalled $OllamaModel) {
            Write-Step "Ollama model $OllamaModel already installed, skipping download"
        } else {
            & $OllamaExe pull $OllamaModel
            if ($LASTEXITCODE -ne 0) {
                throw "Ollama model download failed: $OllamaModel"
            }
        }
    }

    Invoke-SileroPreload
    Invoke-RagEmbeddingPreload
    Write-LlmConfig -Model $OllamaModel -SkipModel:$SkipOllamaModel

    Write-Step "Components are ready"
    Write-Host "InstallDir: $InstallDir"
    Write-Host "Ollama: $OllamaExe"
    Write-Host "Whisper: $(Join-Path $WhisperDir 'whisper-cli.exe')"
    Write-Host "Whisper model: $WhisperModelPath"
    Write-Host "Log: $LogPath"
} finally {
    if ($ollamaProcess -and -not $ollamaProcess.HasExited) {
        Stop-Process -Id $ollamaProcess.Id -Force -ErrorAction SilentlyContinue
    }
    if ($TranscriptStarted) {
        Stop-Transcript | Out-Null
    }
}
