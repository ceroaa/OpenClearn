@echo off
setlocal
cd /d "%~dp0"
python scavenger.py --config config.example.json --dry-run
endlocal
