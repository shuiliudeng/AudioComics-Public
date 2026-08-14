@echo off
chcp 65001 >nul
title Comic Server (8012)
echo ============================================
echo  Starting Comic Server on port 8012...
echo  Closing this window stops the service.
echo ============================================
echo  [0] Killing old server on port 8012...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8012.*LISTENING"') do taskkill /F /PID %%a >nul 2>&1
cd /d "%~dp0"
echo  [1] Opening browser: project home page...
start "" /B powershell -NoProfile -Command "Start-Sleep -Seconds 4; Start-Process 'http://127.0.0.1:8012/'"
call "%~dp0config\paths.bat"
"%GPTSOVITS_PY%" logtee.py "logs\comic_server.log" -- "%GPTSOVITS_PY%" server.py
echo.
echo Server has exited. Press any key to close.
pause
