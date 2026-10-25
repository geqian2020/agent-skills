@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3 "media-analysis\scripts\media_analysis.py" doctor
if errorlevel 1 python "media-analysis\scripts\media_analysis.py" doctor
pause
