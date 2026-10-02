@echo off
setlocal
cd /d "%~dp0"
echo QA Portal - unified main application
echo Activate your compatible isolated conda or Python environment first.
echo Open http://127.0.0.1:8050/login after the server starts.
echo Sign in with demo-admin / demo-only, then open QSL maintenance.
echo Direct page: http://127.0.0.1:8050/QA_portal/maintenance
echo Synthetic data only. No production login, database, or mail connection.
python -B app.py
if errorlevel 1 (
  echo Startup failed. Check the active environment and requirements-qa-portal.txt.
  pause
)
endlocal
