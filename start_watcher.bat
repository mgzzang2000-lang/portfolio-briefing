@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

:loop
"%PYTHON_EXE%" watcher.py
timeout /t 5 >nul
goto loop
