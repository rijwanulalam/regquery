@echo off
REM Run by Task Scheduler: does one scan, uploads it to Supabase, and exits.

REM Go to the folder this .bat file is in
cd /d "%~dp0"

REM Run one scan with the Python inside .venv,
REM and add everything it prints (including errors) to scanner_log.txt
".venv\Scripts\python.exe" scanner.py >> scanner_log.txt 2>&1