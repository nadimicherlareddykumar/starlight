@echo off
title DriveGuard - Driver Safety System
cd /d "%~dp0"
echo ===================================================
echo   Starting DriveGuard - Driver Safety System
echo ===================================================
.\.venv\Scripts\python.exe main.py
pause
