$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$launcher = Join-Path $root "abrir_svg_editor.pyw"
$icon = Join-Path $root "dist\MathContentStudio\MathContentStudio.exe"
$progId = "AuditorIA.SVGEditor"
$appName = "Graficador SVG - Auditor IA"

if (-not (Test-Path $pythonw)) {
    throw "No se encontro pythonw.exe en .venv: $pythonw"
}
if (-not (Test-Path $launcher)) {
    throw "No se encontro el lanzador: $launcher"
}

$command = "`"$pythonw`" `"$launcher`" `"%1`""

New-Item -Path "HKCU:\Software\Classes\$progId" -Force | Out-Null
Set-ItemProperty -Path "HKCU:\Software\Classes\$progId" -Name "(default)" -Value $appName

New-Item -Path "HKCU:\Software\Classes\$progId\DefaultIcon" -Force | Out-Null
if (Test-Path $icon) {
    Set-ItemProperty -Path "HKCU:\Software\Classes\$progId\DefaultIcon" -Name "(default)" -Value "$icon,0"
}

New-Item -Path "HKCU:\Software\Classes\$progId\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path "HKCU:\Software\Classes\$progId\shell\open\command" -Name "(default)" -Value $command

New-Item -Path "HKCU:\Software\Classes\.svg\OpenWithProgids" -Force | Out-Null
New-ItemProperty -Path "HKCU:\Software\Classes\.svg\OpenWithProgids" -Name $progId -Value "" -PropertyType String -Force | Out-Null

$contextPath = "HKCU:\Software\Classes\SystemFileAssociations\.svg\shell\Abrir con Graficador SVG"
New-Item -Path "$contextPath\command" -Force | Out-Null
Set-ItemProperty -Path $contextPath -Name "(default)" -Value "Abrir con Graficador SVG - Auditor IA"
Set-ItemProperty -Path "$contextPath\command" -Name "(default)" -Value $command

Write-Host "Registrado: $appName"
Write-Host "Comando: $command"
