@echo off
chcp 65001 >nul
title Stop All Services
echo ============================================
echo  Stopping ComfyUI(8188) / TTS(9880) / Comic Server(8012)...
echo ============================================
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8188.*LISTENING"') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":9880.*LISTENING"') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8012.*LISTENING"') do taskkill /F /PID %%a >nul 2>&1
echo.
echo All services stopped. VRAM released.
pause
