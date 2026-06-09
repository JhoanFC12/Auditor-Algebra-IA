[CmdletBinding()]
param(
    [string]$ProjectRoot = "",
    [ValidateSet("hf", "openai", "ocr")]
    [string]$Provider = "hf"
)

$ErrorActionPreference = "Stop"
$failed = $false

function Write-Section {
    param([string]$Title)
    Write-Output ""
    Write-Output ("=== {0} ===" -f $Title)
}

function Mark-Fail {
    param([string]$Message)
    Write-Output ("[FAIL] {0}" -f $Message)
    $script:failed = $true
}

function Mark-Ok {
    param([string]$Message)
    Write-Output ("[OK] {0}" -f $Message)
}

function Resolve-Root {
    param([string]$RawRoot)
    if ($RawRoot -and $RawRoot.Trim()) {
        return (Resolve-Path -LiteralPath $RawRoot).Path
    }
    return (Get-Location).Path
}

function Resolve-PythonExecutable {
    param([string]$Root)
    $candidates = @(
        (Join-Path $Root ".venv\Scripts\python.exe"),
        (Join-Path $Root "venv\Scripts\python.exe"),
        "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python311\python.exe",
        "C:\Program Files\Python311\python.exe",
        "C:\Program Files (x86)\Python311\python.exe"
    )
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            & $candidate --version *> $null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch {}
    }
    try {
        $resolved = (Get-Command python -ErrorAction Stop).Source
        if ($resolved) { return $resolved }
    } catch {}
    return ""
}

function Resolve-GitExecutable {
    $candidates = @(
        "C:\Program Files\Git\cmd\git.exe",
        "C:\Program Files\Git\bin\git.exe",
        "C:\Program Files (x86)\Git\cmd\git.exe",
        "C:\Users\$env:USERNAME\AppData\Local\Programs\Git\cmd\git.exe"
    )
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        try {
            & $candidate --version *> $null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch {}
    }
    try {
        $resolved = (Get-Command git -ErrorAction Stop).Source
        if ($resolved) { return $resolved }
    } catch {}
    return ""
}

function Get-GitConfigValue {
    param(
        [string]$Root,
        [string]$Scope,
        [string]$Key,
        [string]$GitExe
    )
    if (-not $GitExe) {
        return ""
    }
    try {
        if ($Scope -eq "local") {
            $value = (& $GitExe -C $Root config --local $Key) 2>$null
        } else {
            $value = (& $GitExe -C $Root config --global $Key) 2>$null
        }
        return ($value | Out-String).Trim()
    } catch {
        return ""
    }
}

function Display-OrUnset {
    param([string]$Value)
    if ($null -eq $Value) { return "<unset>" }
    $trimmed = ($Value | Out-String).Trim()
    if (-not $trimmed) { return "<unset>" }
    return $trimmed
}

function Parse-EnvFileMap {
    param([string]$Path)
    $envMap = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $envMap }
    $lines = Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue
    foreach ($line in $lines) {
        $trimmed = ($line | Out-String).Trim()
        if (-not $trimmed) { continue }
        if ($trimmed.StartsWith("#")) { continue }
        if ($trimmed -notmatch "^[A-Za-z_][A-Za-z0-9_]*\s*=") { continue }
        $parts = $trimmed.Split("=", 2)
        $key = $parts[0].Trim()
        $value = ""
        if ($parts.Length -gt 1) {
            $value = $parts[1].Trim().Trim("'").Trim('"')
        }
        if ($key) {
            $envMap[$key] = $value
        }
    }
    return $envMap
}

function Is-PlaceholderValue {
    param([string]$Value)
    $v = ($Value | Out-String).Trim().ToLowerInvariant()
    if (-not $v) { return $true }
    if ($v -match "replace_me") { return $true }
    if ($v -match "xxx_replace_me") { return $true }
    if ($v -match "changeme") { return $true }
    if ($v -match "^example") { return $true }
    if ($v -match "^dummy") { return $true }
    if ($v -eq "your_value_here") { return $true }
    if ($v -match "^hf_xxx") { return $true }
    if ($v -match "^sk_replace_me") { return $true }
    return $false
}

function Is-Truthy {
    param([string]$Value)
    $v = ($Value | Out-String).Trim().ToLowerInvariant()
    return ($v -in @("1", "true", "yes", "y", "si"))
}

function Has-Bom {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -lt 3) { return $false }
    return ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
}

function Normalize-ReqLines {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    $lines = Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue
    $out = @()
    foreach ($line in $lines) {
        $trimmed = ($line | Out-String).Trim()
        if (-not $trimmed) { continue }
        if ($trimmed.StartsWith("#")) { continue }
        $out += $trimmed
    }
    return $out
}

$root = Resolve-Root -RawRoot $ProjectRoot
$pythonExe = Resolve-PythonExecutable -Root $root
$gitExe = Resolve-GitExecutable

Write-Section "System"
try {
    $os = Get-CimInstance Win32_OperatingSystem
    Write-Output ("Windows: {0} ({1})" -f $os.Caption, $os.Version)
} catch {
    Write-Output "Windows: <unknown>"
}
Write-Output ("PowerShell: {0}" -f $PSVersionTable.PSVersion.ToString())

Write-Section "Git"
if ($gitExe) {
    $gitVersion = (& $gitExe --version | Out-String).Trim()
    Mark-Ok "git_version=$gitVersion"
} else {
    Mark-Fail "git no esta disponible"
}

$autocrlfLocal = Get-GitConfigValue -Root $root -Scope "local" -Key "core.autocrlf" -GitExe $gitExe
$autocrlfGlobal = Get-GitConfigValue -Root $root -Scope "global" -Key "core.autocrlf" -GitExe $gitExe
$eolLocal = Get-GitConfigValue -Root $root -Scope "local" -Key "core.eol" -GitExe $gitExe
$eolGlobal = Get-GitConfigValue -Root $root -Scope "global" -Key "core.eol" -GitExe $gitExe
Write-Output ("core.autocrlf local={0} global={1}" -f (Display-OrUnset $autocrlfLocal), (Display-OrUnset $autocrlfGlobal))
Write-Output ("core.eol      local={0} global={1}" -f (Display-OrUnset $eolLocal), (Display-OrUnset $eolGlobal))

Write-Section "Runtime"
if ($pythonExe) {
    $py = (& $pythonExe -V 2>&1 | Out-String).Trim()
    Mark-Ok "python=$py"
} else {
    Mark-Fail "python no esta disponible"
}

Write-Section "Env file"
$envLocal = Join-Path $root ".env.local"
$envPath = Join-Path $root ".env"
$selectedEnv = ""
if (Test-Path -LiteralPath $envLocal) {
    $selectedEnv = $envLocal
} elseif (Test-Path -LiteralPath $envPath) {
    $selectedEnv = $envPath
}

if (-not $selectedEnv) {
    Mark-Fail "no existe .env.local ni .env"
} else {
    Mark-Ok ("env_file={0}" -f $selectedEnv)
    if (Has-Bom -Path $selectedEnv) {
        Mark-Fail "el env file tiene BOM UTF-8 (EF BB BF)"
    } else {
        Mark-Ok "env_file_sin_bom=true"
    }

    $present = Parse-EnvFileMap -Path $selectedEnv
    $requiredVars = @()
    if ($Provider -eq "hf") {
        $requiredVars += "HF_TOKEN"
    } elseif ($Provider -eq "openai") {
        $requiredVars += "OPENAI_API_KEY"
    }

    $dbVars = @("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_SSLMODE")
    $dbRequired = $true
    if ($present.ContainsKey("DB_REQUIRED")) {
        $dbRequired = Is-Truthy -Value $present["DB_REQUIRED"]
    }
    if ($dbRequired) {
        Write-Output "db_required=true"
        $requiredVars += $dbVars
    } else {
        Write-Output "db_required=false"
    }

    Write-Output ("provider={0}" -f $Provider)
    $missing = @()
    foreach ($varName in $requiredVars) {
        if (-not $present.ContainsKey($varName) -or (Is-PlaceholderValue -Value $present[$varName])) {
            $missing += $varName
        }
    }
    if ($missing.Count -gt 0) {
        Mark-Fail ("variables faltantes en env: {0}" -f (($missing -join ", ")))
    } else {
        Mark-Ok "variables requeridas presentes en env"
    }

    if ($dbRequired -and $missing.Count -eq 0) {
        $dbHost = $present["DB_HOST"]
        $dbPort = $present["DB_PORT"]
        $dbName = $present["DB_NAME"]

        try {
            $portNum = [int]$dbPort
        } catch {
            $portNum = 0
        }

        $tcpReachable = $false
        if ($portNum -le 0) {
            Mark-Fail ("DB_PORT invalido: {0}" -f $dbPort)
        } else {
            $tcp = Test-NetConnection -ComputerName $dbHost -Port $portNum -WarningAction SilentlyContinue
            if (-not $tcp.TcpTestSucceeded) {
                Mark-Fail ("no se puede alcanzar {0}:{1}" -f $dbHost, $dbPort)
            } else {
                $tcpReachable = $true
                Mark-Ok ("tcp_db={0}:{1}" -f $dbHost, $dbPort)
            }
        }

        if ($tcpReachable) {
            $dbEnvNames = @("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_SSLMODE", "DB_CONNECT_TIMEOUT", "DB_SSLROOTCERT")
            $previousDbEnv = @{}
            foreach ($dbEnvName in $dbEnvNames) {
                $previousDbEnv[$dbEnvName] = [Environment]::GetEnvironmentVariable($dbEnvName, "Process")
                if ($present.ContainsKey($dbEnvName)) {
                    [Environment]::SetEnvironmentVariable($dbEnvName, $present[$dbEnvName], "Process")
                } else {
                    [Environment]::SetEnvironmentVariable($dbEnvName, $null, "Process")
                }
            }

            Push-Location $root
            try {
                $pyCheck = @'
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from database.connection import DatabaseManager

db = DatabaseManager()
conn = db.get_connection(db.db_name)
conn.close()
print("DB_OK")
'@
                if (-not $pythonExe) {
                    throw "python no esta disponible"
                }
                $tempScript = Join-Path ([System.IO.Path]::GetTempPath()) ("auditor_ia_doctor_db_" + [Guid]::NewGuid().ToString("N") + ".py")
                $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
                [System.IO.File]::WriteAllText($tempScript, $pyCheck, $utf8NoBom)
                try {
                    $cmdLine = '"' + $pythonExe + '" "' + $tempScript + '" 2>&1'
                    $pyOut = (& cmd.exe /d /c $cmdLine | Out-String).Trim()
                    $pyExit = $LASTEXITCODE
                } finally {
                    Remove-Item -LiteralPath $tempScript -Force -ErrorAction SilentlyContinue
                }
            } finally {
                Pop-Location
                foreach ($dbEnvName in $dbEnvNames) {
                    [Environment]::SetEnvironmentVariable($dbEnvName, $previousDbEnv[$dbEnvName], "Process")
                }
            }

            if ($pyExit -eq 0) {
                Mark-Ok ("db_auth_ok={0}" -f $dbName)
            } else {
                $detail = ($pyOut | Out-String).Trim()
                $detailLower = $detail.ToLowerInvariant()
                if ($detailLower -match "ssl|tls|certificate") {
                    Mark-Fail ("fallo TLS/SSL al conectar a la BD: {0}" -f $detail)
                } elseif ($detailLower -match "password authentication failed|authentication failed") {
                    Mark-Fail ("credenciales de BD rechazadas: {0}" -f $detail)
                } else {
                    Mark-Fail ("no se pudo autenticar contra la BD: {0}" -f $detail)
                }
            }
        }
    }
}

Write-Section "Dependencies"
$requirements = Join-Path $root "requirements.txt"
$lock = Join-Path $root "requirements-lock.txt"

if (-not (Test-Path -LiteralPath $requirements)) {
    Mark-Fail "missing requirements.txt"
} else {
    Mark-Ok "requirements.txt presente"
}

if (-not (Test-Path -LiteralPath $lock)) {
    Mark-Fail "missing requirements-lock.txt"
} else {
    Mark-Ok "requirements-lock.txt presente"
    $reqMain = Normalize-ReqLines -Path $requirements
    $reqLock = Normalize-ReqLines -Path $lock
    $mainSet = @{}
    foreach ($line in $reqMain) { $mainSet[$line] = $true }
    $lockSet = @{}
    foreach ($line in $reqLock) { $lockSet[$line] = $true }
    $missingFromLock = @()
    foreach ($line in $reqMain) {
        if (-not $lockSet.ContainsKey($line)) {
            $missingFromLock += $line
        }
    }
    if ($missingFromLock.Count -gt 0) {
        Mark-Fail ("requirements-lock.txt no contiene: {0}" -f ($missingFromLock -join "; "))
    } else {
        Mark-Ok "requirements-lock.txt incluye todas las lineas de requirements.txt"
    }
}

Write-Section "Summary"
if ($failed) {
    Write-Output "[RESULT] FAIL"
    exit 1
}
Write-Output "[RESULT] PASS"
exit 0
