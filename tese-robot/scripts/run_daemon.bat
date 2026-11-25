@echo off
rem TSE Bot v2 - start the scheduler with the project virtualenv.
rem Dry-run is the default; add --i-understand-risk only after mode=live.
setlocal
set T=%~dp0..
set PY=C:\TSE_Strategy\venv\Scripts\python.exe
set PYTHONIOENCODING=utf-8
cd /d "%T%"
"%PY%" -u "%T%\scripts\run.py" daemon %*
endlocal
