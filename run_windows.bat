@echo off
REM Start PollPulse on http://127.0.0.1:8000 (run setup_windows.bat once first)
if not exist venv\Scripts\activate.bat (
    echo Run setup_windows.bat first.
    exit /b 1
)
call venv\Scripts\activate.bat
python manage.py migrate --noinput
python manage.py runserver
