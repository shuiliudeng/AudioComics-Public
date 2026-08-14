@echo off
chcp 65001 >nul
title ComfyUI Server (8188)
echo ============================================
echo  Starting ComfyUI on port 8188...
echo  Closing this window stops the service.
echo ============================================
call "%~dp0config\paths.bat"
cd /d "%~dp0"
"%GPTSOVITS_PY%" logtee.py "logs\comfyui.log" -- "%COMFYUI_PY%" "%COMFYUI_MAIN%" --force-fp16 --windows-standalone-build --highvram --cuda-device 0 --cuda-malloc --port 8188 --listen 127.0.0.1
echo.
echo ComfyUI has exited. Press any key to close.
pause
