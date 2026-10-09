@echo off
REM Double-click this file to start the Software Scanner
cd /d "%~dp0"
python -m streamlit run regquery.py
pause
