@echo off
title Lucy AI - Installer
color 0C

echo.
echo  ██╗     ██╗   ██╗ ██████╗██╗   ██╗
echo  ██║     ██║   ██║██╔════╝╚██╗ ██╔╝
echo  ██║     ██║   ██║██║      ╚████╔╝
echo  ██║     ██║   ██║██║       ╚██╔╝
echo  ███████╗╚██████╔╝╚██████╗   ██║
echo  ╚══════╝ ╚═════╝  ╚═════╝   ╚═╝
echo.
echo  AI COMPANION - INSTALLER v1.0
echo  ================================
echo.

:: ── Check Python ─────────────────────────────────────────
echo [1/5] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found.
    echo  Download it from https://www.python.org/downloads/
    echo  Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
python --version
echo  OK
echo.

:: ── Check Ollama ─────────────────────────────────────────
echo [2/5] Checking Ollama...
ollama --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Ollama not found.
    echo  Download it from https://ollama.com/download
    echo  Install it, then re-run this installer.
    pause
    exit /b 1
)
echo  OK
echo.

:: ── Install Python packages ───────────────────────────────
echo [3/5] Installing Python packages...
echo  This may take a few minutes on first run.
echo.
pip install fastapi "uvicorn[standard]" websockets faster-whisper edge-tts httpx requests numpy
if errorlevel 1 (
    echo.
    echo  ERROR: Package install failed.
    echo  Try running as Administrator or check your internet connection.
    pause
    exit /b 1
)
echo.
echo  Packages installed OK
echo.

:: ── Pull Ollama models ────────────────────────────────────
echo [4/5] Pulling AI models (this will take a while on first run)...
echo  Pulling language model: qwen2.5vl:3b (~2GB)
ollama pull qwen2.5vl:3b
echo.
echo  Pulling vision model: minicpm-v (~3GB)
ollama pull minicpm-v
echo.
echo  Models ready
echo.

:: ── Done ─────────────────────────────────────────────────
echo [5/5] Setup complete!
echo.
echo  ================================
echo  Run "run.bat" to start Lucy.
echo  ================================
echo.
pause
