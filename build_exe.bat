@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>&1
    if errorlevel 1 (
        echo Python not found. Install Python 3.11 or 3.12 from python.org.
        pause
        exit /b 1
    )
    py -3.12 -m venv .venv 2>nul
    if errorlevel 1 py -3.11 -m venv .venv
    if errorlevel 1 goto build_failed
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 goto build_failed
python -m pip install -r requirements.txt
if errorlevel 1 goto build_failed

python -m PyInstaller --noconfirm --clean ScriptDispatcherBuild.spec

if errorlevel 1 goto build_failed
if not exist "dist\ScriptDispatcher\ScriptDispatcher.exe" goto build_failed

echo.
echo Build complete: dist\ScriptDispatcher\ScriptDispatcher.exe
pause
exit /b 0

:build_failed
echo.
echo Build failed.
pause
exit /b 1
