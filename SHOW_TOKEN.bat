@echo off
setlocal
cd /d "%~dp0roblox-studio-bridge"
title Roblox Bridge - Show Token
color 0A

where py >nul 2>nul
if not errorlevel 1 goto usepy
where python >nul 2>nul
if not errorlevel 1 goto usepython

echo  Python is not installed. Install it from https://www.python.org/downloads/
echo  and tick "Add python.exe to PATH".
echo.
pause
exit /b 1

:usepy
py -3 bridge.py --show-token
pause
exit /b 0

:usepython
python bridge.py --show-token
pause
exit /b 0
