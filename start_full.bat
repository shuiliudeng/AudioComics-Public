@echo off
chcp 65001 >nul
title Full Pipeline Launcher
echo ============================================
echo  Starting ALL services (each in its own window)...
echo  Closing a service window stops that service.
echo ============================================
echo.
echo [0] Cleaning old processes on ports 8188, 9880, 8012...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8188.*LISTENING"') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":9880.*LISTENING"') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8012.*LISTENING"') do taskkill /F /PID %%a >nul 2>&1
echo.
echo [1/3] ComfyUI (8188) ...
start "ComfyUI (8188)" /D "%~dp0" cmd /c start_comfyui.bat
echo [2/3] TTS (9880) ...
start "GPT-SoVITS TTS (9880)" /D "%~dp0" cmd /c start_tts.bat
echo [3/3] Comic Server (8012) ...
start "Comic Server (8012)" /D "%~dp0" cmd /c start_server.bat
echo.
echo Waiting for services to be ready (max 120s)...
set /a waited=0
:waitloop
set ok1=0
set ok2=0
set ok3=0
netstat -ano | findstr ":8188.*LISTENING" >nul 2>&1 && set ok1=1
netstat -ano | findstr ":9880.*LISTENING" >nul 2>&1 && set ok2=1
netstat -ano | findstr ":8012.*LISTENING" >nul 2>&1 && set ok3=1
if "%ok1%%ok2%%ok3%"=="111" goto allup
timeout /t 5 /nobreak >nul
set /a waited+=5
if %waited% geq 120 goto timeout
goto waitloop
:allup
echo.
echo  All services up: ComfyUI(8188) TTS(9880) Comic(8012)
echo  Opening browser...
start "" http://127.0.0.1:8012/
timeout /t 2 /nobreak >nul
exit
:timeout
echo.
echo  WARNING: some services did not start within 120s.
echo  Check the service windows for errors.
pause
