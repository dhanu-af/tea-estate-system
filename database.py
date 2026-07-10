import os
import re
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
PAYMENT_METHODS = ["Cash", "Cheque", "Bank Transfer"]
INVOICE_STATUSES = ["Unpaid", "Partially Paid", "Paid"]

# On Vercel there's no writable, persistent local disk for a SQLite file — every
# serverless invocation gets its own ephemeral filesystem. So in production we
# use Postgres (e.g. Vercel Postgres / Neon, connected via the dashboard's
# Storage tab) instead, detected by the presence of a connection-string env var.
# Locally (and in tests) none of these are set, so SQLite keeps working unchanged.
POSTGRES_URL = (
    os.environ.get("POSTGRES_URL")
    or os.environ.get("DATABASE_URL")
    or os.environ.get("POSTGRES_URL_NON_POOLING")
)
IS_POSTGRES = bool(POSTGRES_URL)

if IS_POSTGRES:
    import psycopg2
    import psycopg2.extras

    IntegrityError = psycopg2.IntegrityError

    _PLACEHOLDER_RE = re.compile(r"\?")

    class _PGResult:
        """Wraps a psycopg2 cursor so existing call sites written against sqlite3
        (fetchone/fetchall, and cursor.lastrowid straight after an INSERT) keep
        working unchanged, whether the app is running on SQLite or Postgres."""

        def __init__(self, cursor, lastrowid=None):
            self._cursor = cursor
            self.lastrowid = lastrowid

        def fetchone(self):
            return self._cursor.fetchone()

        def fetchall(self):
            return self._cursor.fetchall()

        def __iter__(self):
            return iter(self._cursor.fetchall())

    class PGConnection:
        """A sqlite3.Connection-alike over psycopg2. psycopg2 has no connection-level
        .execute() shortcut and uses %s placeholders instead of sqlite3's ?, so this
        adapts both, plus emulates cursor.lastrowid via an implicit RETURNING id."""

        def __init__(self, raw):
            self._raw = raw

        def execute(self, sql, params=()):
            pg_sql = _PLACEHOLDER_RE.sub("%s", sql)
            cursor = self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            lastrowid = None
            if pg_sql.strip().upper().startswith("INSERT") and "RETURNING" not in pg_sql.upper():
                cursor.execute(pg_sql.rstrip().rstrip(";") + " RETURNING id", params)
                row = cursor.fetchone()
                lastrowid = row["id"] if row else None
            else:
                cursor.execute(pg_sql, params)
            return _PGResult(cursor, lastrowid)

        def executescript(self, script):
            self._raw.cursor().execute(script)

        def commit(self):
            self._raw.commit()

        def close(self):
            self._raw.close()

else:
    IntegrityError = sqlite3.IntegrityError


def get_connection():
    """One connection per request, cached on Flask's `g`. Always released by
    close_connection() via teardown_appcontext, even if the request raises —
    so a crash can never leave the database locked for subsequent requests."""
    if "db" not in g:
        if IS_POSTGRES:
            raw = psycopg2.connect(POSTGRES_URL)
            raw.autocommit = True  # each statement commits immediately, so a
            # caught IntegrityError never poisons the rest of the request the
            # way an aborted multi-statement Postgres transaction would.
            g.db = PGConnection(raw)
        else:
            g.db = sqlite3.connect(DB_PATH)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_connection(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id SERIAL PRIMARY KEY,
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
    created_at TEXT DEFAULT (now()::text),
    updated_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS attendance (
    id SERIAL PRIMARY KEY,
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
    created_at TEXT DEFAULT (now()::text),
    updated_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (employee_id) REFERENCES employees (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS work_assignments (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    task_type TEXT NOT NULL,
    field_block TEXT,
    harvest_target REAL,
    actual_output REAL NOT NULL DEFAULT 0,
    productivity_score REAL,
    status TEXT NOT NULL DEFAULT 'Pending',
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (employee_id) REFERENCES employees (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS harvest_weighings (
    id SERIAL PRIMARY KEY,
    work_assignment_id INTEGER NOT NULL,
    gross_weight REAL NOT NULL,
    tare_weight REAL NOT NULL DEFAULT 0,
    net_weight REAL NOT NULL,
    weighed_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (work_assignment_id) REFERENCES work_assignments (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS daily_prices (
    id SERIAL PRIMARY KEY,
    date TEXT UNIQUE NOT NULL,
    price_per_kg REAL NOT NULL,
    updated_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS expenses (
    id SERIAL PRIMARY KEY,
    date TEXT NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT,
    created_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS salary_advances (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT,
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (employee_id) REFERENCES employees (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS factories (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    contact_person TEXT,
    phone_number TEXT,
    address TEXT,
    default_price_per_kg REAL,
    created_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS factory_deliveries (
    id SERIAL PRIMARY KEY,
    delivery_date TEXT NOT NULL,
    factory_id INTEGER NOT NULL,
    estate_weight REAL,
    factory_weight REAL NOT NULL,
    vehicle_number TEXT,
    driver_name TEXT,
    notes TEXT,
    invoice_id INTEGER,
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (factory_id) REFERENCES factories (id),
    FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id SERIAL PRIMARY KEY,
    invoice_number TEXT UNIQUE NOT NULL,
    factory_id INTEGER NOT NULL,
    invoice_date TEXT NOT NULL,
    price_per_kg REAL NOT NULL,
    total_weight REAL NOT NULL,
    total_amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'Unpaid',
    notes TEXT,
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (factory_id) REFERENCES factories (id)
);

CREATE TABLE IF NOT EXISTS invoice_payments (
    id SERIAL PRIMARY KEY,
    invoice_id INTEGER NOT NULL,
    payment_date TEXT NOT NULL,
    amount REAL NOT NULL,
    method TEXT NOT NULL,
    reference_number TEXT,
    note TEXT,
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE CASCADE
);
"""

# factory_deliveries.invoice_id references invoices, which is defined after it —
# fine in SQLite (FKs aren't checked at CREATE TABLE time), but Postgres validates
# the referenced table exists immediately, so invoices must be created first there.
_POSTGRES_SCHEMA = _POSTGRES_SCHEMA.replace(
    """CREATE TABLE IF NOT EXISTS factory_deliveries (
    id SERIAL PRIMARY KEY,
    delivery_date TEXT NOT NULL,
    factory_id INTEGER NOT NULL,
    estate_weight REAL,
    factory_weight REAL NOT NULL,
    vehicle_number TEXT,
    driver_name TEXT,
    notes TEXT,
    invoice_id INTEGER,
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (factory_id) REFERENCES factories (id),
    FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id SERIAL PRIMARY KEY,
    invoice_number TEXT UNIQUE NOT NULL,
    factory_id INTEGER NOT NULL,
    invoice_date TEXT NOT NULL,
    price_per_kg REAL NOT NULL,
    total_weight REAL NOT NULL,
    total_amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'Unpaid',
    notes TEXT,
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (factory_id) REFERENCES factories (id)
);""",
    """CREATE TABLE IF NOT EXISTS invoices (
    id SERIAL PRIMARY KEY,
    invoice_number TEXT UNIQUE NOT NULL,
    factory_id INTEGER NOT NULL,
    invoice_date TEXT NOT NULL,
    price_per_kg REAL NOT NULL,
    total_weight REAL NOT NULL,
    total_amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'Unpaid',
    notes TEXT,
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (factory_id) REFERENCES factories (id)
);

CREATE TABLE IF NOT EXISTS factory_deliveries (
    id SERIAL PRIMARY KEY,
    delivery_date TEXT NOT NULL,
    factory_id INTEGER NOT NULL,
    estate_weight REAL,
    factory_weight REAL NOT NULL,
    vehicle_number TEXT,
    driver_name TEXT,
    notes TEXT,
    invoice_id INTEGER,
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (factory_id) REFERENCES factories (id),
    FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE SET NULL
);""",
)


def init_db():
    conn = get_connection()

    if IS_POSTGRES:
        conn.executescript(_POSTGRES_SCHEMA)
        conn.commit()
        return

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

        CREATE TABLE IF NOT EXISTS factories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            contact_person TEXT,
            phone_number TEXT,
            address TEXT,
            default_price_per_kg REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS factory_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            delivery_date TEXT NOT NULL,
            factory_id INTEGER NOT NULL,
            estate_weight REAL,
            factory_weight REAL NOT NULL,
            vehicle_number TEXT,
            driver_name TEXT,
            notes TEXT,
            invoice_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (factory_id) REFERENCES factories (id),
            FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_number TEXT UNIQUE NOT NULL,
            factory_id INTEGER NOT NULL,
            invoice_date TEXT NOT NULL,
            price_per_kg REAL NOT NULL,
            total_weight REAL NOT NULL,
            total_amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'Unpaid',
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (factory_id) REFERENCES factories (id)
        );

        CREATE TABLE IF NOT EXISTS invoice_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            payment_date TEXT NOT NULL,
            amount REAL NOT NULL,
            method TEXT NOT NULL,
            reference_number TEXT,
            note TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE CASCADE
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
