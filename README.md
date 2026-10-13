# PollPulse — Survey Intelligence and Analytics Platform

PollPulse is a web-based survey platform built using Django and MongoDB. It helps users create surveys, collect responses, and analyze results through interactive charts and statistics.

## Features

- **12 question types** for flexible survey creation.
- **Survey management** to create, edit, publish, close, and archive surveys.
- **Analytics dashboard** with response statistics, charts, and survey comparisons.
- **Admin panel** for user management, survey moderation, and audit logs.
- **Response validation** and duplicate-submission protection.
- **CSV exports** and printable reports.
- **Role-based access** for creators, respondents, and administrators.

## Technology Stack

- Python and Django
- Django REST Framework
- MongoDB and PyMongo
- SQLite for user authentication and sessions
- HTML, CSS, JavaScript, Bootstrap, and Chart.js

## Installation

1. Install Python 3.12 or 3.13 and MongoDB Community Server.
2. Extract the project and open its folder in the terminal.
3. Run `setup_windows.bat`.
4. Create an administrator using `python manage.py createsuperuser`.
5. Start the application using `run_windows.bat`.
6. Open http://127.0.0.1:8000/


## Documentation

- [API Documentation](docs/API.md)
- [Deployment Guide](docs/DEPLOYMENT.md)


## Author

**Rutuja Yashwant Divate**  
MCA Project — PollPulse
