@echo off
chcp 65001 >nul
cd /d "%~dp0"
title HBR Battle Timeline
if not exist "%~dp0.venv\Scripts\python.exe" goto install
"%~dp0.venv\Scripts\python.exe" -c "import cv2,numpy,openpyxl; from PIL import Image; from rapidocr_onnxruntime import RapidOCR" >nul 2>&1
if errorlevel 1 goto install
goto run
:install
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
    pause
    exit /b 1
)
:run
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0app.py" %*
if errorlevel 1 (
    echo.
    echo Startup failed. Please see the error above and README.md.
    pause
)
