@echo off
setlocal
cd /d "%~dp0roblox-studio-bridge"
if not exist "START_BRIDGE.bat" (
    echo.
    echo  Could not find the "roblox-studio-bridge" folder next to this file.
    echo  Make sure you extracted the whole ZIP, not just this file.
    echo.
    pause
    exit /b 1
)
call "START_BRIDGE.bat"
