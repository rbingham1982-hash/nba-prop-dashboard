@echo off
cd /d C:\users\rbing\nba-prop-dashboard
REM No redirect here: daily_parlay_gen.py appends to logs\daily_parlay_gen.log
REM itself, so runs launched outside this script are logged too. Redirecting as
REM well would write every line twice.
REM %* forwards arguments so a second, deliberately-forced run can be scheduled for a
REM split slate: run_daily_parlay_gen.bat --force
venv\Scripts\python.exe daily_parlay_gen.py %*
