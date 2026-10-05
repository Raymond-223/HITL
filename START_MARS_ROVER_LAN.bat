@echo off
setlocal
cd /d "%~dp0"
title MARS Rover Earth Ground Station - LAN

echo WARNING: LAN mode has no account authentication.
echo Use it only on a trusted, isolated rover network.
echo Ground Station URL: http://THIS_COMPUTER_IP:8080
echo.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 scripts\windows_launcher.py --host 0.0.0.0
) else (
  python scripts\windows_launcher.py --host 0.0.0.0
)
endlocal
