@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installing media-analysis Skill for Codex and Claude Code...
py -3 "media-analysis\setup_global.py" --agent codex --agent claude
if errorlevel 1 (
  echo Trying python command instead...
  python "media-analysis\setup_global.py" --agent codex --agent claude
)
echo.
echo Restart Codex/Claude Code to load the Skill.
echo Note: this does not install anything to ChatGPT web sessions.
pause
