@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-Inkling.ps1" -Mode dspark -OpenBrowser
pause
