@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "XIAOZE_PY=C:\Users\ZhuanZ\Documents\ChatGPT\奇思妙想\work\wechat-lab\.venv\Scripts\python.exe"
if not exist "%XIAOZE_PY%" goto missing_python
"%XIAOZE_PY%" "%~dp0launch_local.py" %*
if errorlevel 1 (
  echo.
  echo Local chat could not start. Keep this window open to read the error.
  pause
  exit /b 1
)
exit /b 0
:missing_python
echo Configured Python was not found. See the local chat guide.
pause
exit /b 1
