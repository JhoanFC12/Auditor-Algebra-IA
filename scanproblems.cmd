@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "VENV_PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
  "%VENV_PY%" "%SCRIPT_DIR%scanproblems.py" %*
  exit /b %errorlevel%
)
set "LEGACY_VENV_PY=%SCRIPT_DIR%venv\Scripts\python.exe"
if exist "%LEGACY_VENV_PY%" (
  "%LEGACY_VENV_PY%" "%SCRIPT_DIR%scanproblems.py" %*
  exit /b %errorlevel%
)
python "%SCRIPT_DIR%scanproblems.py" %*
exit /b %errorlevel%
