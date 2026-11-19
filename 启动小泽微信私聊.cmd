@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "XIAOZE_WECHAT_PY=C:\Users\ZhuanZ\Documents\ChatGPT\奇思妙想\work\wechat-lab\.venv\Scripts\python.exe"
if not exist "%XIAOZE_WECHAT_PY%" goto missing_python
if /i "%~1"=="--check" goto check
"%XIAOZE_WECHAT_PY%" "%~dp0wechat_private_ui.py"
if errorlevel 1 goto launch_failed
exit /b 0
:check
"%XIAOZE_WECHAT_PY%" -B -c "import wechat_private_ui, wechat_private_bridge, sys; assert 'app' not in sys.modules; assert 'wx_backend.visual_backend' not in sys.modules; print('Xiaoze WeChat entry: import OK; no WeChat connection or API request.')"
exit /b %errorlevel%
:missing_python
echo Python environment was not found. Do not delete the shared wechat-lab environment.
pause
exit /b 1
:launch_failed
echo.
echo Xiaoze could not start. Keep this window open to read the error.
pause
exit /b 1
