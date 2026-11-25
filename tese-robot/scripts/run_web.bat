@echo off
rem TeseRobot dashboard — http://127.0.0.1:5077
setlocal
set T=%~dp0..
set PY=C:\TSE_Strategy\venv\Scripts\python.exe
set PYTHONIOENCODING=utf-8
cd /d "%T%"
:loop
"%PY%" -u "%T%\webapp\app.py"
timeout /t 5 /nobreak >nul
goto loop
