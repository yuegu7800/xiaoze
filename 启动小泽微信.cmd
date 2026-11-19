@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
rem Keep the existing full private/group runner and its compatible runtime data.
call "%~dp0start-acheng.cmd" %*
exit /b %errorlevel%
