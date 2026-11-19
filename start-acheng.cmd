@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "ACHENG_PY=C:\Users\ZhuanZ\Documents\ChatGPT\奇思妙想\work\wechat-lab\.venv\Scripts\python.exe"
if not exist "%ACHENG_PY%" goto missing_python
if /i "%~1"=="--check" goto check
"%ACHENG_PY%" "%~dp0launch.py" --check
set "ACHENG_EXIT=%ERRORLEVEL%"
if not "%ACHENG_EXIT%"=="0" (
  echo.
  echo Startup failed. Keep this window open and report the error above.
  pause
  exit /b %ACHENG_EXIT%
)
set "ACHENG_PYW=C:\Users\ZhuanZ\Documents\ChatGPT\奇思妙想\work\wechat-lab\.venv\Scripts\pythonw.exe"
start "" "%ACHENG_PYW%" "%~dp0launch.py"
echo Acheng runs in its own window. You may close this console.
exit /b 0
:check
"%ACHENG_PY%" "%~dp0launch.py" --check
exit /b %ERRORLEVEL%
:missing_python
echo Python was not found at the configured path.
pause
exit /b 1
