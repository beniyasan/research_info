@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_wsl.ps1" -Job weekly %*
exit /b %ERRORLEVEL%
