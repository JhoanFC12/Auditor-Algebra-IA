@echo off
setlocal
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap-windows.ps1" %*
exit /b %errorlevel%
