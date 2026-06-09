@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  ".venv\Scripts\pythonw.exe" -m modulos.modulo8_svg_editor.ai_assistant_lab.gui
) else if exist "venv\Scripts\pythonw.exe" (
  "venv\Scripts\pythonw.exe" -m modulos.modulo8_svg_editor.ai_assistant_lab.gui
) else (
  python -m modulos.modulo8_svg_editor.ai_assistant_lab.gui
)
