@echo off
REM Keep the NBA/MLB prop dashboard running: relaunch Streamlit whenever it exits.
REM Launch detached (survives terminal/session close):
REM   powershell -Command "Start-Process -FilePath '.\run_dashboard_forever.bat' -WindowStyle Hidden"
REM To stop the dashboard entirely, kill this wrapper (taskkill /IM cmd.exe filtered,
REM or Stop-Process on its PID) AND the python.exe on port 8501 — otherwise it just
REM restarts.
cd /d C:\Users\rbing\nba-prop-dashboard
if not exist logs mkdir logs

:loop
echo [%date% %time%] (re)starting streamlit on :8501 >> logs\dashboard_wrapper.log
venv\Scripts\python.exe -m streamlit run nba_prop_dashboard.py --server.port 8501 --server.headless true >> logs\streamlit_restart.log 2>&1
echo [%date% %time%] streamlit exited (code %errorlevel%); restarting in 3s >> logs\dashboard_wrapper.log
timeout /t 3 /nobreak >nul
goto loop
