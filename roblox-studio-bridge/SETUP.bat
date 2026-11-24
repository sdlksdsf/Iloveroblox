@echo off
setlocal
cd /d "%~dp0"
title Roblox Studio MCP Bridge - Setup
color 0E

echo.
echo  This creates your secret token and checks that everything works.
echo  You only do this ONCE.
echo.

where py >nul 2>nul
if not errorlevel 1 goto usepy

where python >nul 2>nul
if not errorlevel 1 goto usepython

echo  Python 3 is not installed. Install it from https://www.python.org/downloads/
echo  and tick "Add python.exe to PATH", then run this again.
echo.
pause
exit /b 1

:usepy
py -3 bridge.py --setup
py -3 bridge.py --doctor
pause
exit /b 0

:usepython
python bridge.py --setup
python bridge.py --doctor
pause
exit /b 0
