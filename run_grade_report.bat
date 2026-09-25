@echo off
cd /d C:\users\rbing\nba-prop-dashboard
REM No redirect: grade_report.py appends to logs\grade_report.log itself, so runs
REM launched by hand are logged too and a redirect here would write every line twice.
REM %* forwards a season and week, e.g. run_grade_report.bat 2026 3 — with no arguments
REM it reports the most recent week that has results.
venv\Scripts\python.exe grade_report.py %*
