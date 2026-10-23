@echo off
rem One-time setup for Vlog Studio on Windows (fully local).
setlocal
cd /d "%~dp0"
echo.
echo  Vlog Studio setup
echo  -----------------

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo  ffmpeg was not found.
  echo  Install it with:   winget install Gyan.FFmpeg
  echo  then CLOSE this window, open a new one and run setup.bat again.
  echo.
  pause
  exit /b 1
)

set "PY="
for %%V in (3.13 3.12 3.11) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo  Python 3.11 or newer was not found.
  echo  Install it with:   winget install Python.Python.3.12
  echo  or from https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^)
  echo.
  pause
  exit /b 1
)
echo  Using %PY%

if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto :fail
if not exist "vlog.toml" copy /y "vlog.example.toml" "vlog.toml" >nul

rem Desktop shortcut that starts the app without a console window
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Vlog Studio.lnk'));" ^
  "$s.TargetPath=(Join-Path '%~dp0' 'Vlog Studio.vbs'); $s.WorkingDirectory='%~dp0'; $s.IconLocation=(Join-Path '%~dp0' 'assets\vlogstudio.ico'); $s.Save()" >nul 2>nul

echo.
echo  Setup complete.
echo   - Double-click "Vlog Studio" on your Desktop (or "Vlog Studio.vbs" here) to open the app.
echo   - Command line:  vlog doctor   /   vlog make
echo   - Optional local AI: install Ollama (https://ollama.com), then:  ollama pull qwen2.5vl:7b
echo.
".venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'.'); from vlogstudio import util; print('  GPU encoding:', util.gpu_kind() or 'not available (CPU will be used)')"
echo.
pause
exit /b 0

:fail
echo.
echo  Setup failed. See the messages above.
pause
exit /b 1
