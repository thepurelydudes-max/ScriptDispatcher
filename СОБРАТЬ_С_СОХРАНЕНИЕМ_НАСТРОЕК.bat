@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

if not exist "build_exe.bat" (
  echo ERROR: Place this BAT next to build_exe.bat in the Script Dispatcher SOURCE folder.
  pause
  exit /b 1
)
if not exist "ScriptDispatcherBuild.spec" (
  echo ERROR: ScriptDispatcherBuild.spec was not found in this folder.
  pause
  exit /b 1
)

set "OLD_DATA=%~dp0dist\ScriptDispatcher\data"
set "NEW_DATA=%~dp0dist\ScriptDispatcher\data"
set "HAS_BACKUP=0"

if exist "%OLD_DATA%\" (
  call :backup_data
  if errorlevel 1 goto backup_failed
) else (
  echo NOTE: No data folder in dist\ScriptDispatcher.
  echo If your current EXE runs from ANOTHER folder, manually copy its data folder
  echo into dist\ScriptDispatcher\data BEFORE running this BAT.
  echo.
  pause
)

echo.
echo Close ScriptDispatcher.exe before continuing.
echo The existing build script may pause at the end. Press a key there
echo to return to THIS script, which restores your saved settings.
echo.
call "%~dp0build_exe.bat"
if errorlevel 1 goto build_failed
if not exist "%~dp0dist\ScriptDispatcher\ScriptDispatcher.exe" goto build_failed

if "%HAS_BACKUP%"=="1" (
  echo.
  echo Restoring old scheduler settings, secrets and history...
  robocopy "%BACKUP%" "%NEW_DATA%" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
  if errorlevel 8 goto restore_failed
)

echo.
echo SUCCESS: dist\ScriptDispatcher\ScriptDispatcher.exe
if "%HAS_BACKUP%"=="1" echo Original data restored. Backup remains at "%BACKUP%".
pause
exit /b 0

:backup_data
for /f "usebackq delims=" %%I in (`powershell.exe -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"`) do set "STAMP=%%I"
if not defined STAMP set "STAMP=%RANDOM%_%RANDOM%"
set "BACKUP=%~dp0_BACKUPS\data_before_build_%STAMP%"
echo Backing up data from the existing dist\ScriptDispatcher build...
robocopy "%OLD_DATA%" "%BACKUP%" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP
if errorlevel 8 exit /b 1
set "HAS_BACKUP=1"
echo Backup completed: "%BACKUP%"
exit /b 0

:backup_failed
echo ERROR: Could not back up data. Build has NOT started.
pause
exit /b 1

:build_failed
echo ERROR: Build failed. Old data backup has NOT been deleted.
pause
exit /b 1

:restore_failed
echo ERROR: Could not restore all data. The backup is still at "%BACKUP%".
echo Close the application, then copy backup contents into dist\ScriptDispatcher\data.
pause
exit /b 1
