@echo off
REM PollPulse first-time setup for Windows. Run from the project folder.
REM Requires Python 3.12+ on PATH and MongoDB Community Server running.

where py >nul 2>nul && (set PY=py -3) || (set PY=python)
if not exist venv (
    echo Creating virtual environment...
    %PY% -m venv venv || goto :error
)
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || goto :error

if not exist .env (
    copy .env.example .env >nul
    for /f "delims=" %%k in ('python -c "import secrets; print(secrets.token_urlsafe(50))"') do set KEY=%%k
    powershell -NoProfile -Command "(Get-Content .env) -replace 'replace-me-with-a-long-random-value', $env:KEY | Set-Content .env"
    echo Created .env with a new random secret key.
)

python manage.py migrate || goto :error
python manage.py init_mongo || goto :error
echo.
echo Setup complete. Next steps:
echo   1. Create your administrator:   python manage.py createsuperuser
echo   2. Optional sample data:         python manage.py seed_demo
echo   3. Start the server:             run_windows.bat
goto :eof

:error
echo.
echo Setup stopped because of the error above. See README.md, section "Troubleshooting".
exit /b 1
