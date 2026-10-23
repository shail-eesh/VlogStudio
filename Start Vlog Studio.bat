@echo off
rem Starts Vlog Studio with a visible log window (closing the window stops the app).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call setup.bat
call "%~dp0vlog.cmd" ui
pause
