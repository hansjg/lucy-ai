@echo off
title GOD EARS
color 1F

echo.
echo  GOD EARS - always listening - Starting...
echo.

:: -- Force RTX 4050 (CUDA) over Intel UHD --------------------
set CUDA_VISIBLE_DEVICES=0
set OLLAMA_NUM_GPU=999
set OLLAMA_GPU_OVERHEAD=0

:: -- Start Ollama in background if not running ---------------
tasklist /fi "imagename eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if errorlevel 1 (
    echo  Starting Ollama service...
    start /min "" "C:\Users\hansj\AppData\Local\Programs\Ollama\ollama.exe" serve
    timeout /t 3 /nobreak >nul
)

:: -- Launch server -------------------------------------------
echo  Launching God Ears...
echo  Open http://localhost:8100 in your browser.
echo  Press Ctrl+C (or the Terminate button) to stop.
echo.
pushd "%~dp0"
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start "" http://localhost:8100"
py -3.13 god_ears.py
popd
