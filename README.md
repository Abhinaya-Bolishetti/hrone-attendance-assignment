# Attendance Management API

A REST API built with FastAPI and MongoDB for managing employee attendance, punch-in and punch-out records, attendance regularization, and attendance analytics.

## Features

- Employee creation and listing with pagination.
- Attendance punch-in and punch-out.
- Attendance record regularization.
- Attendance date calculation for overnight shifts.
- Late arrival and overtime calculations.
- Employee monthly attendance analytics.
- Department attendance summaries.
- Late arrival leaderboard.
- Department attendance trends with daily data and moving averages.
- MongoDB aggregation pipelines for analytics.
- Query explain endpoints for inspecting database index usage.

## Tech Stack

- Python
- FastAPI
- MongoDB
- PyMongo
- Pydantic

## Prerequisites

Install the following before running the application:

- Python 3.10 or later
- MongoDB running locally or an accessible MongoDB instance
- Git

## Installation

Clone the repository:

```bash
git clone https://github.com/Abhinaya-Bolishetti/hrone-attendance-assignment.git
cd hrone-attendance-assignment
```

Create and activate a virtual environment.

**Windows PowerShell:**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

Create a `.env` file in the project root with your MongoDB configuration:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
```

Make sure MongoDB is running before starting the application.

Do not commit `.env` or any database credentials to GitHub.

## Run the Application

Start the FastAPI server:

```bash
python -m uvicorn app.main:app --reload --port 8000
```

The application will be available at:

- API: http://127.0.0.1:8000
- Interactive API documentation: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## API Usage

Use the Swagger UI at `/docs` to explore the available endpoints, provide request parameters, and execute API calls.

The API includes operations for:

- Managing employees
- Recording attendance
- Punching in and out
- Regularizing attendance
- Retrieving attendance records
- Viewing monthly and department analytics
- Inspecting query execution plans

## Database Indexes

The application creates indexes to support common employee and attendance queries, including:

- Unique employee code.
- Unique employee attendance date.
- Attendance date and employee code.
- Employee code, date, and attendance status.
- Department and employee joining date.

The explain endpoints can be used to inspect MongoDB query plans and index usage.

## Project Structure

```text
candidate_kit/
├── app/
│   └── main.py
├── .env
├── .gitignore
├── requirements.txt
├── README.md
├── REVIEW.md
└── DECISIONS.md
```

## Documentation

- `REVIEW.md` — Defects identified in the starter application, their fixes, and verification notes.
- `DECISIONS.md` — Technical decisions and implementation trade-offs.

## Author

Abhinaya Bolishetti
