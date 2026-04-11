@echo off
setlocal
cd /d "%~dp0"
python clean_chrome_ai_cache.py --kill-chrome
endlocal

