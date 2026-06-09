[CmdletBinding()]
param(
    [string]$Path = ""
)

$ErrorActionPreference = "Stop"

function Resolve-EnvPath {
    param([string]$RawPath)
    if ($RawPath -and $RawPath.Trim()) {
        return (Resolve-Path -LiteralPath $RawPath).Path
    }
    $cwd = (Get-Location).Path
    $localPath = Join-Path $cwd ".env.local"
    $envPath = Join-Path $cwd ".env"
    if (Test-Path -LiteralPath $localPath) { return $localPath }
    if (Test-Path -LiteralPath $envPath) { return $envPath }
    throw "No .env.local or .env found in $cwd. Use -Path <file>."
}

function Read-AllBytes {
    param([string]$FilePath)
    return [System.IO.File]::ReadAllBytes($FilePath)
}

function Write-Utf8NoBom {
    param(
        [string]$FilePath,
        [string]$Content
    )
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($FilePath, $Content, $encoding)
}

$resolved = Resolve-EnvPath -RawPath $Path
$bytesBefore = Read-AllBytes -FilePath $resolved
$hadBom = $false
if ($bytesBefore.Length -ge 3) {
    $hadBom = ($bytesBefore[0] -eq 0xEF -and $bytesBefore[1] -eq 0xBB -and $bytesBefore[2] -eq 0xBF)
}

$text = ""
try {
    $text = [System.IO.File]::ReadAllText($resolved, [System.Text.Encoding]::UTF8)
} catch {
    $text = [System.IO.File]::ReadAllText($resolved)
}

# Repository strategy: normalize to LF.
$normalized = $text -replace "`r`n", "`n"
$normalized = $normalized -replace "`r", "`n"

Write-Utf8NoBom -FilePath $resolved -Content $normalized

$bytesAfter = Read-AllBytes -FilePath $resolved
$hasBomAfter = $false
if ($bytesAfter.Length -ge 3) {
    $hasBomAfter = ($bytesAfter[0] -eq 0xEF -and $bytesAfter[1] -eq 0xBB -and $bytesAfter[2] -eq 0xBF)
}

Write-Output ("[normalize-env] file={0}" -f $resolved)
Write-Output ("[normalize-env] had_bom={0} has_bom_after={1}" -f $hadBom, $hasBomAfter)
Write-Output "[normalize-env] eol=LF utf8_no_bom=true"

