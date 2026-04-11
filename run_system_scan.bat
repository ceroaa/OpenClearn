@echo off
setlocal
cd /d "%~dp0"
python system_scan.py --recent-hours 24 --min-file-mb 200 --top 20 --recent-limit 50
endlocal

