@echo off
cd /d C:\users\rbing\nba-prop-dashboard
venv\Scripts\python.exe daily_parlay_gen.py >> logs\daily_parlay_gen.log 2>&1
