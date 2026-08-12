@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0RenpyThiefPatch.exe" (
    echo RenpyThiefPatch.exe was not found next to this launcher.
    pause
    exit /b 1
)
start "" "%~dp0RenpyThiefPatch.exe"
exit /b %errorlevel%
