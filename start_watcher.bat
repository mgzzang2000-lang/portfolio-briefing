@echo off
cd /d "%~dp0"
set PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
:loop
"%PYTHON_EXE%" watcher.py
echo.
echo [watcher.py 종료됨 — 5초 후 재시작]
timeout /t 5 >nul
goto loop
