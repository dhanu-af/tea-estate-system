import sqlite3
from pathlib import Path

from flask import g

DB_PATH = Path(__file__).parent / "tea_estate.db"

JOB_POSITIONS = ["Tea picker", "Supervisor", "Factory worker", "Driver", "Field officer"]
EMPLOYMENT_TYPES = ["Permanent", "Casual", "Contract"]
PAY_CYCLES = ["Daily", "Weekly", "Monthly"]
ATTENDANCE_STATUSES = ["Present", "Absent", "Leave", "Sick Leave", "Half Day"]
VERIFICATION_METHODS = ["QR code", "Fingerprint", "Face recognition", "Manual entry"]
TASK_TYPES = ["Plucking", "Fertilizer", "Pruning", "Weeding", "Spraying"]
WORK_STATUSES = ["Pending", "In Progress", "Completed"]
EXPENSE_CATEGORIES = ["Fertilizer", "Transport", "Fuel", "Food", "Others"]


def get_connection():
    """One connection per request, cached on Flask's `g`. Always released by
    close_connection() via teardown_appcontext, even if the request raises —
    so a crash can never leave the database locked for subsequent requests."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_connection(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_number TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            national_id TEXT,
            date_of_birth TEXT,
            gender TEXT,
            address TEXT,
            phone_number TEXT,
            emergency_contact TEXT,
            job_position TEXT,
            department TEXT,
            estate_division TEXT,
            start_date TEXT,
            employment_type TEXT,
            work_experience TEXT,
            skills_certificates TEXT,
            salary_type TEXT,
            pay_cycle TEXT,
            rate_per_kg REAL,
            hourly_rate REAL,
            over_target_commission_percent REAL,
            epf_etf_applicable INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            check_in TEXT,
            check_out TEXT,
            break_start TEXT,
            break_end TEXT,
            total_break_hours REAL,
            total_work_hours REAL,
            status TEXT NOT NULL,
            verification_method TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (employee_id) REFERENCES employees (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS work_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            task_type TEXT NOT NULL,
            field_block TEXT,
            harvest_target REAL,
            actual_output REAL NOT NULL DEFAULT 0,
            productivity_score REAL,
            status TEXT NOT NULL DEFAULT 'Pending',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (employee_id) REFERENCES employees (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS harvest_weighings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_assignment_id INTEGER NOT NULL,
            gross_weight REAL NOT NULL,
            tare_weight REAL NOT NULL DEFAULT 0,
            net_weight REAL NOT NULL,
            weighed_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (work_assignment_id) REFERENCES work_assignments (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS daily_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            price_per_kg REAL NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS salary_advances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (employee_id) REFERENCES employees (id) ON DELETE CASCADE
        );
        """
    )

    existing_employee_columns = {row["name"] for row in conn.execute("PRAGMA table_info(employees)")}
    if "rate_per_kg" not in existing_employee_columns:
        conn.execute("ALTER TABLE employees ADD COLUMN rate_per_kg REAL")
    if "hourly_rate" not in existing_employee_columns:
        conn.execute("ALTER TABLE employees ADD COLUMN hourly_rate REAL")
    if "over_target_commission_percent" not in existing_employee_columns:
        conn.execute("ALTER TABLE employees ADD COLUMN over_target_commission_percent REAL")
    if "epf_etf_applicable" not in existing_employee_columns:
        conn.execute("ALTER TABLE employees ADD COLUMN epf_etf_applicable INTEGER NOT NULL DEFAULT 0")
    if "updated_at" not in existing_employee_columns:
        conn.execute("ALTER TABLE employees ADD COLUMN updated_at TEXT DEFAULT (datetime('now'))")

    existing_attendance_columns = {row["name"] for row in conn.execute("PRAGMA table_info(attendance)")}
    if "updated_at" not in existing_attendance_columns:
        conn.execute("ALTER TABLE attendance ADD COLUMN updated_at TEXT DEFAULT (datetime('now'))")

    conn.commit()
    conn.close()


