@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-qtrade.ps1" %*
exit /b %errorlevel%
