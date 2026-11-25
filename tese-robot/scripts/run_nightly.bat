@echo off
rem TeseRobot - manual nightly chain: data -> features -> picks
rem Use this if the scheduler was not running at 20:30 Tehran.
setlocal
set T=%~dp0..
set PY=C:\TSE_Strategy\venv\Scripts\python.exe
set PYTHONIOENCODING=utf-8
cd /d "%T%"

echo [1/3] daily bars
"%PY%" -u "%T%\scripts\update_data.py" || goto :err

echo [2/3] intraday + Burst features (top-N candidates)
"%PY%" -u "%T%\scripts\build_day_features.py" --top 120 || goto :err

echo [3/3] queue_v3 picks
"%PY%" -u "%T%\scripts\run.py" picks || goto :err

echo NIGHTLY OK
exit /b 0

:err
echo NIGHTLY FAILED - see logs
exit /b 1
