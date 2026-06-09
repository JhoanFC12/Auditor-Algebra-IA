param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$specPath = Join-Path $projectRoot "MathContentStudio.spec"

if (!(Test-Path $pythonExe)) {
    throw "No se encontro Python del entorno virtual en: $pythonExe"
}
if (!(Test-Path $specPath)) {
    throw "No se encontro el archivo spec en: $specPath"
}

if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $projectRoot "build")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $projectRoot "dist")
}

& $pythonExe -m pip install --upgrade pyinstaller
& $pythonExe -m PyInstaller --noconfirm --clean $specPath

if ($LASTEXITCODE -ne 0) {
    throw "Fallo el build con PyInstaller."
}

$exePath = Join-Path $projectRoot "dist\MathContentStudio\MathContentStudio.exe"
Write-Host ""
Write-Host "Build completado." -ForegroundColor Green
Write-Host "Ejecutable: $exePath"
