@echo off
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo Environnement virtuel absent. Le creer avec :
  echo   py -3.12 -m venv venv
  echo   venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)
venv\Scripts\python.exe -m manager.superviseur
if errorlevel 1 pause
