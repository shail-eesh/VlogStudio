@echo off
rem Vlog Studio command line for Windows:  vlog <command>   (run setup.bat once first)
setlocal
set "HERE=%~dp0"
if not exist "%HERE%.venv\Scripts\python.exe" (
  echo Vlog Studio is not set up yet. Run setup.bat first.
  exit /b 1
)
set "PYTHONPATH=%HERE%;%PYTHONPATH%"
set "PYTHONUTF8=1"
"%HERE%.venv\Scripts\python.exe" -m vlogstudio %*
