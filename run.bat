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

:: ── Model caches live on D: (keeps C: free for the system) ─
set HF_HOME=D:\LucyCache\huggingface
set OLLAMA_MODELS=D:\LucyCache\ollama-models

:: ── Phone install + push notifications (optional) ──────────
:: /mobile/<name> needs a real HTTPS hostname reachable from the phone —
:: start Tailscale separately and run once:
::   tailscale serve https / http://localhost:8000
:: Lucy boots and runs fully LAN-only without this; only the phone
:: install/subscribe page needs it.

:: ── Start Ollama in background if not running ─────────────
tasklist /fi "imagename eq ollama.exe" 2>nul | find /i "ollama.exe" >nul
if errorlevel 1 (
    echo  Starting Ollama service...
    start /min "" "C:\Users\hansj\AppData\Local\Programs\Ollama\ollama.exe" serve
    timeout /t 3 /nobreak >nul
)

:: ── Already running? just open the browser ────────────────
powershell -NoProfile -Command "exit (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue).Count"
if errorlevel 1 (
    echo  Lucy is already running - opening the browser.
    start "" http://localhost:8000
    timeout /t 3 /nobreak >nul
    exit /b
)

:: ── Launch server ─────────────────────────────────────────
echo  Launching Lucy server...
echo  The browser opens by itself once Lucy is ready (30-40s on a cold boot).
echo  Press Ctrl+C to stop.
echo.
pushd D:\Users\ruxtg
:: poll the port and open the browser only when the server actually answers
start "" /min powershell -NoProfile -Command "for($i=0;$i -lt 180;$i++){try{$c=New-Object Net.Sockets.TcpClient('127.0.0.1',8000);$c.Close();Start-Process 'http://localhost:8000';break}catch{Start-Sleep 1}}"
py -3.13 main.py
popd
echo.
echo  Lucy stopped. If this was unexpected, the error should be visible above.
pause
