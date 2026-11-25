@echo off
rem Bring TeseRobot back after a reboot: dashboard (5000+5077) and live daemon.
setlocal
set T=%~dp0..
set PY=C:\TSE_Strategy\venv\Scripts\python.exe
set PYTHONIOENCODING=utf-8
cd /d "%T%"

echo [1/2] dashboard supervisor
start "TeseRobot Web" /min "%PY%" -u "%T%\scripts\web_supervisor.py"

echo [2/2] trading daemon (live)
start "TeseRobot Daemon" /min "%PY%" -u "%T%\scripts\run.py" daemon --i-understand-risk

echo.
echo Dashboard: http://127.0.0.1:5077  ^|  http://127.0.0.1:5000
echo Health   : /health
exit /b 0
