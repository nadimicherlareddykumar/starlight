@echo off
title DriveGuard - History and Reports Viewer
cd /d "%~dp0"
echo ===================================================
echo   Starting DriveGuard History and Reports Viewer
echo ===================================================
.\.venv\Scripts\python.exe web_only.py
pause
