@echo off
setlocal
cd /d "%~dp0"
title MARS Rover Earth Ground Station

echo ============================================================
echo  MARS Rover Earth Ground Station
echo  Rover supervision dashboard - LAN mode
echo  Other devices can open http://THIS_COMPUTER_IP:8080
echo ============================================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scripts\windows_launcher.py
) else (
  python scripts\windows_launcher.py
)
endlocal
