@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>&1
if errorlevel 1 (
    echo ERROR: uv was not found in PATH.
    echo Install uv first, then run this file again.
    pause
    exit /b 1
)

start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8780/'"
uv run editor_server.py

if errorlevel 1 (
    echo.
    echo The editor stopped with an error. Port 8780 may already be in use.
    pause
)
