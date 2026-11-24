@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Setup-Inkling.ps1" -LaunchGui
pause
