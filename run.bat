@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    call install_and_run.bat
    exit /b
)
start "" ".venv\Scripts\pythonw.exe" app.py
