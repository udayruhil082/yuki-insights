@echo off
title Yuki Insights
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Please install it from https://www.python.org/downloads/ and tick "Add Python to PATH".
  pause
  exit /b
)
python -m pip install --quiet --disable-pip-version-check openpyxl==3.1.5 >nul 2>nul
python radar\app.py
pause
