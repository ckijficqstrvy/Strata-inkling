@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-Inkling.ps1" -Mode fast -OpenBrowser
pause
