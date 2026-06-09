[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [ValidateSet("hf", "openai", "ocr")]
    [string]$Provider = "hf",
    [string]$PythonVersion = "3.11",
    [string]$VenvDir = ".venv",
    [switch]$SkipDoctor,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Output ("[bootstrap] {0}" -f $Message)
}

function Resolve-Root {
    param([string]$RawRoot)
    if ($RawRoot -and $RawRoot.Trim()) {
        return (Resolve-Path -LiteralPath $RawRoot).Path
    }
    return (Get-Location).Path
}

function Exec-OrThrow {
    param(
        [string]$Label,
        [string]$FilePath,
        [string[]]$CommandArgs
    )
    Write-Step $Label
    & $FilePath @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw ("Command failed ({0}), exit_code={1}" -f $Label, $LASTEXITCODE)
    }
}

function Resolve-PythonCommand {
    param(
        [string]$Root,
        [string]$PyVersion
    )
    $fallbacks = @(
        "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python311\python.exe",
        "C:\Program Files\Python311\python.exe",
        "C:\Program Files (x86)\Python311\python.exe"
    )
    try {
        & py -$PyVersion --version *> $null
        if ($LASTEXITCODE -eq 0) {
            return @{ Cmd = "py"; BaseArgs = @("-$PyVersion") }
        }
    } catch {}
    try {
        & python --version *> $null
        if ($LASTEXITCODE -eq 0) {
            return @{ Cmd = "python"; BaseArgs = @() }
        }
    } catch {}
    foreach ($candidate in $fallbacks) {
        if (-not (Test-Path -LiteralPath $candidate)) {
            continue
        }
        try {
            & $candidate --version *> $null
            if ($LASTEXITCODE -eq 0) {
                return @{ Cmd = $candidate; BaseArgs = @() }
            }
        } catch {}
    }
    throw "Python not found. Install Python $PyVersion and re-run."
}

$root = Resolve-Root -RawRoot $ProjectRoot
$venvPath = Join-Path $root $VenvDir
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$requirementsLock = Join-Path $root "requirements-lock.txt"
$requirementsTxt = Join-Path $root "requirements.txt"
$envExample = Join-Path $root ".env.example"
$envLocal = Join-Path $root ".env.local"
$envPlain = Join-Path $root ".env"
$normalizeScript = Join-Path $root "scripts\normalize-env.ps1"
$doctorScript = Join-Path $root "scripts\doctor.ps1"

Write-Step ("project_root={0}" -f $root)

if (-not (Test-Path -LiteralPath $requirementsLock) -and -not (Test-Path -LiteralPath $requirementsTxt)) {
    throw "Missing requirements-lock.txt and requirements.txt."
}

$pyCmd = Resolve-PythonCommand -Root $root -PyVersion $PythonVersion
Write-Step ("python_command={0} {1}" -f $pyCmd.Cmd, (($pyCmd.BaseArgs -join " ").Trim()))

if (-not (Test-Path -LiteralPath $venvPython)) {
    Exec-OrThrow -Label ("create venv ({0})" -f $VenvDir) -FilePath $pyCmd.Cmd -CommandArgs (@($pyCmd.BaseArgs) + @("-m", "venv", $venvPath))
} else {
    Write-Step ("venv already exists: {0}" -f $venvPath)
}

Exec-OrThrow -Label "upgrade pip in venv" -FilePath $venvPython -CommandArgs @("-m", "pip", "install", "--upgrade", "pip")

if (Test-Path -LiteralPath $requirementsLock) {
    Exec-OrThrow -Label "install locked dependencies" -FilePath $venvPython -CommandArgs @("-m", "pip", "install", "-r", $requirementsLock)
} else {
    Exec-OrThrow -Label "install dependencies (requirements.txt fallback)" -FilePath $venvPython -CommandArgs @("-m", "pip", "install", "-r", $requirementsTxt)
}

if (-not (Test-Path -LiteralPath $envLocal)) {
    if (Test-Path -LiteralPath $envExample) {
        Write-Step "create .env.local from .env.example"
        Copy-Item -LiteralPath $envExample -Destination $envLocal -Force
    } elseif (Test-Path -LiteralPath $envPlain) {
        Write-Step "copy .env to .env.local"
        Copy-Item -LiteralPath $envPlain -Destination $envLocal -Force
    } else {
        Write-Step "env template not found (.env.example/.env); skipping copy"
    }
} else {
    Write-Step ".env.local already exists"
}

if (Test-Path -LiteralPath $normalizeScript) {
    Exec-OrThrow -Label "normalize env file (utf8 no bom + lf)" -FilePath "powershell" -CommandArgs @("-ExecutionPolicy", "Bypass", "-File", $normalizeScript)
} else {
    Write-Step "normalize-env.ps1 not found; skipping"
}

if (-not $SkipDoctor) {
    if (Test-Path -LiteralPath $doctorScript) {
        Exec-OrThrow -Label ("run doctor (provider={0})" -f $Provider) -FilePath "powershell" -CommandArgs @("-ExecutionPolicy", "Bypass", "-File", $doctorScript, "-Provider", $Provider)
    } else {
        throw "doctor.ps1 not found."
    }
} else {
    Write-Step "doctor skipped by flag"
}

if (-not $SkipTests) {
    Exec-OrThrow -Label "run scan pipeline tests" -FilePath $venvPython -CommandArgs @("-m", "unittest", "discover", "-s", "tests/scan_pipeline", "-p", "test_*.py")
} else {
    Write-Step "tests skipped by flag"
}

Write-Output ""
Write-Output "[bootstrap] DONE"
Write-Output ("[bootstrap] activate venv: {0}" -f (Join-Path $venvPath "Scripts\Activate.ps1"))
Write-Output ("[bootstrap] run app: {0} main.py" -f $venvPython)
