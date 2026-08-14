@echo off
chcp 65001 >nul
title GPT-SoVITS TTS Server (9880)
echo ============================================
echo  Starting TTS API on port 9880...
echo  Closing this window stops the service.
echo ============================================
call "%~dp0config\paths.bat"
cd /d "%GPTSOVITS_ROOT%"
"%GPTSOVITS_PY%" "%~dp0logtee.py" --cwd "%GPTSOVITS_ROOT%" "%~dp0logs\tts.log" -- "%GPTSOVITS_PY%" api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS\configs\tts_infer.yaml
echo.
echo TTS Server has exited. Press any key to close.
pause
