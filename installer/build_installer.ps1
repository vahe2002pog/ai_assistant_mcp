param(
    [string]$InnoCompilerPath = "",
    [switch]$Debug,
    [switch]$SkipPyInstaller
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$SpecPath = Join-Path $ScriptDir "compass.spec"
$IssPath = Join-Path $ScriptDir "compass.iss"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$PyDistRoot = Join-Path $ProjectRoot "dist\pyinstaller\$Stamp"
$PyWorkRoot = Join-Path $ProjectRoot "build\pyinstaller\$Stamp"
$PyAppDir = Join-Path $PyDistRoot "Compass"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Resolve-InnoCompiler {
    param([string]$ExplicitPath)

    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        $candidates += $ExplicitPath
    }
    if (-not [string]::IsNullOrWhiteSpace($env:INNO_COMPILER_PATH)) {
        $candidates += $env:INNO_COMPILER_PATH
    }
    $candidates += @(
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )

    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if ((Split-Path -Leaf $candidate) -ne "ISCC.exe") {
            $candidate = Join-Path $candidate "ISCC.exe"
        }
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    throw "Inno Setup 6 compiler (ISCC.exe) not found. Install Inno Setup or pass -InnoCompilerPath."
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$ErrorMessage
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $ErrorMessage
    }
}

Push-Location $ProjectRoot
try {
    $InnoCompiler = Resolve-InnoCompiler $InnoCompilerPath

    if ($Debug) {
        Write-Step "Debug build enabled: Compass.exe will open a console"
        $env:COMPASS_DEBUG_BUILD = "1"
    } else {
        $env:COMPASS_DEBUG_BUILD = "0"
    }

    if (-not $SkipPyInstaller) {
        Write-Step "Ensuring PyInstaller is installed"
        Invoke-Checked "python" @("-m", "pip", "install", "pyinstaller") "PyInstaller install failed"

        Write-Step "Building PyInstaller onedir package"
        New-Item -ItemType Directory -Force -Path $PyDistRoot, $PyWorkRoot | Out-Null
        Invoke-Checked "python" @(
            "-m", "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath", $PyDistRoot,
            "--workpath", $PyWorkRoot,
            $SpecPath
        ) "PyInstaller build failed"
    } else {
        Write-Step "Skipping PyInstaller build"
        $latest = Get-ChildItem -LiteralPath (Join-Path $ProjectRoot "dist\pyinstaller") -Directory -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if (-not $latest) {
            throw "No existing PyInstaller build found for -SkipPyInstaller."
        }
        $PyAppDir = Join-Path $latest.FullName "Compass"
    }

    if (-not (Test-Path (Join-Path $PyAppDir "Compass.exe"))) {
        throw "Compass.exe was not found in PyInstaller output: $PyAppDir"
    }

    Write-Step "Building Inno Setup installer"
    $env:COMPASS_BUILD_SOURCE = $PyAppDir
    Invoke-Checked $InnoCompiler @($IssPath) "Inno Setup build failed"

    Write-Step "Installer build completed"
    Write-Host "Build source: $PyAppDir"
    Write-Host "Mode: $(if ($Debug) { 'Debug console' } else { 'Release windowed' })"
    Write-Host "Output: $(Join-Path $ScriptDir 'Output')"
} finally {
    Pop-Location
}
