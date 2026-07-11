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
PAYMENT_METHODS = ["Cash", "Bank Transfer", "Cheque", "Other"]
INVOICE_STATUSES = ["Unpaid", "Partially Paid", "Paid"]
USER_ROLES = ["Admin", "Dhanu Operations"]
BANK_ACCOUNT_TYPES = ["Savings", "Current"]
PAYROLL_CYCLE_STATUSES = ["Unpaid", "Paid"]
PAYROLL_TRANSACTION_STATUSES = ["Pending", "Paid", "Failed"]
INVENTORY_CATEGORIES = ["Fertilizer", "Chemicals", "Packaging Materials", "Field Supplies", "Other"]
INVENTORY_TRANSACTION_TYPES = ["In", "Out"]
ASSET_STATUSES = ["Active", "Under Repair", "Disposed"]
DEPRECIATION_PERIOD_TYPES = ["Monthly", "Quarterly", "Yearly"]
LOGIN_STATUSES = ["Success", "Failed"]

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
    bank_name TEXT,
    bank_branch TEXT,
    bank_account_name TEXT,
    bank_account_number TEXT,
    bank_branch_code TEXT,
    bank_account_type TEXT,
    default_payment_method TEXT,
    required_daily_hours REAL,
    annual_leave_entitlement REAL,
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
    role TEXT NOT NULL DEFAULT 'Admin',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS daily_prices (
    id SERIAL PRIMARY KEY,
    date TEXT UNIQUE NOT NULL,
    price_per_kg REAL NOT NULL,
    created_by INTEGER,
    updated_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS expenses (
    id SERIAL PRIMARY KEY,
    date TEXT NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT,
    payment_method TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS salary_advances (
    id SERIAL PRIMARY KEY,
    employee_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT,
    payment_method TEXT,
    created_by INTEGER,
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

-- invoices must be created before factory_deliveries: Postgres validates a
-- foreign key's target table exists immediately (SQLite doesn't check until
-- the FK is actually enforced), so the dependency order matters here.
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
    created_by INTEGER,
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
    created_by INTEGER,
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (factory_id) REFERENCES factories (id),
    FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS invoice_payments (
    id SERIAL PRIMARY KEY,
    invoice_id INTEGER NOT NULL,
    payment_date TEXT NOT NULL,
    amount REAL NOT NULL,
    method TEXT NOT NULL,
    reference_number TEXT,
    note TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payroll_cycles (
    id SERIAL PRIMARY KEY,
    cycle_start TEXT UNIQUE NOT NULL,
    cycle_end TEXT NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Unpaid',
    paid_at TEXT,
    created_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS payroll_transactions (
    id SERIAL PRIMARY KEY,
    cycle_id INTEGER NOT NULL,
    employee_id INTEGER,
    employee_number TEXT NOT NULL,
    full_name TEXT NOT NULL,
    present_days INTEGER,
    total_hours REAL,
    hourly_rate REAL,
    hourly_pay REAL,
    total_output REAL,
    rate_per_kg REAL,
    bonus_kg REAL,
    bonus_pay REAL,
    harvest_payment REAL,
    leave_pay REAL,
    paid_leave_days INTEGER,
    no_pay_days INTEGER,
    total_pay REAL,
    advance_total REAL,
    employee_epf REAL,
    employer_epf REAL,
    employer_etf REAL,
    epf_etf_applicable INTEGER NOT NULL DEFAULT 0,
    net_pay REAL,
    payment_method TEXT,
    payment_date TEXT,
    payment_reference TEXT,
    payment_status TEXT NOT NULL DEFAULT 'Pending',
    bank_name TEXT,
    bank_branch TEXT,
    bank_account_name TEXT,
    bank_account_number TEXT,
    bank_branch_code TEXT,
    bank_account_type TEXT,
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (cycle_id) REFERENCES payroll_cycles (id) ON DELETE CASCADE,
    FOREIGN KEY (employee_id) REFERENCES employees (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS login_history (
    id SERIAL PRIMARY KEY,
    user_id INTEGER,
    username TEXT NOT NULL,
    ip_address TEXT,
    device_browser TEXT,
    status TEXT NOT NULL,
    login_at TEXT DEFAULT (now()::text),
    logout_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS inventory_items (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit TEXT NOT NULL,
    minimum_stock_level REAL NOT NULL DEFAULT 0,
    unit_cost REAL NOT NULL DEFAULT 0,
    created_by INTEGER,
    created_at TEXT DEFAULT (now()::text),
    updated_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS inventory_transactions (
    id SERIAL PRIMARY KEY,
    item_id INTEGER NOT NULL,
    transaction_type TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit_cost REAL,
    supplier TEXT,
    batch_number TEXT,
    expiry_date TEXT,
    transaction_date TEXT NOT NULL,
    note TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (item_id) REFERENCES inventory_items (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS assets (
    id SERIAL PRIMARY KEY,
    asset_code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    category TEXT,
    purchase_date TEXT,
    purchase_cost REAL,
    salvage_value REAL NOT NULL DEFAULT 0,
    supplier TEXT,
    serial_number TEXT,
    assigned_location TEXT,
    assigned_employee_id INTEGER,
    warranty_expiry TEXT,
    service_schedule TEXT,
    next_service_date TEXT,
    status TEXT NOT NULL DEFAULT 'Active',
    depreciation_period_months INTEGER,
    created_by INTEGER,
    created_at TEXT DEFAULT (now()::text),
    updated_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (assigned_employee_id) REFERENCES employees (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS asset_maintenance_log (
    id SERIAL PRIMARY KEY,
    asset_id INTEGER NOT NULL,
    maintenance_date TEXT NOT NULL,
    description TEXT NOT NULL,
    cost REAL,
    performed_by TEXT,
    next_service_date TEXT,
    created_by INTEGER,
    created_at TEXT DEFAULT (now()::text),
    FOREIGN KEY (asset_id) REFERENCES assets (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS prepaid_expenses (
    id SERIAL PRIMARY KEY,
    description TEXT NOT NULL,
    category TEXT,
    total_cost REAL NOT NULL,
    start_date TEXT NOT NULL,
    period_type TEXT NOT NULL DEFAULT 'Monthly',
    period_count INTEGER NOT NULL,
    created_by INTEGER,
    created_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS announcements (
    id SERIAL PRIMARY KEY,
    message TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER,
    created_at TEXT DEFAULT (now()::text)
);

CREATE TABLE IF NOT EXISTS weather_cache (
    id INTEGER PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


def init_db():
    conn = get_connection()

    if IS_POSTGRES:
        conn.executescript(_POSTGRES_SCHEMA)
        # Postgres supports IF NOT EXISTS on ADD COLUMN, so these are safe/idempotent
        # even against a database that already existed before this feature was added.
        conn.executescript(
            """
            ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'Admin';
            ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active INTEGER NOT NULL DEFAULT 1;
            ALTER TABLE daily_prices ADD COLUMN IF NOT EXISTS created_by INTEGER;
            ALTER TABLE expenses ADD COLUMN IF NOT EXISTS payment_method TEXT;
            ALTER TABLE expenses ADD COLUMN IF NOT EXISTS created_by INTEGER;
            ALTER TABLE salary_advances ADD COLUMN IF NOT EXISTS payment_method TEXT;
            ALTER TABLE salary_advances ADD COLUMN IF NOT EXISTS created_by INTEGER;
            ALTER TABLE invoices ADD COLUMN IF NOT EXISTS created_by INTEGER;
            ALTER TABLE factory_deliveries ADD COLUMN IF NOT EXISTS created_by INTEGER;
            ALTER TABLE invoice_payments ADD COLUMN IF NOT EXISTS created_by INTEGER;
            ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_name TEXT;
            ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_branch TEXT;
            ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_account_name TEXT;
            ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_account_number TEXT;
            ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_branch_code TEXT;
            ALTER TABLE employees ADD COLUMN IF NOT EXISTS bank_account_type TEXT;
            ALTER TABLE employees ADD COLUMN IF NOT EXISTS default_payment_method TEXT;
            ALTER TABLE employees ADD COLUMN IF NOT EXISTS required_daily_hours REAL;
            ALTER TABLE employees ADD COLUMN IF NOT EXISTS annual_leave_entitlement REAL;
            ALTER TABLE payroll_transactions ADD COLUMN IF NOT EXISTS bonus_pay REAL;
            ALTER TABLE payroll_transactions ADD COLUMN IF NOT EXISTS leave_pay REAL;
            ALTER TABLE payroll_transactions ADD COLUMN IF NOT EXISTS paid_leave_days INTEGER;
            ALTER TABLE payroll_transactions ADD COLUMN IF NOT EXISTS no_pay_days INTEGER;
            """
        )
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
            bank_name TEXT,
            bank_branch TEXT,
            bank_account_name TEXT,
            bank_account_number TEXT,
            bank_branch_code TEXT,
            bank_account_type TEXT,
            default_payment_method TEXT,
            required_daily_hours REAL,
            annual_leave_entitlement REAL,
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
            role TEXT NOT NULL DEFAULT 'Admin',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS daily_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            price_per_kg REAL NOT NULL,
            created_by INTEGER,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            payment_method TEXT,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS salary_advances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            payment_method TEXT,
            created_by INTEGER,
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
            created_by INTEGER,
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
            created_by INTEGER,
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
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS payroll_cycles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_start TEXT UNIQUE NOT NULL,
            cycle_end TEXT NOT NULL,
            due_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Unpaid',
            paid_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS payroll_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id INTEGER NOT NULL,
            employee_id INTEGER,
            employee_number TEXT NOT NULL,
            full_name TEXT NOT NULL,
            present_days INTEGER,
            total_hours REAL,
            hourly_rate REAL,
            hourly_pay REAL,
            total_output REAL,
            rate_per_kg REAL,
            bonus_kg REAL,
            bonus_pay REAL,
            harvest_payment REAL,
            leave_pay REAL,
            paid_leave_days INTEGER,
            no_pay_days INTEGER,
            total_pay REAL,
            advance_total REAL,
            employee_epf REAL,
            employer_epf REAL,
            employer_etf REAL,
            epf_etf_applicable INTEGER NOT NULL DEFAULT 0,
            net_pay REAL,
            payment_method TEXT,
            payment_date TEXT,
            payment_reference TEXT,
            payment_status TEXT NOT NULL DEFAULT 'Pending',
            bank_name TEXT,
            bank_branch TEXT,
            bank_account_name TEXT,
            bank_account_number TEXT,
            bank_branch_code TEXT,
            bank_account_type TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (cycle_id) REFERENCES payroll_cycles (id) ON DELETE CASCADE,
            FOREIGN KEY (employee_id) REFERENCES employees (id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS login_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT NOT NULL,
            ip_address TEXT,
            device_browser TEXT,
            status TEXT NOT NULL,
            login_at TEXT DEFAULT (datetime('now')),
            logout_at TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS inventory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit TEXT NOT NULL,
            minimum_stock_level REAL NOT NULL DEFAULT 0,
            unit_cost REAL NOT NULL DEFAULT 0,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS inventory_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            transaction_type TEXT NOT NULL,
            quantity REAL NOT NULL,
            unit_cost REAL,
            supplier TEXT,
            batch_number TEXT,
            expiry_date TEXT,
            transaction_date TEXT NOT NULL,
            note TEXT,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (item_id) REFERENCES inventory_items (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            purchase_date TEXT,
            purchase_cost REAL,
            salvage_value REAL NOT NULL DEFAULT 0,
            supplier TEXT,
            serial_number TEXT,
            assigned_location TEXT,
            assigned_employee_id INTEGER,
            warranty_expiry TEXT,
            service_schedule TEXT,
            next_service_date TEXT,
            status TEXT NOT NULL DEFAULT 'Active',
            depreciation_period_months INTEGER,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (assigned_employee_id) REFERENCES employees (id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS asset_maintenance_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            maintenance_date TEXT NOT NULL,
            description TEXT NOT NULL,
            cost REAL,
            performed_by TEXT,
            next_service_date TEXT,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (asset_id) REFERENCES assets (id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS prepaid_expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            category TEXT,
            total_cost REAL NOT NULL,
            start_date TEXT NOT NULL,
            period_type TEXT NOT NULL DEFAULT 'Monthly',
            period_count INTEGER NOT NULL,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS weather_cache (
            id INTEGER PRIMARY KEY,
            fetched_at TEXT NOT NULL,
            payload TEXT NOT NULL
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

    existing_user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
    if "role" not in existing_user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'Admin'")
    if "is_active" not in existing_user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")

    existing_daily_price_columns = {row["name"] for row in conn.execute("PRAGMA table_info(daily_prices)")}
    if "created_by" not in existing_daily_price_columns:
        conn.execute("ALTER TABLE daily_prices ADD COLUMN created_by INTEGER")

    existing_expense_columns = {row["name"] for row in conn.execute("PRAGMA table_info(expenses)")}
    if "payment_method" not in existing_expense_columns:
        conn.execute("ALTER TABLE expenses ADD COLUMN payment_method TEXT")
    if "created_by" not in existing_expense_columns:
        conn.execute("ALTER TABLE expenses ADD COLUMN created_by INTEGER")

    existing_advance_columns = {row["name"] for row in conn.execute("PRAGMA table_info(salary_advances)")}
    if "payment_method" not in existing_advance_columns:
        conn.execute("ALTER TABLE salary_advances ADD COLUMN payment_method TEXT")
    if "created_by" not in existing_advance_columns:
        conn.execute("ALTER TABLE salary_advances ADD COLUMN created_by INTEGER")

    existing_invoice_columns = {row["name"] for row in conn.execute("PRAGMA table_info(invoices)")}
    if "created_by" not in existing_invoice_columns:
        conn.execute("ALTER TABLE invoices ADD COLUMN created_by INTEGER")

    existing_delivery_columns = {row["name"] for row in conn.execute("PRAGMA table_info(factory_deliveries)")}
    if "created_by" not in existing_delivery_columns:
        conn.execute("ALTER TABLE factory_deliveries ADD COLUMN created_by INTEGER")

    existing_payment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(invoice_payments)")}
    if "created_by" not in existing_payment_columns:
        conn.execute("ALTER TABLE invoice_payments ADD COLUMN created_by INTEGER")

    existing_txn_columns = {row["name"] for row in conn.execute("PRAGMA table_info(payroll_transactions)")}
    if "bonus_pay" not in existing_txn_columns:
        conn.execute("ALTER TABLE payroll_transactions ADD COLUMN bonus_pay REAL")
    if "leave_pay" not in existing_txn_columns:
        conn.execute("ALTER TABLE payroll_transactions ADD COLUMN leave_pay REAL")
    if "paid_leave_days" not in existing_txn_columns:
        conn.execute("ALTER TABLE payroll_transactions ADD COLUMN paid_leave_days INTEGER")
    if "no_pay_days" not in existing_txn_columns:
        conn.execute("ALTER TABLE payroll_transactions ADD COLUMN no_pay_days INTEGER")

    for col, ddl in (
        ("bank_name", "ALTER TABLE employees ADD COLUMN bank_name TEXT"),
        ("bank_branch", "ALTER TABLE employees ADD COLUMN bank_branch TEXT"),
        ("bank_account_name", "ALTER TABLE employees ADD COLUMN bank_account_name TEXT"),
        ("bank_account_number", "ALTER TABLE employees ADD COLUMN bank_account_number TEXT"),
        ("bank_branch_code", "ALTER TABLE employees ADD COLUMN bank_branch_code TEXT"),
        ("bank_account_type", "ALTER TABLE employees ADD COLUMN bank_account_type TEXT"),
        ("default_payment_method", "ALTER TABLE employees ADD COLUMN default_payment_method TEXT"),
        ("required_daily_hours", "ALTER TABLE employees ADD COLUMN required_daily_hours REAL"),
        ("annual_leave_entitlement", "ALTER TABLE employees ADD COLUMN annual_leave_entitlement REAL"),
    ):
        if col not in existing_employee_columns:
            conn.execute(ddl)

    conn.commit()
    conn.close()
