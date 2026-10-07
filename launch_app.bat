@echo off
setlocal
cd /d "%~dp0"
cscript //nologo "%~dp0launch_app.vbs" --console
if errorlevel 1 (
    echo.
    echo Failed to start Dota 2 Translator. See the error details above.
    pause
    exit /b 1
)
exit /b 0
