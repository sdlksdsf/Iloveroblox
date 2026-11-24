@echo off
setlocal
cd /d "%~dp0"
title Roblox Studio MCP Bridge
color 0B

where py >nul 2>nul
if not errorlevel 1 goto usepy

where python >nul 2>nul
if not errorlevel 1 goto usepython

echo.
echo  Python 3 is not installed on this PC.
echo.
echo  1. Open https://www.python.org/downloads/
echo  2. Download and run the Windows installer.
echo  3. TICK "Add python.exe to PATH" on the first screen. This matters.
echo  4. Double-click this file again.
echo.
pause
exit /b 1

:usepy
py -3 bridge.py --run
set RC=%errorlevel%
if not "%RC%"=="0" pause
exit /b %RC%

:usepython
python bridge.py --run
set RC=%errorlevel%
if not "%RC%"=="0" pause
exit /b %RC%
