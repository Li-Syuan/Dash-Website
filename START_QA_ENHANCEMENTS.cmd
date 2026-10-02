@echo off
setlocal
cd /d "%~dp0"
echo QA Portal offline enhancements
 echo Activate your compatible conda environment before running this file.
echo Open http://127.0.0.1:8051/QA_portal/ after the server starts.
echo Synthetic data only; not a production login.
python -B qa_portal_demo.py
if errorlevel 1 (
  echo Startup failed. Check Python environment and requirements-qa-portal.txt.
  pause
)
endlocal
