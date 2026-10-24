@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -m venv .venv
  if errorlevel 1 goto :error
)
call ".venv\Scripts\activate.bat"
python -m pip install -r requirements.txt
if errorlevel 1 goto :error
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
exit /b 0
:error
echo Campus Connect could not start. Check Python installation and the error above.
pause
exit /b 1
