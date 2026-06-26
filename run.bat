@echo off
title Lucy AI
color 0C

echo.
echo  LUCY AI COMPANION - Starting...
echo.

:: ── Force RTX 4050 (CUDA) over Intel UHD ──────────────────
set CUDA_VISIBLE_DEVICES=0
set OLLAMA_NUM_GPU=999
set OLLAMA_GPU_OVERHEAD=0

:: ── Start Ollama in background if not running ─────────────
tasklist /fi "imagename eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if errorlevel 1 (
    echo  Starting Ollama service...
    start /min "" "C:\Users\hansj\AppData\Local\Programs\Ollama\ollama.exe" serve
    timeout /t 3 /nobreak >nul
)

:: ── Launch server ─────────────────────────────────────────
echo  Launching Lucy server...
echo  Open http://localhost:8000 in your browser.
echo  Press Ctrl+C to stop.
echo.
start "" "http://localhost:8000"
python main.py
