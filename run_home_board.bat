@echo off
REM The home (NBA + WNBA) half of the daily run, for Task Scheduler. The git sync and the
REM generator call live in run_home_board.ps1; %* forwards --force for a split slate.
cd /d C:\users\rbing\nba-prop-dashboard
powershell.exe -NoProfile -ExecutionPolicy Bypass -File run_home_board.ps1 %*
