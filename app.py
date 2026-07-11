import calendar
import io
import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import qrcode
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
from werkzeug.security import generate_password_hash, check_password_hash

from database import (
    get_connection,
    close_connection,
    init_db,
    IntegrityError,
    JOB_POSITIONS,
    EMPLOYMENT_TYPES,
    PAY_CYCLES,
    ATTENDANCE_STATUSES,
    VERIFICATION_METHODS,
    TASK_TYPES,
    WORK_STATUSES,
    EXPENSE_CATEGORIES,
    PAYMENT_METHODS,
    INVOICE_STATUSES,
    USER_ROLES,
    BANK_ACCOUNT_TYPES,
    PAYROLL_TRANSACTION_STATUSES,
    INVENTORY_CATEGORIES,
    INVENTORY_TRANSACTION_TYPES,
    ASSET_STATUSES,
    DEPRECIATION_PERIOD_TYPES,
    LOGIN_STATUSES,
)
from utils import (
    compute_attendance_hours,
    compute_productivity,
    next_work_status,
    compute_harvest_pay,
    compute_epf_etf,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "tea-estate-dev-secret")
app.teardown_appcontext(close_connection)

# Shown on the printed Finance & Factory Statement header — edit these to your
# actual registered details; there's no company-profile settings page (yet).
COMPANY_NAME = "DKNS Tea Lands"
COMPANY_TAGLINE = "Tea Estate Management System"
COMPANY_ADDRESS = os.environ.get("COMPANY_ADDRESS", "Tea Estate Office, Sri Lanka")
COMPANY_PHONE = os.environ.get("COMPANY_PHONE", "+94 00 000 0000")
COMPANY_EMAIL = os.environ.get("COMPANY_EMAIL", "info@dkns.ai")


def _now_text():
    """Portable 'now' timestamp computed in Python rather than SQL, since
    SQLite's datetime('now') isn't valid syntax on Postgres."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")


# Create tables on cold start. Safe to call repeatedly (CREATE TABLE IF NOT EXISTS) —
# needed because on Vercel this module is only ever imported, never run as __main__.
with app.app_context():
    init_db()

PUBLIC_ENDPOINTS = {"checkin", "employee_badge", "login", "setup", "static"}

# Endpoints whose name starts with one of these prefixes are Admin-only — the
# "Dhanu Operations" role can use everything else (Dashboard, Employees,
# Attendance, Work & Harvest) but is redirected away from these.
ADMIN_ONLY_PREFIXES = (
    "payroll", "income", "finance", "factory", "delivery", "invoice", "user",
    "asset", "prepaid", "depreciation", "announcement",
)

# The permanent Super Admin / System Owner account. This one specific username
# is a protected system account: it can never be disabled, deleted, demoted,
# or renamed by anyone (including other Admins, and including itself) through
# the app, and it is the only account that can reach User Management at all
# once it exists — see _protected_account_exists() below for the bootstrap
# exception that keeps a brand-new install from locking itself out.
SUPER_ADMIN_USERNAME = "DKNS"

# Endpoints that create/edit/disable/delete users or change passwords — once the
# protected System Owner account exists, only that account may reach these.
# user_login_history is deliberately excluded: it's an audit trail, not user
# management, so any Admin may still view it.
SUPER_ADMIN_ONLY_ENDPOINTS = {
    "user_list", "user_new", "user_edit", "user_change_password",
    "user_toggle_active", "user_delete",
}


def _is_protected_account(user):
    return bool(user) and user["username"].strip().lower() == SUPER_ADMIN_USERNAME.lower()


def _protected_account_exists(conn):
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE LOWER(username) = LOWER(?)", (SUPER_ADMIN_USERNAME,)
    ).fetchone()
    return row["c"] > 0


@app.before_request
def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return

    conn = get_connection()
    has_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] > 0

    if not has_users:
        return redirect(url_for("setup"))
    if not session.get("user_id"):
        return redirect(url_for("login"))

    # Re-check the account fresh from the DB (not just at login) so disabling a
    # user takes effect immediately, even if they already have a live session.
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
    if not user or not user["is_active"]:
        session.clear()
        flash("This account has been disabled. Contact your administrator.", "error")
        return redirect(url_for("login"))
    session["role"] = user["role"]
    session["is_super_admin"] = _is_protected_account(user)

    if user["role"] != "Admin" and request.endpoint.startswith(ADMIN_ONLY_PREFIXES):
        flash("Your account doesn't have access to that section.", "error")
        return redirect(url_for("dashboard"))

    if (
        request.endpoint in SUPER_ADMIN_ONLY_ENDPOINTS
        and not session["is_super_admin"]
        and _protected_account_exists(conn)
    ):
        flash(f"Only the {SUPER_ADMIN_USERNAME} (System Owner) account can manage users.", "error")
        return redirect(url_for("dashboard"))

EMPLOYEE_FIELDS = [
    "full_name",
    "national_id",
    "date_of_birth",
    "gender",
    "address",
    "phone_number",
    "emergency_contact",
    "job_position",
    "department",
    "estate_division",
    "start_date",
    "employment_type",
    "work_experience",
    "skills_certificates",
    "salary_type",
    "pay_cycle",
    "rate_per_kg",
    "hourly_rate",
    "over_target_commission_percent",
    "epf_etf_applicable",
    "bank_name",
    "bank_branch",
    "bank_account_name",
    "bank_account_number",
    "bank_branch_code",
    "bank_account_type",
    "default_payment_method",
    "required_daily_hours",
    "annual_leave_entitlement",
]


@app.context_processor
def inject_lookups():
    return dict(
        job_positions=JOB_POSITIONS,
        employment_types=EMPLOYMENT_TYPES,
        pay_cycles=PAY_CYCLES,
        attendance_statuses=ATTENDANCE_STATUSES,
        verification_methods=VERIFICATION_METHODS,
        task_types=TASK_TYPES,
        work_statuses=WORK_STATUSES,
        expense_categories=EXPENSE_CATEGORIES,
        payment_methods=PAYMENT_METHODS,
        invoice_statuses=INVOICE_STATUSES,
        user_roles=USER_ROLES,
        bank_account_types=BANK_ACCOUNT_TYPES,
        payroll_transaction_statuses=PAYROLL_TRANSACTION_STATUSES,
        inventory_categories=INVENTORY_CATEGORIES,
        inventory_transaction_types=INVENTORY_TRANSACTION_TYPES,
        asset_statuses=ASSET_STATUSES,
        depreciation_period_types=DEPRECIATION_PERIOD_TYPES,
        is_protected_account=_is_protected_account,
        super_admin_username=SUPER_ADMIN_USERNAME,
    )


@app.route("/")
def dashboard():
    conn = get_connection()
    employee_count = conn.execute("SELECT COUNT(*) AS c FROM employees").fetchone()["c"]
    today = request.args.get("today")
    from datetime import date

    today = today or date.today().isoformat()

    today_rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM attendance WHERE date = ? GROUP BY status",
        (today,),
    ).fetchall()
    today_summary = {row["status"]: row["c"] for row in today_rows}
    marked_today = sum(today_summary.values())

    recent_employees = conn.execute(
        "SELECT * FROM employees ORDER BY id DESC LIMIT 5"
    ).fetchall()
    recent_attendance = conn.execute(
        """SELECT a.*, e.full_name, e.employee_number FROM attendance a
           JOIN employees e ON e.id = a.employee_id
           ORDER BY a.id DESC LIMIT 5"""
    ).fetchall()

    harvest_today = conn.execute(
        "SELECT COALESCE(SUM(actual_output), 0) AS total FROM work_assignments WHERE date = ?",
        (today,),
    ).fetchone()["total"]
    recent_assignments = conn.execute(
        """SELECT w.*, e.full_name, e.employee_number FROM work_assignments w
           JOIN employees e ON e.id = w.employee_id
           ORDER BY w.id DESC LIMIT 5"""
    ).fetchall()

    from datetime import timedelta

    anchor = date.fromisoformat(today)
    trend_labels, trend_harvest, trend_present = [], [], []
    for i in range(6, -1, -1):
        d = (anchor - timedelta(days=i)).isoformat()
        trend_labels.append(d[5:])  # MM-DD, keeps the chart x-axis compact
        h = conn.execute(
            "SELECT COALESCE(SUM(actual_output), 0) AS total FROM work_assignments WHERE date = ?", (d,)
        ).fetchone()["total"]
        trend_harvest.append(round(h, 2))
        p = conn.execute(
            "SELECT COUNT(*) AS c FROM attendance WHERE date = ? AND status = 'Present'", (d,)
        ).fetchone()["c"]
        trend_present.append(p)

    tea_price = _latest_tea_price(conn)
    weather = _fetch_weather_forecast()
    upcoming_birthdays = _upcoming_birthdays(conn, within_days=30)
    announcements = conn.execute(
        """SELECT a.*, u.username AS created_by_name FROM announcements a
           LEFT JOIN users u ON u.id = a.created_by
           WHERE a.is_active = 1 ORDER BY a.id DESC LIMIT 10"""
    ).fetchall()

    return render_template(
        "dashboard.html",
        employee_count=employee_count,
        today=today,
        today_summary=today_summary,
        marked_today=marked_today,
        recent_employees=recent_employees,
        recent_attendance=recent_attendance,
        harvest_today=harvest_today,
        recent_assignments=recent_assignments,
        trend_labels=trend_labels,
        trend_harvest=trend_harvest,
        trend_present=trend_present,
        tea_price=tea_price,
        weather=weather,
        upcoming_birthdays=upcoming_birthdays,
        announcements=announcements,
    )


# ---------- Auth ----------


@app.route("/setup", methods=["GET", "POST"])
def setup():
    conn = get_connection()
    has_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] > 0
    if has_users:
        return redirect(url_for("login"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("setup.html")
        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("setup.html")

        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, is_active) VALUES (?, ?, 'Admin', 1)",
                (username, generate_password_hash(password)),
            )
            conn.commit()
        except IntegrityError:
            flash("That username is already taken.", "error")
            return render_template("setup.html")

        flash("Admin account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("setup.html")


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "—"


def _device_browser():
    ua = request.user_agent
    platform = (ua.platform or "").title()
    browser = (ua.browser or "").title()
    if platform or browser:
        return " · ".join(p for p in (platform, browser) if p)
    return (ua.string or "—")[:120]


def _record_login_attempt(conn, username, status, user_id=None):
    conn.execute(
        "INSERT INTO login_history (user_id, username, ip_address, device_browser, status) VALUES (?, ?, ?, ?, ?)",
        (user_id, username, _client_ip(), _device_browser(), status),
    )
    conn.commit()


@app.route("/login", methods=["GET", "POST"])
def login():
    conn = get_connection()
    has_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"] > 0
    if not has_users:
        return redirect(url_for("setup"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            if not user["is_active"]:
                _record_login_attempt(conn, username, "Failed", user_id=user["id"])
                flash("This account has been disabled. Contact your administrator.", "error")
                return render_template("login.html")
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["is_super_admin"] = _is_protected_account(user)
            _record_login_attempt(conn, username, "Success", user_id=user["id"])
            flash(f"Welcome, {user['username']}.", "success")
            return redirect(url_for("dashboard"))

        _record_login_attempt(conn, username or "(blank)", "Failed", user_id=user["id"] if user else None)
        flash("Invalid username or password.", "error")
        return render_template("login.html")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    conn = get_connection()
    user_id = session.get("user_id")
    if user_id:
        conn.execute(
            """UPDATE login_history SET logout_at = ? WHERE id = (
                   SELECT id FROM login_history WHERE user_id = ? AND status = 'Success' AND logout_at IS NULL
                   ORDER BY id DESC LIMIT 1
               )""",
            (_now_text(), user_id),
        )
        conn.commit()
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


# ---------- User Management ----------


def _active_admin_count(conn, exclude_id=None):
    if exclude_id is None:
        return conn.execute("SELECT COUNT(*) AS c FROM users WHERE role = 'Admin' AND is_active = 1").fetchone()["c"]
    return conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE role = 'Admin' AND is_active = 1 AND id != ?", (exclude_id,)
    ).fetchone()["c"]


@app.route("/users")
def user_list():
    conn = get_connection()
    users = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    return render_template("users.html", users=users)


@app.route("/users/new", methods=["GET", "POST"])
def user_new():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        role = request.form.get("role", "").strip()

        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("user_form.html", user=request.form, mode="new")
        if password != confirm_password:
            flash("Passwords do not match.", "error")
            return render_template("user_form.html", user=request.form, mode="new")
        if role not in USER_ROLES:
            flash("Select a valid role.", "error")
            return render_template("user_form.html", user=request.form, mode="new")

        conn = get_connection()
        if username.lower() == SUPER_ADMIN_USERNAME.lower() and _protected_account_exists(conn):
            flash("Access Denied – Protected System Account. That username is reserved.", "error")
            return render_template("user_form.html", user=request.form, mode="new")

        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, is_active) VALUES (?, ?, ?, 1)",
                (username, generate_password_hash(password), role),
            )
            conn.commit()
        except IntegrityError:
            flash("That username is already taken.", "error")
            return render_template("user_form.html", user=request.form, mode="new")

        flash(f"User {username} created.", "success")
        return redirect(url_for("user_list"))

    return render_template("user_form.html", user={}, mode="new")


@app.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
def user_edit(user_id):
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("user_list"))

    protected = _is_protected_account(user)

    if request.method == "POST":
        if protected:
            flash(
                f"Access Denied – Protected System Account. {SUPER_ADMIN_USERNAME}'s username and role are locked.",
                "error",
            )
            return redirect(url_for("user_list"))

        username = request.form.get("username", "").strip()
        role = request.form.get("role", "").strip()

        if not username:
            flash("Username is required.", "error")
            merged = dict(user)
            merged.update(request.form)
            return render_template("user_form.html", user=merged, mode="edit", user_id=user_id)
        if username.lower() == SUPER_ADMIN_USERNAME.lower():
            flash("Access Denied – Protected System Account. That username is reserved.", "error")
            merged = dict(user)
            merged.update(request.form)
            return render_template("user_form.html", user=merged, mode="edit", user_id=user_id)
        if role not in USER_ROLES:
            flash("Select a valid role.", "error")
            merged = dict(user)
            merged.update(request.form)
            return render_template("user_form.html", user=merged, mode="edit", user_id=user_id)

        if user["role"] == "Admin" and role != "Admin" and _active_admin_count(conn, exclude_id=user_id) == 0:
            flash("Can't change this account's role — it's the last active Admin.", "error")
            return redirect(url_for("user_list"))

        try:
            conn.execute("UPDATE users SET username = ?, role = ? WHERE id = ?", (username, role, user_id))
            conn.commit()
        except IntegrityError:
            flash("That username is already taken.", "error")
            merged = dict(user)
            merged.update(request.form)
            return render_template("user_form.html", user=merged, mode="edit", user_id=user_id)

        flash("User updated.", "success")
        return redirect(url_for("user_list"))

    return render_template("user_form.html", user=dict(user), mode="edit", user_id=user_id, protected=protected)


@app.route("/users/<int:user_id>/password", methods=["POST"])
def user_change_password(user_id):
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("user_list"))

    new_password = request.form.get("new_password", "")
    confirm_new_password = request.form.get("confirm_new_password", "")

    if not new_password:
        flash("Enter a new password.", "error")
        return redirect(url_for("user_edit", user_id=user_id))
    if new_password != confirm_new_password:
        flash("Passwords do not match.", "error")
        return redirect(url_for("user_edit", user_id=user_id))

    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), user_id)
    )
    conn.commit()
    flash(f"Password updated for {user['username']}.", "success")
    return redirect(url_for("user_edit", user_id=user_id))


@app.route("/users/<int:user_id>/toggle", methods=["POST"])
def user_toggle_active(user_id):
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("user_list"))

    if _is_protected_account(user):
        flash(f"Access Denied – Protected System Account. {SUPER_ADMIN_USERNAME} cannot be disabled.", "error")
        return redirect(url_for("user_list"))

    if user_id == session.get("user_id"):
        flash("You can't disable your own account. Ask another admin to do it.", "error")
        return redirect(url_for("user_list"))

    if user["is_active"] and user["role"] == "Admin" and _active_admin_count(conn, exclude_id=user_id) == 0:
        flash("Can't disable the last active Admin account.", "error")
        return redirect(url_for("user_list"))

    new_status = 0 if user["is_active"] else 1
    conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_status, user_id))
    conn.commit()
    flash(f"{user['username']} {'enabled' if new_status else 'disabled'}.", "success")
    return redirect(url_for("user_list"))


@app.route("/users/<int:user_id>/delete", methods=["POST"])
def user_delete(user_id):
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("user_list"))

    if _is_protected_account(user):
        flash(f"Access Denied – Protected System Account. {SUPER_ADMIN_USERNAME} cannot be deleted.", "error")
        return redirect(url_for("user_list"))

    if user_id == session.get("user_id"):
        flash("You can't delete your own account while logged in.", "error")
        return redirect(url_for("user_list"))

    if user["role"] == "Admin" and user["is_active"] and _active_admin_count(conn, exclude_id=user_id) == 0:
        flash("Can't delete the last active Admin account.", "error")
        return redirect(url_for("user_list"))

    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    flash(f"User {user['username']} removed.", "success")
    return redirect(url_for("user_list"))


@app.route("/users/login-history")
def user_login_history():
    conn = get_connection()
    search = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    date_filter = request.args.get("date", "").strip()

    query = "SELECT * FROM login_history WHERE 1=1"
    params = []
    if search:
        query += " AND username LIKE ?"
        params.append(f"%{search}%")
    if status_filter in LOGIN_STATUSES:
        query += " AND status = ?"
        params.append(status_filter)
    if date_filter:
        query += " AND login_at LIKE ?"
        params.append(f"{date_filter}%")
    query += " ORDER BY id DESC LIMIT 500"

    records = conn.execute(query, params).fetchall()
    return render_template(
        "login_history.html", records=records, search=search, status_filter=status_filter, date_filter=date_filter
    )


# ---------- Employees ----------


@app.route("/employees")
def employee_list():
    q = request.args.get("q", "").strip()
    conn = get_connection()
    if q:
        rows = conn.execute(
            """SELECT * FROM employees
               WHERE full_name LIKE ? OR employee_number LIKE ? OR job_position LIKE ?
               ORDER BY id DESC""",
            (f"%{q}%", f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM employees ORDER BY id DESC").fetchall()
    upcoming_birthdays = _upcoming_birthdays(conn, within_days=30)
    return render_template("employees.html", employees=rows, q=q, upcoming_birthdays=upcoming_birthdays)


@app.route("/employees/new", methods=["GET", "POST"])
def employee_new():
    if request.method == "POST":
        conn = get_connection()
        data = {f: request.form.get(f, "").strip() for f in EMPLOYEE_FIELDS}
        data["rate_per_kg"] = float(data["rate_per_kg"]) if data["rate_per_kg"] else None
        data["hourly_rate"] = float(data["hourly_rate"]) if data["hourly_rate"] else None
        data["over_target_commission_percent"] = (
            float(data["over_target_commission_percent"]) if data["over_target_commission_percent"] else None
        )
        data["epf_etf_applicable"] = 1 if data["epf_etf_applicable"] else 0
        data["required_daily_hours"] = float(data["required_daily_hours"]) if data["required_daily_hours"] else None
        data["annual_leave_entitlement"] = (
            float(data["annual_leave_entitlement"]) if data["annual_leave_entitlement"] else None
        )
        if not data["full_name"]:
            flash("Full name is required.", "error")
            return render_template("employee_form.html", employee=data, mode="new")

        cursor = conn.execute(
            f"""INSERT INTO employees (employee_number, {', '.join(EMPLOYEE_FIELDS)})
                VALUES ('', {', '.join(['?'] * len(EMPLOYEE_FIELDS))})""",
            [data[f] for f in EMPLOYEE_FIELDS],
        )
        emp_number = f"EMP-{cursor.lastrowid:04d}"
        conn.execute("UPDATE employees SET employee_number = ? WHERE id = ?", (emp_number, cursor.lastrowid))
        conn.commit()
        flash(f"Employee {emp_number} registered successfully.", "success")
        return redirect(url_for("employee_list"))

    return render_template("employee_form.html", employee={}, mode="new")


@app.route("/employees/<int:employee_id>/edit", methods=["GET", "POST"])
def employee_edit(employee_id):
    conn = get_connection()
    employee = conn.execute(
        "SELECT * FROM employees WHERE id = ?", (employee_id,)
    ).fetchone()
    if not employee:
        flash("Employee not found.", "error")
        return redirect(url_for("employee_list"))

    if request.method == "POST":
        expected_updated_at = request.form.get("expected_updated_at", "")
        if employee["updated_at"] and expected_updated_at and employee["updated_at"] != expected_updated_at:
            flash(
                "Someone else updated this employee while you were editing. "
                "Your changes were not saved — please review the current values and try again.",
                "error",
            )
            return redirect(url_for("employee_edit", employee_id=employee_id))

        data = {f: request.form.get(f, "").strip() for f in EMPLOYEE_FIELDS}
        data["rate_per_kg"] = float(data["rate_per_kg"]) if data["rate_per_kg"] else None
        data["hourly_rate"] = float(data["hourly_rate"]) if data["hourly_rate"] else None
        data["over_target_commission_percent"] = (
            float(data["over_target_commission_percent"]) if data["over_target_commission_percent"] else None
        )
        data["epf_etf_applicable"] = 1 if data["epf_etf_applicable"] else 0
        data["required_daily_hours"] = float(data["required_daily_hours"]) if data["required_daily_hours"] else None
        data["annual_leave_entitlement"] = (
            float(data["annual_leave_entitlement"]) if data["annual_leave_entitlement"] else None
        )
        if not data["full_name"]:
            flash("Full name is required.", "error")
            merged = dict(employee)
            merged.update(data)
            return render_template("employee_form.html", employee=merged, mode="edit", employee_id=employee_id)

        set_clause = ", ".join(f"{f} = ?" for f in EMPLOYEE_FIELDS)
        conn.execute(
            f"UPDATE employees SET {set_clause}, updated_at = ? WHERE id = ?",
            [data[f] for f in EMPLOYEE_FIELDS] + [_now_text(), employee_id],
        )
        conn.commit()
        flash("Employee updated.", "success")
        return redirect(url_for("employee_list"))

    recent_work = conn.execute(
        """SELECT * FROM work_assignments WHERE employee_id = ?
           ORDER BY date DESC, id DESC LIMIT 5""",
        (employee_id,),
    ).fetchall()
    birthday_info = _next_birthday_info(employee["date_of_birth"])
    return render_template(
        "employee_form.html",
        employee=dict(employee),
        mode="edit",
        employee_id=employee_id,
        recent_work=recent_work,
        birthday_info=birthday_info,
    )


@app.route("/employees/<int:employee_id>/delete", methods=["POST"])
def employee_delete(employee_id):
    conn = get_connection()
    conn.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
    conn.commit()
    flash("Employee removed.", "success")
    return redirect(url_for("employee_list"))


@app.route("/employees/<int:employee_id>/badge.png")
def employee_badge(employee_id):
    conn = get_connection()
    employee = conn.execute("SELECT employee_number FROM employees WHERE id = ?", (employee_id,)).fetchone()
    if not employee:
        flash("Employee not found.", "error")
        return redirect(url_for("employee_list"))

    checkin_url = url_for("checkin", employee_number=employee["employee_number"], _external=True)
    img = qrcode.make(checkin_url)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png")


# ---------- QR Check-in (Module 5, Step 1) ----------


@app.route("/checkin/<employee_number>")
def checkin(employee_number):
    conn = get_connection()
    employee = conn.execute(
        "SELECT * FROM employees WHERE employee_number = ?", (employee_number,)
    ).fetchone()
    if not employee:
        return render_template("checkin_result.html", employee=None), 404

    today = datetime.now().date().isoformat()
    now_time = datetime.now().strftime("%H:%M")

    record = conn.execute(
        "SELECT * FROM attendance WHERE employee_id = ? AND date = ?", (employee["id"], today)
    ).fetchone()

    if record is None:
        conn.execute(
            """INSERT INTO attendance (employee_id, date, check_in, status, verification_method)
               VALUES (?, ?, ?, 'Present', 'QR code')""",
            (employee["id"], today, now_time),
        )
        conn.commit()
        action = "checked_in"
    elif record["check_out"] is None:
        total_break, net_work = compute_attendance_hours(
            record["check_in"], now_time, record["break_start"], record["break_end"]
        )
        conn.execute(
            "UPDATE attendance SET check_out = ?, total_break_hours = ?, total_work_hours = ?, updated_at = ? WHERE id = ?",
            (now_time, total_break, net_work, _now_text(), record["id"]),
        )
        conn.commit()
        action = "checked_out"
    else:
        action = "already_done"

    todays_task = conn.execute(
        "SELECT * FROM work_assignments WHERE employee_id = ? AND date = ?", (employee["id"], today)
    ).fetchone()

    return render_template(
        "checkin_result.html",
        employee=employee,
        action=action,
        now_time=now_time,
        todays_task=todays_task,
    )


# ---------- Attendance ----------


def _compute_attendance_summary(conn, date_from, date_to):
    """Per-employee attendance & leave summary for a date range: days worked,
    absent, paid leave, No-Pay (LOP) days, attendance %, hours, overtime, and
    leave balance. Sick Leave is always paid; annual Leave is paid only up to
    the employee's entitlement for the year — once that's used up, further
    Leave days count as No-Pay (LOP), the same as Absent days."""
    employees = conn.execute(
        """SELECT id, employee_number, full_name, required_daily_hours, annual_leave_entitlement
           FROM employees ORDER BY full_name"""
    ).fetchall()

    year_start = f"{date_from[:4]}-01-01"

    summary = []
    for e in employees:
        records = conn.execute(
            "SELECT date, status, total_work_hours FROM attendance WHERE employee_id = ? AND date BETWEEN ? AND ? ORDER BY date",
            (e["id"], date_from, date_to),
        ).fetchall()

        days_worked = sum(1 for r in records if r["status"] in ("Present", "Half Day"))
        days_absent = sum(1 for r in records if r["status"] == "Absent")
        sick_leave_days = sum(1 for r in records if r["status"] == "Sick Leave")
        total_recorded = len(records)
        total_hours = round(sum(r["total_work_hours"] or 0 for r in records), 2)

        entitlement = e["annual_leave_entitlement"]
        leave_this_year = conn.execute(
            "SELECT date FROM attendance WHERE employee_id = ? AND status = 'Leave' AND date BETWEEN ? AND ? ORDER BY date",
            (e["id"], year_start, date_to),
        ).fetchall()
        paid_leave_in_period = 0
        unpaid_leave_in_period = 0
        running = 0
        for r in leave_this_year:
            is_paid = entitlement is not None and running < entitlement
            if date_from <= r["date"] <= date_to:
                if is_paid:
                    paid_leave_in_period += 1
                else:
                    unpaid_leave_in_period += 1
            running += 1
        leave_balance = round(max(0, entitlement - running), 2) if entitlement is not None else None

        paid_leave_days = paid_leave_in_period + sick_leave_days
        no_pay_days = days_absent + unpaid_leave_in_period
        attendance_pct = round(days_worked / total_recorded * 100, 1) if total_recorded else None

        overtime_hours = None
        incomplete_hours = False
        if e["required_daily_hours"]:
            overtime_hours = round(
                sum(
                    max(0, (r["total_work_hours"] or 0) - e["required_daily_hours"])
                    for r in records
                    if r["status"] in ("Present", "Half Day")
                ),
                2,
            )
            expected_hours = e["required_daily_hours"] * days_worked
            incomplete_hours = days_worked > 0 and total_hours < expected_hours

        summary.append(
            {
                "id": e["id"],
                "employee_number": e["employee_number"],
                "full_name": e["full_name"],
                "days_worked": days_worked,
                "days_absent": days_absent,
                "paid_leave_days": paid_leave_days,
                "no_pay_days": no_pay_days,
                "attendance_pct": attendance_pct,
                "total_hours": total_hours,
                "overtime_hours": overtime_hours,
                "leave_balance": leave_balance,
                "incomplete_hours": incomplete_hours,
                "required_daily_hours": e["required_daily_hours"],
            }
        )
    return summary


@app.route("/attendance")
def attendance_list():
    date_filter = request.args.get("date", "").strip()
    conn = get_connection()
    query = """SELECT a.*, e.full_name, e.employee_number FROM attendance a
               JOIN employees e ON e.id = a.employee_id"""
    params = []
    if date_filter:
        query += " WHERE a.date = ?"
        params.append(date_filter)
    query += " ORDER BY a.date DESC, a.id DESC"
    rows = conn.execute(query, params).fetchall()

    cycle = _ensure_current_cycle(conn)
    summary = _compute_attendance_summary(conn, cycle["cycle_start"], cycle["cycle_end"])

    total_days_worked = sum(s["days_worked"] for s in summary)
    total_days_absent = sum(s["days_absent"] for s in summary)
    total_no_pay_days = sum(s["no_pay_days"] for s in summary)
    total_paid_leave_days = sum(s["paid_leave_days"] for s in summary)
    total_hours = round(sum(s["total_hours"] for s in summary), 2)
    total_overtime_hours = round(sum(s["overtime_hours"] or 0 for s in summary), 2)
    # no_pay_days already includes absences, so it isn't added again here (that would double-count them).
    total_recorded = total_days_worked + total_paid_leave_days + total_no_pay_days
    overall_attendance_pct = round(total_days_worked / total_recorded * 100, 1) if total_recorded else None

    return render_template(
        "attendance.html",
        records=rows,
        date_filter=date_filter,
        cycle=cycle,
        summary=summary,
        total_days_worked=total_days_worked,
        total_days_absent=total_days_absent,
        total_no_pay_days=total_no_pay_days,
        total_paid_leave_days=total_paid_leave_days,
        total_hours=total_hours,
        total_overtime_hours=total_overtime_hours,
        overall_attendance_pct=overall_attendance_pct,
    )


@app.route("/attendance/new", methods=["GET", "POST"])
def attendance_new():
    conn = get_connection()
    employees = conn.execute("SELECT id, employee_number, full_name FROM employees ORDER BY full_name").fetchall()

    if request.method == "POST":
        employee_id = request.form.get("employee_id")
        date = request.form.get("date", "").strip()
        status = request.form.get("status", "").strip()

        if not employee_id or not date or not status:
            flash("Employee, date, and status are required.", "error")
            return render_template("attendance_form.html", employees=employees, record=request.form, mode="new")

        check_in = request.form.get("check_in", "").strip() or None
        check_out = request.form.get("check_out", "").strip() or None
        break_start = request.form.get("break_start", "").strip() or None
        break_end = request.form.get("break_end", "").strip() or None
        verification_method = request.form.get("verification_method", "").strip() or None

        total_break, net_work = compute_attendance_hours(check_in, check_out, break_start, break_end)

        conn.execute(
            """INSERT INTO attendance
               (employee_id, date, check_in, check_out, break_start, break_end,
                total_break_hours, total_work_hours, status, verification_method)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (employee_id, date, check_in, check_out, break_start, break_end,
             total_break, net_work, status, verification_method),
        )
        conn.commit()
        flash("Attendance recorded.", "success")
        return redirect(url_for("attendance_list"))

    return render_template("attendance_form.html", employees=employees, record={}, mode="new")


@app.route("/attendance/<int:record_id>/edit", methods=["GET", "POST"])
def attendance_edit(record_id):
    conn = get_connection()
    employees = conn.execute("SELECT id, employee_number, full_name FROM employees ORDER BY full_name").fetchall()
    record = conn.execute("SELECT * FROM attendance WHERE id = ?", (record_id,)).fetchone()
    if not record:
        flash("Attendance record not found.", "error")
        return redirect(url_for("attendance_list"))

    if request.method == "POST":
        expected_updated_at = request.form.get("expected_updated_at", "")
        if record["updated_at"] and expected_updated_at and record["updated_at"] != expected_updated_at:
            flash(
                "Someone else updated this attendance record while you were editing. "
                "Your changes were not saved — please review the current values and try again.",
                "error",
            )
            return redirect(url_for("attendance_edit", record_id=record_id))

        employee_id = request.form.get("employee_id")
        date = request.form.get("date", "").strip()
        status = request.form.get("status", "").strip()

        if not employee_id or not date or not status:
            flash("Employee, date, and status are required.", "error")
            merged = dict(record)
            merged.update(request.form)
            return render_template("attendance_form.html", employees=employees, record=merged, mode="edit", record_id=record_id)

        check_in = request.form.get("check_in", "").strip() or None
        check_out = request.form.get("check_out", "").strip() or None
        break_start = request.form.get("break_start", "").strip() or None
        break_end = request.form.get("break_end", "").strip() or None
        verification_method = request.form.get("verification_method", "").strip() or None

        total_break, net_work = compute_attendance_hours(check_in, check_out, break_start, break_end)

        conn.execute(
            """UPDATE attendance SET employee_id=?, date=?, check_in=?, check_out=?,
               break_start=?, break_end=?, total_break_hours=?, total_work_hours=?,
               status=?, verification_method=?, updated_at=? WHERE id=?""",
            (employee_id, date, check_in, check_out, break_start, break_end,
             total_break, net_work, status, verification_method, _now_text(), record_id),
        )
        conn.commit()
        flash("Attendance updated.", "success")
        return redirect(url_for("attendance_list"))

    return render_template("attendance_form.html", employees=employees, record=dict(record), mode="edit", record_id=record_id)


@app.route("/attendance/<int:record_id>/delete", methods=["POST"])
def attendance_delete(record_id):
    conn = get_connection()
    conn.execute("DELETE FROM attendance WHERE id = ?", (record_id,))
    conn.commit()
    flash("Attendance record removed.", "success")
    return redirect(url_for("attendance_list"))


# ---------- Work Assignments & Harvest Weighing ----------


@app.route("/work-assignments")
def work_assignment_list():
    date_filter = request.args.get("date", "").strip()
    conn = get_connection()
    query = """SELECT w.*, e.full_name, e.employee_number FROM work_assignments w
               JOIN employees e ON e.id = w.employee_id"""
    params = []
    if date_filter:
        query += " WHERE w.date = ?"
        params.append(date_filter)
    query += " ORDER BY w.date DESC, w.id DESC"
    rows = conn.execute(query, params).fetchall()
    return render_template("work_assignments.html", assignments=rows, date_filter=date_filter)


@app.route("/work-assignments/new", methods=["GET", "POST"])
def work_assignment_new():
    conn = get_connection()
    employees = conn.execute("SELECT id, employee_number, full_name FROM employees ORDER BY full_name").fetchall()

    if request.method == "POST":
        employee_id = request.form.get("employee_id")
        date = request.form.get("date", "").strip()
        task_type = request.form.get("task_type", "").strip()
        field_block = request.form.get("field_block", "").strip() or None
        harvest_target = request.form.get("harvest_target", "").strip() or None

        if not employee_id or not date or not task_type:
            flash("Employee, date, and task type are required.", "error")
            return render_template("work_assignment_form.html", employees=employees, assignment=request.form)

        conn.execute(
            """INSERT INTO work_assignments (employee_id, date, task_type, field_block, harvest_target)
               VALUES (?, ?, ?, ?, ?)""",
            (employee_id, date, task_type, field_block, harvest_target),
        )
        conn.commit()
        flash("Work assignment created.", "success")
        return redirect(url_for("work_assignment_list"))

    return render_template("work_assignment_form.html", employees=employees, assignment={})


@app.route("/work-assignments/<int:assignment_id>")
def work_assignment_detail(assignment_id):
    conn = get_connection()
    assignment = conn.execute(
        """SELECT w.*, e.full_name, e.employee_number, e.rate_per_kg, e.over_target_commission_percent
           FROM work_assignments w
           JOIN employees e ON e.id = w.employee_id WHERE w.id = ?""",
        (assignment_id,),
    ).fetchone()
    if not assignment:
        flash("Work assignment not found.", "error")
        return redirect(url_for("work_assignment_list"))

    weighings = conn.execute(
        "SELECT * FROM harvest_weighings WHERE work_assignment_id = ? ORDER BY id DESC",
        (assignment_id,),
    ).fetchall()

    pay = compute_harvest_pay(
        assignment["actual_output"],
        assignment["harvest_target"],
        assignment["rate_per_kg"],
        assignment["over_target_commission_percent"],
    )
    harvest_payment = pay["total_pay"] if assignment["rate_per_kg"] else None

    return render_template(
        "work_assignment_detail.html",
        assignment=assignment,
        weighings=weighings,
        harvest_payment=harvest_payment,
        bonus_kg=pay["bonus_kg"],
        bonus_pay=pay["bonus_pay"] if assignment["rate_per_kg"] else None,
    )


@app.route("/work-assignments/<int:assignment_id>/weigh", methods=["POST"])
def work_assignment_weigh(assignment_id):
    conn = get_connection()
    assignment = conn.execute("SELECT * FROM work_assignments WHERE id = ?", (assignment_id,)).fetchone()
    if not assignment:
        flash("Work assignment not found.", "error")
        return redirect(url_for("work_assignment_list"))

    try:
        gross_weight = float(request.form.get("gross_weight", ""))
        tare_weight = float(request.form.get("tare_weight", "") or 0)
    except ValueError:
        flash("Gross and tare weight must be numbers.", "error")
        return redirect(url_for("work_assignment_detail", assignment_id=assignment_id))

    net_weight = round(max(gross_weight - tare_weight, 0), 2)

    conn.execute(
        """INSERT INTO harvest_weighings (work_assignment_id, gross_weight, tare_weight, net_weight)
           VALUES (?, ?, ?, ?)""",
        (assignment_id, gross_weight, tare_weight, net_weight),
    )

    # Step 4 — Automatic Harvest Update: Actual_Output += Net_Weight, recompute productivity
    new_actual_output = round(assignment["actual_output"] + net_weight, 2)
    productivity = compute_productivity(new_actual_output, assignment["harvest_target"])
    status = next_work_status(new_actual_output, assignment["harvest_target"], assignment["status"])

    conn.execute(
        "UPDATE work_assignments SET actual_output = ?, productivity_score = ?, status = ? WHERE id = ?",
        (new_actual_output, productivity, status, assignment_id),
    )
    conn.commit()

    flash(f"Weighed {net_weight} kg. Output is now {new_actual_output} kg.", "success")
    return redirect(url_for("work_assignment_detail", assignment_id=assignment_id))


@app.route("/work-assignments/<int:assignment_id>/status", methods=["POST"])
def work_assignment_set_status(assignment_id):
    conn = get_connection()
    assignment = conn.execute("SELECT * FROM work_assignments WHERE id = ?", (assignment_id,)).fetchone()
    if not assignment:
        flash("Work assignment not found.", "error")
        return redirect(url_for("work_assignment_list"))

    status = request.form.get("status", "").strip()
    if status not in WORK_STATUSES:
        flash("Invalid status.", "error")
        return redirect(url_for("work_assignment_detail", assignment_id=assignment_id))

    conn.execute("UPDATE work_assignments SET status = ? WHERE id = ?", (status, assignment_id))
    conn.commit()

    flash(f"Status set to {status}.", "success")
    return redirect(url_for("work_assignment_detail", assignment_id=assignment_id))


@app.route("/work-assignments/<int:assignment_id>/weighings/<int:weighing_id>/delete", methods=["POST"])
def work_assignment_weighing_delete(assignment_id, weighing_id):
    conn = get_connection()
    assignment = conn.execute("SELECT * FROM work_assignments WHERE id = ?", (assignment_id,)).fetchone()
    weighing = conn.execute("SELECT * FROM harvest_weighings WHERE id = ?", (weighing_id,)).fetchone()
    if not assignment or not weighing:
        flash("Record not found.", "error")
        return redirect(url_for("work_assignment_list"))

    conn.execute("DELETE FROM harvest_weighings WHERE id = ?", (weighing_id,))

    new_actual_output = round(max(assignment["actual_output"] - weighing["net_weight"], 0), 2)
    productivity = compute_productivity(new_actual_output, assignment["harvest_target"])
    status = next_work_status(new_actual_output, assignment["harvest_target"], assignment["status"])

    conn.execute(
        "UPDATE work_assignments SET actual_output = ?, productivity_score = ?, status = ? WHERE id = ?",
        (new_actual_output, productivity, status, assignment_id),
    )
    conn.commit()

    flash("Weighing entry removed and output corrected.", "success")
    return redirect(url_for("work_assignment_detail", assignment_id=assignment_id))


@app.route("/work-assignments/<int:assignment_id>/delete", methods=["POST"])
def work_assignment_delete(assignment_id):
    conn = get_connection()
    conn.execute("DELETE FROM work_assignments WHERE id = ?", (assignment_id,))
    conn.commit()
    flash("Work assignment removed.", "success")
    return redirect(url_for("work_assignment_list"))


# ---------- Payroll ----------

SRI_LANKA_TZ = ZoneInfo("Asia/Colombo")


def _colombo_today():
    """'Today' as observed in Sri Lanka (UTC+5:30), regardless of the server's
    own timezone — pay cycles are defined by the Sri Lankan calendar week."""
    return datetime.now(SRI_LANKA_TZ).date()


def _week_bounds(d):
    """The Monday-Sunday week containing date d."""
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


@app.template_filter("lk_date")
def lk_date(value):
    """Sri Lankan date display format: 'YYYY-MM-DD' -> 'DD/MM/YYYY'."""
    if not value:
        return "—"
    try:
        return date.fromisoformat(value).strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return value


# ---------- Weather (Open-Meteo, 7-day forecast for Galle) ----------

GALLE_LATITUDE = 6.0535
GALLE_LONGITUDE = 80.2210
WEATHER_CACHE_TTL_MINUTES = 45

WEATHER_CODE_INFO = {
    0: ("Clear sky", "☀️"),
    1: ("Mainly clear", "🌤️"),
    2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"),
    45: ("Fog", "🌫️"),
    48: ("Depositing rime fog", "🌫️"),
    51: ("Light drizzle", "🌦️"),
    53: ("Moderate drizzle", "🌦️"),
    55: ("Dense drizzle", "🌦️"),
    56: ("Light freezing drizzle", "🌦️"),
    57: ("Dense freezing drizzle", "🌦️"),
    61: ("Slight rain", "🌧️"),
    63: ("Moderate rain", "🌧️"),
    65: ("Heavy rain", "🌧️"),
    66: ("Light freezing rain", "🌧️"),
    67: ("Heavy freezing rain", "🌧️"),
    71: ("Slight snow fall", "🌨️"),
    73: ("Moderate snow fall", "🌨️"),
    75: ("Heavy snow fall", "🌨️"),
    77: ("Snow grains", "🌨️"),
    80: ("Slight rain showers", "🌦️"),
    81: ("Moderate rain showers", "🌧️"),
    82: ("Violent rain showers", "⛈️"),
    85: ("Slight snow showers", "🌨️"),
    86: ("Heavy snow showers", "🌨️"),
    95: ("Thunderstorm", "⛈️"),
    96: ("Thunderstorm, slight hail", "⛈️"),
    99: ("Thunderstorm, heavy hail", "⛈️"),
}


def _weather_description(code):
    return WEATHER_CODE_INFO.get(code, ("Unknown", "🌡️"))


def _fetch_weather_forecast():
    """7-day forecast for Galle, Sri Lanka from Open-Meteo (no API key needed),
    cached in the weather_cache table for WEATHER_CACHE_TTL_MINUTES — Vercel's
    serverless filesystem can't hold an in-memory cache reliably across
    invocations, so the cache has to live in the database."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM weather_cache WHERE id = 1").fetchone()

    if row:
        fetched_at = datetime.fromisoformat(row["fetched_at"])
        age_minutes = (datetime.now(timezone.utc).replace(tzinfo=None) - fetched_at).total_seconds() / 60
        if age_minutes < WEATHER_CACHE_TTL_MINUTES:
            return json.loads(row["payload"])

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={GALLE_LATITUDE}&longitude={GALLE_LONGITUDE}"
        "&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,"
        "precipitation_probability_max,windspeed_10m_max"
        "&current_weather=true&timezone=Asia%2FColombo&forecast_days=7"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError):
        # Open-Meteo unreachable — fall back to the last cached forecast (even if
        # stale) rather than showing nothing.
        return json.loads(row["payload"]) if row else None

    daily = raw.get("daily", {})
    days = []
    for i, day_iso in enumerate(daily.get("time", [])):
        code = daily.get("weathercode", [])[i] if i < len(daily.get("weathercode", [])) else None
        label, icon = _weather_description(code)
        days.append(
            {
                "date": day_iso,
                "description": label,
                "icon": icon,
                "temp_max": daily.get("temperature_2m_max", [None] * (i + 1))[i],
                "temp_min": daily.get("temperature_2m_min", [None] * (i + 1))[i],
                "precipitation_sum": daily.get("precipitation_sum", [None] * (i + 1))[i],
                "precipitation_probability_max": daily.get("precipitation_probability_max", [None] * (i + 1))[i],
                "windspeed_max": daily.get("windspeed_10m_max", [None] * (i + 1))[i],
            }
        )

    current = raw.get("current_weather") or {}
    current_label, current_icon = _weather_description(current.get("weathercode"))
    payload = {
        "location": "Galle, Sri Lanka",
        "current": {
            "temperature": current.get("temperature"),
            "windspeed": current.get("windspeed"),
            "description": current_label,
            "icon": current_icon,
            "time": current.get("time"),
        },
        "days": days,
    }

    payload_text = json.dumps(payload)
    fetched_at_text = _now_text()
    if row:
        conn.execute(
            "UPDATE weather_cache SET fetched_at = ?, payload = ? WHERE id = 1",
            (fetched_at_text, payload_text),
        )
    else:
        conn.execute(
            "INSERT INTO weather_cache (id, fetched_at, payload) VALUES (1, ?, ?)",
            (fetched_at_text, payload_text),
        )
    conn.commit()
    return payload


@app.route("/weather")
def weather_forecast():
    forecast = _fetch_weather_forecast()
    return render_template("weather.html", forecast=forecast)


# ---------- Birthdays & Announcements (Auto Updates) ----------


def _next_birthday_info(date_of_birth, today=None):
    if not date_of_birth:
        return None
    try:
        dob = date.fromisoformat(date_of_birth)
    except (ValueError, TypeError):
        return None
    today = today or _colombo_today()
    try:
        next_bday = dob.replace(year=today.year)
    except ValueError:
        # Born on Feb 29 — observe on Feb 28 in non-leap years.
        next_bday = date(today.year, 2, 28)
    if next_bday < today:
        try:
            next_bday = dob.replace(year=today.year + 1)
        except ValueError:
            next_bday = date(today.year + 1, 2, 28)
    return {
        "next_date": next_bday.isoformat(),
        "days_until": (next_bday - today).days,
        "turning_age": next_bday.year - dob.year,
    }


def _upcoming_birthdays(conn, within_days=30):
    today = _colombo_today()
    rows = conn.execute(
        "SELECT id, full_name, employee_number, date_of_birth FROM employees "
        "WHERE date_of_birth IS NOT NULL AND date_of_birth != ''"
    ).fetchall()
    upcoming = []
    for e in rows:
        info = _next_birthday_info(e["date_of_birth"], today)
        if info and info["days_until"] <= within_days:
            upcoming.append(
                {
                    "id": e["id"],
                    "full_name": e["full_name"],
                    "employee_number": e["employee_number"],
                    **info,
                }
            )
    upcoming.sort(key=lambda x: x["days_until"])
    return upcoming


def _latest_tea_price(conn):
    today_iso = _colombo_today().isoformat()
    row = conn.execute("SELECT * FROM daily_prices WHERE date = ?", (today_iso,)).fetchone()
    if row:
        return dict(row)
    row = conn.execute("SELECT * FROM daily_prices ORDER BY date DESC LIMIT 1").fetchone()
    return dict(row) if row else None


@app.route("/announcements/new", methods=["POST"])
def announcement_new():
    message = request.form.get("message", "").strip()
    if not message:
        flash("Announcement message is required.", "error")
        return redirect(request.referrer or url_for("dashboard"))
    conn = get_connection()
    conn.execute(
        "INSERT INTO announcements (message, created_by) VALUES (?, ?)",
        (message, session.get("user_id")),
    )
    conn.commit()
    flash("Announcement posted.", "success")
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/announcements/<int:announcement_id>/delete", methods=["POST"])
def announcement_delete(announcement_id):
    conn = get_connection()
    conn.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
    conn.commit()
    flash("Announcement removed.", "success")
    return redirect(request.referrer or url_for("dashboard"))


def _ensure_current_cycle(conn):
    """The current active pay cycle is the latest Unpaid one. If none exists at
    all (first-ever use, or every prior cycle has been paid), auto-create a
    Monday-Sunday cycle for the current Sri Lankan calendar week, due the
    following Monday."""
    cycle = conn.execute(
        "SELECT * FROM payroll_cycles WHERE status = 'Unpaid' ORDER BY cycle_start DESC LIMIT 1"
    ).fetchone()
    if cycle:
        return dict(cycle)

    monday, sunday = _week_bounds(_colombo_today())
    due_date = sunday + timedelta(days=1)
    conn.execute(
        "INSERT INTO payroll_cycles (cycle_start, cycle_end, due_date, status) VALUES (?, ?, ?, 'Unpaid')",
        (monday.isoformat(), sunday.isoformat(), due_date.isoformat()),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM payroll_cycles WHERE cycle_start = ?", (monday.isoformat(),)).fetchone())


def _compute_payroll_rows(date_from, date_to, include_advances=False):
    """Harvest pay is computed per work assignment (each has its own daily target),
    not from an aggregated total, since kg beyond a given day's target earns a
    commission bonus that a single SUM-based calculation couldn't apply correctly."""
    conn = get_connection()
    employees = conn.execute(
        """SELECT e.id, e.employee_number, e.full_name, e.rate_per_kg, e.hourly_rate,
                  e.over_target_commission_percent, e.epf_etf_applicable, e.required_daily_hours,
                  (SELECT COUNT(*) FROM attendance a
                     WHERE a.employee_id = e.id AND a.date BETWEEN ? AND ? AND a.status = 'Present') AS present_days,
                  (SELECT COALESCE(SUM(a.total_work_hours), 0) FROM attendance a
                     WHERE a.employee_id = e.id AND a.date BETWEEN ? AND ?) AS total_hours
           FROM employees e
           ORDER BY e.full_name""",
        (date_from, date_to, date_from, date_to),
    ).fetchall()

    attendance_by_employee = {s["id"]: s for s in _compute_attendance_summary(conn, date_from, date_to)}

    payroll_rows = []
    for e in employees:
        assignments = conn.execute(
            "SELECT actual_output, harvest_target FROM work_assignments WHERE employee_id = ? AND date BETWEEN ? AND ?",
            (e["id"], date_from, date_to),
        ).fetchall()

        total_output = 0
        bonus_kg_total = 0
        bonus_pay_total = 0
        harvest_payment_total = 0
        for a in assignments:
            output = a["actual_output"] or 0
            total_output += output
            if e["rate_per_kg"]:
                pay = compute_harvest_pay(output, a["harvest_target"], e["rate_per_kg"], e["over_target_commission_percent"])
                harvest_payment_total += pay["total_pay"]
                bonus_kg_total += pay["bonus_kg"]
                bonus_pay_total += pay["bonus_pay"]

        row = dict(e)
        row["total_output"] = round(total_output, 2)
        row["bonus_kg"] = round(bonus_kg_total, 2)
        row["bonus_pay"] = round(bonus_pay_total, 2) if row["rate_per_kg"] else None
        row["harvest_payment"] = round(harvest_payment_total, 2) if row["rate_per_kg"] else None
        row["hourly_pay"] = (
            round(row["total_hours"] * row["hourly_rate"], 2) if row["hourly_rate"] else None
        )

        attendance_info = attendance_by_employee.get(row["id"], {})
        paid_leave_days = attendance_info.get("paid_leave_days", 0)
        row["no_pay_days"] = attendance_info.get("no_pay_days", 0)
        row["paid_leave_days"] = paid_leave_days
        # Paid leave (annual Leave within entitlement, plus all Sick Leave) is compensated at
        # the employee's day rate; No-Pay (LOP) days above genuinely earn nothing extra — they
        # already contribute 0 hours to hourly_pay, so no separate deduction is needed for them.
        row["leave_pay"] = (
            round(paid_leave_days * row["required_daily_hours"] * row["hourly_rate"], 2)
            if (row["hourly_rate"] and row["required_daily_hours"] and paid_leave_days)
            else 0
        )

        parts = [p for p in (row["harvest_payment"], row["hourly_pay"]) if p is not None]
        if row["leave_pay"]:
            parts.append(row["leave_pay"])
        row["total_pay"] = round(sum(parts), 2) if parts else None

        epf_etf = compute_epf_etf(row["total_pay"], row["epf_etf_applicable"])
        row["employee_epf"] = epf_etf["employee_epf"]
        row["employer_epf"] = epf_etf["employer_epf"]
        row["employer_etf"] = epf_etf["employer_etf"]
        row["true_labor_cost"] = (
            round((row["total_pay"] or 0) + epf_etf["employer_epf"] + epf_etf["employer_etf"], 2)
            if row["total_pay"] is not None
            else None
        )

        if include_advances:
            advance_total = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM salary_advances WHERE employee_id = ? AND date BETWEEN ? AND ?",
                (row["id"], date_from, date_to),
            ).fetchone()["total"]
            row["advance_total"] = round(advance_total, 2) if advance_total else 0
            deductions = row["advance_total"] + row["employee_epf"]
            row["net_pay"] = round((row["total_pay"] or 0) - deductions, 2) if (row["total_pay"] or deductions) else None

        payroll_rows.append(row)
    return payroll_rows


@app.route("/payroll")
def payroll():
    conn = get_connection()
    cycle = _ensure_current_cycle(conn)
    date_from, date_to = cycle["cycle_start"], cycle["cycle_end"]

    payroll_rows = _compute_payroll_rows(date_from, date_to, include_advances=True)
    total_payment = sum(r["total_pay"] for r in payroll_rows if r["total_pay"])
    total_advances = sum(r["advance_total"] for r in payroll_rows if r["advance_total"])
    total_employee_epf = sum(r["employee_epf"] for r in payroll_rows if r["employee_epf"])
    total_employer_epf_etf = sum(
        (r["employer_epf"] or 0) + (r["employer_etf"] or 0) for r in payroll_rows
    )
    total_net_pay = sum(r["net_pay"] for r in payroll_rows if r["net_pay"])

    employees = conn.execute("SELECT id, employee_number, full_name FROM employees ORDER BY full_name").fetchall()
    advances = conn.execute(
        """SELECT sa.*, e.full_name, e.employee_number FROM salary_advances sa
           JOIN employees e ON e.id = sa.employee_id
           WHERE sa.date BETWEEN ? AND ?
           ORDER BY sa.date DESC, sa.id DESC""",
        (date_from, date_to),
    ).fetchall()

    today_iso = _colombo_today().isoformat()
    is_due = today_iso >= cycle["due_date"]

    return render_template(
        "payroll.html",
        cycle=cycle,
        is_due=is_due,
        rows=payroll_rows,
        date_from=date_from,
        date_to=date_to,
        total_payment=total_payment,
        total_advances=total_advances,
        total_employee_epf=round(total_employee_epf, 2),
        total_employer_epf_etf=round(total_employer_epf_etf, 2),
        total_net_pay=round(total_net_pay, 2),
        employees=employees,
        advances=advances,
    )


@app.route("/payroll/mark-paid", methods=["POST"])
def payroll_mark_paid():
    """Freezes the current cycle's payroll figures into payroll_transactions
    (so later edits to attendance/harvest can never retroactively change a paid
    cycle's numbers), locks the cycle, and opens the next Monday-Sunday cycle."""
    conn = get_connection()
    cycle = _ensure_current_cycle(conn)
    if cycle["status"] != "Unpaid":
        flash("This cycle has already been marked as paid.", "error")
        return redirect(url_for("payroll"))

    date_from, date_to = cycle["cycle_start"], cycle["cycle_end"]
    payroll_rows = _compute_payroll_rows(date_from, date_to, include_advances=True)
    today_iso = _colombo_today().isoformat()

    snapshotted = 0
    for row in payroll_rows:
        if row["total_pay"] is None:
            continue  # no rate configured — nothing to pay or freeze
        employee = conn.execute("SELECT * FROM employees WHERE id = ?", (row["id"],)).fetchone()
        conn.execute(
            """INSERT INTO payroll_transactions
               (cycle_id, employee_id, employee_number, full_name, present_days, total_hours,
                hourly_rate, hourly_pay, total_output, rate_per_kg, bonus_kg, bonus_pay, harvest_payment,
                leave_pay, paid_leave_days, no_pay_days,
                total_pay, advance_total, employee_epf, employer_epf, employer_etf, epf_etf_applicable,
                net_pay, payment_method, payment_date, payment_status,
                bank_name, bank_branch, bank_account_name, bank_account_number, bank_branch_code, bank_account_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cycle["id"], row["id"], row["employee_number"], row["full_name"], row["present_days"],
                row["total_hours"], row["hourly_rate"], row["hourly_pay"], row["total_output"], row["rate_per_kg"],
                row["bonus_kg"], row["bonus_pay"], row["harvest_payment"],
                row["leave_pay"], row["paid_leave_days"], row["no_pay_days"],
                row["total_pay"], row["advance_total"],
                row["employee_epf"], row["employer_epf"], row["employer_etf"], row["epf_etf_applicable"],
                row["net_pay"], employee["default_payment_method"] or "Cash", today_iso, "Paid",
                employee["bank_name"], employee["bank_branch"], employee["bank_account_name"],
                employee["bank_account_number"], employee["bank_branch_code"], employee["bank_account_type"],
            ),
        )
        snapshotted += 1

    conn.execute(
        "UPDATE payroll_cycles SET status = 'Paid', paid_at = ? WHERE id = ?", (_now_text(), cycle["id"])
    )

    next_start = date.fromisoformat(date_to) + timedelta(days=1)
    next_end = next_start + timedelta(days=6)
    next_due = next_end + timedelta(days=1)
    existing_next = conn.execute(
        "SELECT id FROM payroll_cycles WHERE cycle_start = ?", (next_start.isoformat(),)
    ).fetchone()
    if not existing_next:
        conn.execute(
            "INSERT INTO payroll_cycles (cycle_start, cycle_end, due_date, status) VALUES (?, ?, ?, 'Unpaid')",
            (next_start.isoformat(), next_end.isoformat(), next_due.isoformat()),
        )
    conn.commit()

    flash(
        f"Cycle {lk_date(date_from)} to {lk_date(date_to)} marked as paid and archived "
        f"({snapshotted} employee{'s' if snapshotted != 1 else ''}). Next cycle opened.",
        "success",
    )
    return redirect(url_for("payroll"))


@app.route("/payroll/advance", methods=["POST"])
def payroll_add_advance():
    employee_id = request.form.get("employee_id", "").strip()
    date_value = request.form.get("date", "").strip()
    amount_value = request.form.get("amount", "").strip()
    note = request.form.get("note", "").strip() or None
    payment_method = request.form.get("payment_method", "").strip() or None

    if not employee_id or not date_value:
        flash("Employee and date are required.", "error")
        return redirect(url_for("payroll"))

    try:
        amount = float(amount_value)
    except ValueError:
        flash("Advance amount must be a number.", "error")
        return redirect(url_for("payroll"))

    conn = get_connection()
    conn.execute(
        "INSERT INTO salary_advances (employee_id, date, amount, note, payment_method, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (employee_id, date_value, amount, note, payment_method, session.get("user_id")),
    )
    conn.commit()
    flash(f"Salary advance of {amount} recorded.", "success")
    return redirect(url_for("payroll"))


@app.route("/payroll/advance/<int:advance_id>/delete", methods=["POST"])
def payroll_delete_advance(advance_id):
    conn = get_connection()
    advance = conn.execute("SELECT * FROM salary_advances WHERE id = ?", (advance_id,)).fetchone()
    if not advance:
        flash("Advance not found.", "error")
        return redirect(url_for("payroll"))

    conn.execute("DELETE FROM salary_advances WHERE id = ?", (advance_id,))
    conn.commit()
    flash("Salary advance removed.", "success")
    return redirect(url_for("payroll"))


@app.route("/payroll/export.csv")
def payroll_export():
    import csv

    conn = get_connection()
    cycle = _ensure_current_cycle(conn)
    date_from, date_to = cycle["cycle_start"], cycle["cycle_end"]
    payroll_rows = _compute_payroll_rows(date_from, date_to, include_advances=True)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Employee Number", "Full Name", "Present Days", "Hours Worked", "Hourly Rate",
            "Time-based Pay", "Harvest (kg)", "Rate/kg", "Bonus (kg)", "Harvest Pay",
            "Paid Leave Days", "Leave Pay", "No-Pay (LOP) Days", "Total Pay",
            "Salary Advance", "Employee EPF (8%)", "Net Pay", "Employer EPF (12%)", "Employer ETF (3%)",
        ]
    )
    for r in payroll_rows:
        writer.writerow(
            [
                r["employee_number"], r["full_name"], r["present_days"], r["total_hours"], r["hourly_rate"],
                r["hourly_pay"], r["total_output"], r["rate_per_kg"], r["bonus_kg"], r["harvest_payment"],
                r["paid_leave_days"], r["leave_pay"], r["no_pay_days"], r["total_pay"],
                r["advance_total"], r["employee_epf"], r["net_pay"], r["employer_epf"], r["employer_etf"],
            ]
        )

    csv_bytes = io.BytesIO(buffer.getvalue().encode("utf-8"))
    return send_file(
        csv_bytes,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"payroll_{date_from}_to_{date_to}.csv",
    )


def _build_payslip_pdf(row, employee_extra, date_from, date_to, advances, payment_info, download_name):
    """Shared by the live current-cycle payslip and the Payroll History reprint —
    `row` supplies the pay figures (from either _compute_payroll_rows or a frozen
    payroll_transactions snapshot), `employee_extra` supplies position/division/
    bank details, and `payment_info` supplies payment method/status/date/reference."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm, leftMargin=20 * mm, rightMargin=20 * mm
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("PayslipTitle", parent=styles["Title"], fontSize=18, spaceAfter=2)
    sub_style = ParagraphStyle("PayslipSub", parent=styles["Normal"], textColor=colors.HexColor("#6b7a67"))
    green = colors.HexColor("#2f5d3a")
    red = colors.HexColor("#b3413a")
    blue = colors.HexColor("#385580")
    border = colors.HexColor("#dbe5d9")

    elements = [
        Paragraph(COMPANY_NAME, title_style),
        Paragraph("Payslip", sub_style),
        Spacer(1, 10 * mm),
    ]

    info_table = Table(
        [
            ["Employee", row["full_name"]],
            ["Employee ID", row["employee_number"]],
            ["Position", employee_extra.get("job_position") or "—"],
            ["Division", employee_extra.get("estate_division") or "—"],
            ["Pay period", f"{lk_date(date_from)} to {lk_date(date_to)}"],
        ],
        colWidths=[40 * mm, 120 * mm],
    )
    info_table.setStyle(TableStyle([("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    elements.append(info_table)
    elements.append(Spacer(1, 8 * mm))

    earnings_rows = [
        ["Earnings", "Details", "Amount"],
        [
            "Time-based pay",
            f"{row['total_hours']} h x {row['hourly_rate'] if row['hourly_rate'] is not None else '—'}",
            row["hourly_pay"] if row["hourly_pay"] is not None else "—",
        ],
        [
            "Harvest pay",
            f"{row['total_output']} kg x {row['rate_per_kg'] if row['rate_per_kg'] is not None else '—'}",
            row["harvest_payment"] if row["harvest_payment"] is not None else "—",
        ],
    ]
    if row["bonus_kg"]:
        earnings_rows.append(
            [
                "  incl. over-target commission",
                f"{row['bonus_kg']} kg bonus",
                row["bonus_pay"],
            ]
        )
    if row.get("leave_pay"):
        earnings_rows.append(
            [
                "Paid leave",
                f"{row.get('paid_leave_days') or 0} day(s) @ day rate",
                row["leave_pay"],
            ]
        )
    earnings_rows.append(["", "Total Pay", row["total_pay"] if row["total_pay"] is not None else "—"])

    earnings_table = Table(earnings_rows, colWidths=[45 * mm, 70 * mm, 45 * mm])
    earnings_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), green),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (1, -1), (-1, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, border),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ]
        )
    )
    elements.append(earnings_table)
    elements.append(Spacer(1, 8 * mm))

    deduction_rows = [["Deductions", "Date", "Amount"]]
    if advances:
        for a in advances:
            deduction_rows.append(["Salary advance", lk_date(a["date"]), a["amount"]])
    if row["employee_epf"]:
        deduction_rows.append(["EPF (8% employee contribution)", "", row["employee_epf"]])
    if len(deduction_rows) == 1:
        deduction_rows.append(["No deductions in this period", "", ""])
    total_deductions = round((row["advance_total"] or 0) + row["employee_epf"], 2)
    deduction_rows.append(["", "Total Deductions", total_deductions])
    deduction_table = Table(deduction_rows, colWidths=[45 * mm, 70 * mm, 45 * mm])
    deduction_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), red),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (1, -1), (-1, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, border),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
            ]
        )
    )
    elements.append(deduction_table)
    elements.append(Spacer(1, 10 * mm))

    net_pay_style = ParagraphStyle("NetPay", parent=styles["Title"], fontSize=16, textColor=green)
    elements.append(Paragraph(f"Net Pay: {row['net_pay'] if row['net_pay'] is not None else '—'}", net_pay_style))

    if row["epf_etf_applicable"]:
        elements.append(Spacer(1, 6 * mm))
        elements.append(
            Paragraph(
                f"Employer also contributes EPF 12% ({row['employer_epf']}) + ETF 3% ({row['employer_etf']}) "
                "separately — not deducted from this payslip.",
                sub_style,
            )
        )

    if row.get("no_pay_days"):
        elements.append(Spacer(1, 4 * mm))
        elements.append(
            Paragraph(
                f"{row['no_pay_days']} No-Pay (LOP) day(s) this period (absence or leave beyond entitlement) "
                "earned no pay and are not included above.",
                sub_style,
            )
        )

    elements.append(Spacer(1, 10 * mm))
    payment_rows = [
        ["Payment method", payment_info.get("payment_method") or "—"],
        ["Payment status", payment_info.get("payment_status") or "Pending"],
        ["Payment date", lk_date(payment_info.get("payment_date"))],
        ["Reference / Transaction ID", payment_info.get("payment_reference") or "—"],
    ]
    if (employee_extra.get("bank_name") or employee_extra.get("bank_account_number")) and (
        payment_info.get("payment_method") == "Bank Transfer" or employee_extra.get("bank_name")
    ):
        payment_rows.extend(
            [
                ["Bank", employee_extra.get("bank_name") or "—"],
                ["Branch", employee_extra.get("bank_branch") or "—"],
                ["Branch / BSB Code", employee_extra.get("bank_branch_code") or "—"],
                ["Account Name", employee_extra.get("bank_account_name") or "—"],
                ["Account Number", employee_extra.get("bank_account_number") or "—"],
                ["Account Type", employee_extra.get("bank_account_type") or "—"],
            ]
        )
    payment_table = Table(payment_rows, colWidths=[45 * mm, 115 * mm])
    payment_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), blue),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, border),
            ]
        )
    )
    elements.append(Paragraph("Payment Information", ParagraphStyle("PayInfo", parent=styles["Heading3"], fontSize=11)))
    elements.append(Spacer(1, 3 * mm))
    elements.append(payment_table)

    doc.build(elements)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=download_name,
    )


@app.route("/payroll/payslip/<int:employee_id>")
def payroll_payslip(employee_id):
    conn = get_connection()
    cycle = _ensure_current_cycle(conn)
    date_from, date_to = cycle["cycle_start"], cycle["cycle_end"]

    employee = conn.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
    if not employee:
        flash("Employee not found.", "error")
        return redirect(url_for("payroll"))

    payroll_rows = _compute_payroll_rows(date_from, date_to, include_advances=True)
    row = next((r for r in payroll_rows if r["id"] == employee_id), None)
    if row is None:
        flash("No payroll data for this employee in the selected period.", "error")
        return redirect(url_for("payroll"))

    advances = conn.execute(
        "SELECT * FROM salary_advances WHERE employee_id = ? AND date BETWEEN ? AND ? ORDER BY date",
        (employee_id, date_from, date_to),
    ).fetchall()

    employee_extra = dict(employee)
    payment_info = {
        "payment_method": employee["default_payment_method"],
        "payment_status": "Pending",
        "payment_date": None,
        "payment_reference": None,
    }

    return _build_payslip_pdf(
        row, employee_extra, date_from, date_to, advances, payment_info,
        download_name=f"payslip_{row['employee_number']}_{date_from}_to_{date_to}.pdf",
    )


# ---------- Payroll History ----------


@app.route("/payroll/history")
def payroll_history():
    conn = get_connection()

    search = request.args.get("q", "").strip().lower()
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()

    query = "SELECT * FROM payroll_cycles WHERE status = 'Paid'"
    params = []
    if date_from:
        query += " AND cycle_end >= ?"
        params.append(date_from)
    if date_to:
        query += " AND cycle_start <= ?"
        params.append(date_to)
    query += " ORDER BY cycle_start DESC"
    cycles = [dict(c) for c in conn.execute(query, params).fetchall()]

    for c in cycles:
        totals = conn.execute(
            """SELECT COUNT(*) AS employee_count, COALESCE(SUM(net_pay), 0) AS total_net_pay,
                      COALESCE(SUM(total_pay), 0) AS total_gross_pay
               FROM payroll_transactions WHERE cycle_id = ?""",
            (c["id"],),
        ).fetchone()
        c["employee_count"] = totals["employee_count"]
        c["total_net_pay"] = round(totals["total_net_pay"], 2)
        c["total_gross_pay"] = round(totals["total_gross_pay"], 2)

    if search:
        def _matches(c):
            if search in c["cycle_start"] or search in c["cycle_end"]:
                return True
            names = conn.execute(
                "SELECT full_name FROM payroll_transactions WHERE cycle_id = ?", (c["id"],)
            ).fetchall()
            return any(search in n["full_name"].lower() for n in names)

        cycles = [c for c in cycles if _matches(c)]

    return render_template("payroll_history.html", cycles=cycles, search=search, date_from=date_from, date_to=date_to)


@app.route("/payroll/history/<int:cycle_id>")
def payroll_cycle_detail(cycle_id):
    conn = get_connection()
    cycle = conn.execute("SELECT * FROM payroll_cycles WHERE id = ?", (cycle_id,)).fetchone()
    if not cycle:
        flash("Payroll cycle not found.", "error")
        return redirect(url_for("payroll_history"))

    transactions = conn.execute(
        "SELECT * FROM payroll_transactions WHERE cycle_id = ? ORDER BY full_name", (cycle_id,)
    ).fetchall()
    total_net_pay = round(sum(t["net_pay"] or 0 for t in transactions), 2)
    total_gross_pay = round(sum(t["total_pay"] or 0 for t in transactions), 2)

    return render_template(
        "payroll_cycle_detail.html",
        cycle=cycle,
        transactions=transactions,
        total_net_pay=total_net_pay,
        total_gross_pay=total_gross_pay,
    )


@app.route("/payroll/history/<int:cycle_id>/transactions/<int:transaction_id>/update", methods=["POST"])
def payroll_transaction_update(cycle_id, transaction_id):
    conn = get_connection()
    transaction = conn.execute(
        "SELECT * FROM payroll_transactions WHERE id = ? AND cycle_id = ?", (transaction_id, cycle_id)
    ).fetchone()
    if not transaction:
        flash("Payroll transaction not found.", "error")
        return redirect(url_for("payroll_cycle_detail", cycle_id=cycle_id))

    payment_method = request.form.get("payment_method", "").strip()
    payment_status = request.form.get("payment_status", "").strip()
    payment_date = request.form.get("payment_date", "").strip() or None
    payment_reference = request.form.get("payment_reference", "").strip() or None

    if payment_method not in PAYMENT_METHODS or payment_status not in PAYROLL_TRANSACTION_STATUSES:
        flash("Select a valid payment method and status.", "error")
        return redirect(url_for("payroll_cycle_detail", cycle_id=cycle_id))

    conn.execute(
        """UPDATE payroll_transactions
           SET payment_method = ?, payment_status = ?, payment_date = ?, payment_reference = ?
           WHERE id = ?""",
        (payment_method, payment_status, payment_date, payment_reference, transaction_id),
    )
    conn.commit()
    flash(f"Payment record updated for {transaction['full_name']}.", "success")
    return redirect(url_for("payroll_cycle_detail", cycle_id=cycle_id))


@app.route("/payroll/history/<int:cycle_id>/export.csv")
def payroll_cycle_export(cycle_id):
    import csv

    conn = get_connection()
    cycle = conn.execute("SELECT * FROM payroll_cycles WHERE id = ?", (cycle_id,)).fetchone()
    if not cycle:
        flash("Payroll cycle not found.", "error")
        return redirect(url_for("payroll_history"))

    transactions = conn.execute(
        "SELECT * FROM payroll_transactions WHERE cycle_id = ? ORDER BY full_name", (cycle_id,)
    ).fetchall()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Employee Number", "Full Name", "Present Days", "Hours Worked", "Hourly Rate", "Time-based Pay",
            "Harvest (kg)", "Rate/kg", "Bonus (kg)", "Harvest Pay", "Total Pay", "Salary Advance",
            "Employee EPF (8%)", "Net Pay", "Employer EPF (12%)", "Employer ETF (3%)",
            "Payment Method", "Payment Date", "Payment Reference", "Payment Status",
            "Bank Name", "Bank Branch", "Branch/BSB Code", "Account Name", "Account Number", "Account Type",
        ]
    )
    for t in transactions:
        writer.writerow(
            [
                t["employee_number"], t["full_name"], t["present_days"], t["total_hours"], t["hourly_rate"],
                t["hourly_pay"], t["total_output"], t["rate_per_kg"], t["bonus_kg"], t["harvest_payment"],
                t["total_pay"], t["advance_total"], t["employee_epf"], t["net_pay"], t["employer_epf"],
                t["employer_etf"], t["payment_method"], t["payment_date"], t["payment_reference"], t["payment_status"],
                t["bank_name"], t["bank_branch"], t["bank_branch_code"], t["bank_account_name"],
                t["bank_account_number"], t["bank_account_type"],
            ]
        )

    csv_bytes = io.BytesIO(buffer.getvalue().encode("utf-8"))
    return send_file(
        csv_bytes,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"payroll_{cycle['cycle_start']}_to_{cycle['cycle_end']}.csv",
    )


@app.route("/payroll/history/<int:cycle_id>/payslip/<int:employee_id>")
def payroll_history_payslip(cycle_id, employee_id):
    conn = get_connection()
    cycle = conn.execute("SELECT * FROM payroll_cycles WHERE id = ?", (cycle_id,)).fetchone()
    if not cycle:
        flash("Payroll cycle not found.", "error")
        return redirect(url_for("payroll_history"))

    t = conn.execute(
        "SELECT * FROM payroll_transactions WHERE cycle_id = ? AND employee_id = ?", (cycle_id, employee_id)
    ).fetchone()
    if not t:
        flash("No payroll record for this employee in that cycle.", "error")
        return redirect(url_for("payroll_cycle_detail", cycle_id=cycle_id))

    row = dict(t)
    employee = conn.execute("SELECT job_position, estate_division FROM employees WHERE id = ?", (employee_id,)).fetchone()
    employee_extra = dict(employee) if employee else {}
    employee_extra.update(
        {
            "bank_name": t["bank_name"], "bank_branch": t["bank_branch"], "bank_account_name": t["bank_account_name"],
            "bank_account_number": t["bank_account_number"], "bank_branch_code": t["bank_branch_code"],
            "bank_account_type": t["bank_account_type"],
        }
    )
    advances = conn.execute(
        "SELECT * FROM salary_advances WHERE employee_id = ? AND date BETWEEN ? AND ? ORDER BY date",
        (employee_id, cycle["cycle_start"], cycle["cycle_end"]),
    ).fetchall()
    payment_info = {
        "payment_method": t["payment_method"],
        "payment_status": t["payment_status"],
        "payment_date": t["payment_date"],
        "payment_reference": t["payment_reference"],
    }

    return _build_payslip_pdf(
        row, employee_extra, cycle["cycle_start"], cycle["cycle_end"], advances, payment_info,
        download_name=f"payslip_{row['employee_number']}_{cycle['cycle_start']}_to_{cycle['cycle_end']}.pdf",
    )


@app.route("/payroll/history/<int:cycle_id>/pdf")
def payroll_cycle_pdf(cycle_id):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.pdfgen import canvas as pdfcanvas

    conn = get_connection()
    cycle = conn.execute("SELECT * FROM payroll_cycles WHERE id = ?", (cycle_id,)).fetchone()
    if not cycle:
        flash("Payroll cycle not found.", "error")
        return redirect(url_for("payroll_history"))

    transactions = conn.execute(
        "SELECT * FROM payroll_transactions WHERE cycle_id = ? ORDER BY full_name", (cycle_id,)
    ).fetchall()

    pagesize = landscape(A4)
    page_width = pagesize[0]
    green = colors.HexColor("#2f5d3a")
    dark_green = colors.HexColor("#1f4028")
    border = colors.HexColor("#dbe5d9")
    muted = colors.HexColor("#6b7a67")

    class NumberedCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.setFont("Helvetica", 8)
                self.setFillColor(muted)
                self.drawRightString(page_width - 15 * mm, 10 * mm, f"Page {self._pageNumber} of {total_pages}")
                super().showPage()
            super().save()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=pagesize, topMargin=15 * mm, bottomMargin=18 * mm, leftMargin=15 * mm, rightMargin=15 * mm
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Title"], fontSize=18, textColor=dark_green)
    sub_style = ParagraphStyle("ReportSub", parent=styles["Normal"], textColor=muted, fontSize=9)

    elements = [
        Paragraph(COMPANY_NAME, title_style),
        Paragraph(f"{COMPANY_ADDRESS} &middot; {COMPANY_PHONE}", sub_style),
        Spacer(1, 6 * mm),
        Paragraph("PAYROLL REPORT", ParagraphStyle("ReportHead", parent=styles["Heading2"], fontSize=13, textColor=dark_green)),
        Paragraph(
            f"Pay period: {lk_date(cycle['cycle_start'])} to {lk_date(cycle['cycle_end'])} "
            f"&middot; Due: {lk_date(cycle['due_date'])} &middot; Status: {cycle['status']}",
            sub_style,
        ),
        Spacer(1, 6 * mm),
    ]

    table_rows = [["Employee", "Hours", "Harvest (kg)", "Gross Pay", "Advance", "EPF", "Net Pay", "Method", "Status"]]
    for t in transactions:
        table_rows.append(
            [
                f"{t['full_name']} ({t['employee_number']})", t["total_hours"] or "—", t["total_output"] or "—",
                t["total_pay"] or "—", t["advance_total"] or "—", t["employee_epf"] or "—", t["net_pay"] or "—",
                t["payment_method"] or "—", t["payment_status"],
            ]
        )
    total_net = round(sum(t["net_pay"] or 0 for t in transactions), 2)
    total_gross = round(sum(t["total_pay"] or 0 for t in transactions), 2)
    table_rows.append(["Totals", "", "", total_gross, "", "", total_net, "", ""])

    available_width = page_width - 30 * mm
    col_widths = [w * available_width for w in (0.22, 0.09, 0.11, 0.11, 0.09, 0.08, 0.11, 0.1, 0.09)]
    report_table = Table(table_rows, colWidths=col_widths, repeatRows=1)
    report_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), green),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("GRID", (0, 0), (-1, -1), 0.4, border),
                ("ALIGN", (1, 0), (6, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 0.75, dark_green),
            ]
        )
    )
    elements.append(report_table)
    elements.append(Spacer(1, 12 * mm))

    sig_table = Table(
        [
            ["_________________________", "", "_________________________"],
            ["Prepared by", "", "Authorized by"],
            ["Name / Date", "", "Name / Date"],
        ],
        colWidths=[available_width * 0.4, available_width * 0.2, available_width * 0.4],
    )
    sig_table.setStyle(TableStyle([("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"), ("TEXTCOLOR", (0, 1), (-1, -1), muted)]))
    elements.append(sig_table)

    doc.build(elements, canvasmaker=NumberedCanvas)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"payroll_report_{cycle['cycle_start']}_to_{cycle['cycle_end']}.pdf",
    )


# ---------- Income & Profit ----------


def _income_date_range():
    """Resolve the view (daily/weekly/monthly/custom) into a concrete date_from/date_to,
    plus the anchor date to keep the date-picker showing what the user selected."""
    import calendar
    from datetime import date, timedelta

    view = request.args.get("view", "daily")
    if view not in ("daily", "weekly", "monthly", "custom"):
        view = "daily"

    today = date.today()

    if view == "custom":
        date_from = request.args.get("from", "").strip() or today.isoformat()
        date_to = request.args.get("to", "").strip() or today.isoformat()
        return view, date_from, date_to, date_from

    anchor_str = request.args.get("date", "").strip() or today.isoformat()
    anchor = date.fromisoformat(anchor_str)

    if view == "weekly":
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=6)
    elif view == "monthly":
        start = anchor.replace(day=1)
        end = anchor.replace(day=calendar.monthrange(anchor.year, anchor.month)[1])
    else:
        start = end = anchor

    return view, start.isoformat(), end.isoformat(), anchor_str


@app.route("/income", methods=["GET"])
def income():
    view, date_from, date_to, anchor_date = _income_date_range()

    conn = get_connection()

    price_row = conn.execute(
        "SELECT price_per_kg FROM daily_prices WHERE date = ?", (anchor_date,)
    ).fetchone()
    price_per_kg = price_row["price_per_kg"] if price_row else None

    harvest_kg = conn.execute(
        "SELECT COALESCE(SUM(actual_output), 0) AS total FROM work_assignments WHERE date BETWEEN ? AND ?",
        (date_from, date_to),
    ).fetchone()["total"]

    # Income multiplies each day's harvest by *that day's* price, since price changes daily —
    # a single price can't be applied across a range. Days with no price set are flagged, not guessed.
    income_row = conn.execute(
        """SELECT COALESCE(SUM(w.actual_output * dp.price_per_kg), 0) AS income,
                  COALESCE(SUM(CASE WHEN dp.price_per_kg IS NULL THEN w.actual_output ELSE 0 END), 0) AS unpriced_kg
           FROM work_assignments w
           LEFT JOIN daily_prices dp ON dp.date = w.date
           WHERE w.date BETWEEN ? AND ?""",
        (date_from, date_to),
    ).fetchone()
    unpriced_kg = income_row["unpriced_kg"]
    income_total = round(income_row["income"], 2) if harvest_kg > unpriced_kg else None
    priced_kg = harvest_kg - unpriced_kg
    avg_price = round(income_total / priced_kg, 2) if income_total is not None and priced_kg else None

    employee_income_rows = conn.execute(
        """SELECT w.employee_id, COALESCE(SUM(w.actual_output * dp.price_per_kg), 0) AS income_share
           FROM work_assignments w
           LEFT JOIN daily_prices dp ON dp.date = w.date
           WHERE w.date BETWEEN ? AND ?
           GROUP BY w.employee_id""",
        (date_from, date_to),
    ).fetchall()
    income_share_by_employee = {r["employee_id"]: r["income_share"] for r in employee_income_rows}

    employee_rows = _compute_payroll_rows(date_from, date_to)
    employee_cost = sum(r["true_labor_cost"] for r in employee_rows if r["true_labor_cost"]) or 0

    expenses = conn.execute(
        "SELECT * FROM expenses WHERE date BETWEEN ? AND ? ORDER BY date DESC, id DESC", (date_from, date_to)
    ).fetchall()
    expense_total = sum(e["amount"] for e in expenses) or 0

    total_cost = round(employee_cost + expense_total, 2)
    profit = round(income_total - total_cost, 2) if income_total is not None else None

    def pct(amount):
        return round(amount / total_cost * 100, 1) if total_cost and amount is not None else None

    breakdown = []
    for r in employee_rows:
        if not r["total_output"] and not r["total_pay"]:
            continue
        raw_share = income_share_by_employee.get(r["id"])
        income_share = round(raw_share, 2) if raw_share is not None and income_total is not None else None
        cost = r["true_labor_cost"] or 0
        breakdown.append(
            {
                "id": r["id"],
                "full_name": r["full_name"],
                "employee_number": r["employee_number"],
                "kg": r["total_output"],
                "cost": r["true_labor_cost"],
                "cost_percent": pct(cost),
                "income_share": income_share,
                "profit_share": round(income_share - cost, 2) if income_share is not None else None,
            }
        )

    expense_list = [
        {"id": e["id"], "date": e["date"], "category": e["category"], "amount": e["amount"], "note": e["note"], "percent": pct(e["amount"])}
        for e in expenses
    ]

    # Percentage-of-cost breakdown: employee pay + each expense category, as a share of total_cost.
    cost_breakdown = []
    if total_cost:
        if employee_cost:
            cost_breakdown.append({"label": "Employee Pay", "amount": round(employee_cost, 2), "percent": pct(employee_cost)})
        by_category = {}
        for e in expenses:
            by_category[e["category"]] = by_category.get(e["category"], 0) + e["amount"]
        for category, amount in sorted(by_category.items(), key=lambda kv: -kv[1]):
            cost_breakdown.append({"label": category, "amount": round(amount, 2), "percent": pct(amount)})

    return render_template(
        "income.html",
        view=view,
        selected_date=anchor_date,
        date_from=date_from,
        date_to=date_to,
        price_per_kg=price_per_kg,
        avg_price=avg_price,
        harvest_kg=harvest_kg,
        unpriced_kg=unpriced_kg,
        employee_cost=round(employee_cost, 2),
        employee_cost_percent=pct(employee_cost),
        expense_total=round(expense_total, 2),
        expense_total_percent=pct(expense_total),
        total_cost=total_cost,
        income_total=income_total,
        profit=profit,
        breakdown=breakdown,
        expenses=expense_list,
        cost_breakdown=cost_breakdown,
    )


@app.route("/income/expense", methods=["POST"])
def income_add_expense():
    date_value = request.form.get("date", "").strip()
    category = request.form.get("category", "").strip()
    amount_value = request.form.get("amount", "").strip()
    note = request.form.get("note", "").strip() or None
    payment_method = request.form.get("payment_method", "").strip() or None

    if category not in EXPENSE_CATEGORIES:
        flash("Invalid expense category.", "error")
        return redirect(url_for("income", date=date_value))

    try:
        amount = float(amount_value)
    except ValueError:
        flash("Expense amount must be a number.", "error")
        return redirect(url_for("income", date=date_value))

    conn = get_connection()
    conn.execute(
        "INSERT INTO expenses (date, category, amount, note, payment_method, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (date_value, category, amount, note, payment_method, session.get("user_id")),
    )
    conn.commit()
    flash(f"{category} expense of {amount} added for {date_value}.", "success")
    return redirect(url_for("income", date=date_value))


@app.route("/income/expense/<int:expense_id>/delete", methods=["POST"])
def income_delete_expense(expense_id):
    conn = get_connection()
    expense = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if not expense:
        flash("Expense not found.", "error")
        return redirect(url_for("income"))

    conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    flash("Expense removed.", "success")
    return redirect(url_for("income", date=expense["date"]))


@app.route("/income/price", methods=["POST"])
def income_set_price():
    date_value = request.form.get("date", "").strip()
    price_value = request.form.get("price_per_kg", "").strip()

    try:
        price = float(price_value)
    except ValueError:
        flash("Tea price must be a number.", "error")
        return redirect(url_for("income", date=date_value))

    conn = get_connection()
    conn.execute(
        """INSERT INTO daily_prices (date, price_per_kg, created_by, updated_at) VALUES (?, ?, ?, ?)
           ON CONFLICT(date) DO UPDATE SET price_per_kg = excluded.price_per_kg,
               created_by = excluded.created_by, updated_at = excluded.updated_at""",
        (date_value, price, session.get("user_id"), _now_text()),
    )
    conn.commit()
    flash(f"Tea price for {date_value} set to {price}.", "success")
    return redirect(url_for("income", date=date_value))


# ---------- Finance & Factory ----------


@app.route("/finance")
def finance_dashboard():
    view, date_from, date_to, anchor_date = _income_date_range()

    conn = get_connection()

    revenue_row = conn.execute(
        """SELECT COALESCE(SUM(total_amount), 0) AS total, COALESCE(SUM(total_weight), 0) AS weight
           FROM invoices WHERE invoice_date BETWEEN ? AND ?""",
        (date_from, date_to),
    ).fetchone()
    total_revenue = revenue_row["total"]
    total_invoiced_weight = revenue_row["weight"]

    payments_received = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM invoice_payments WHERE payment_date BETWEEN ? AND ?",
        (date_from, date_to),
    ).fetchone()["total"]

    outstanding_receivables = conn.execute(
        """SELECT COALESCE(SUM(i.total_amount - COALESCE(p.paid, 0)), 0) AS total
           FROM invoices i
           LEFT JOIN (SELECT invoice_id, SUM(amount) AS paid FROM invoice_payments GROUP BY invoice_id) p
             ON p.invoice_id = i.id
           WHERE i.status != 'Paid'"""
    ).fetchone()["total"]

    deliveries_row = conn.execute(
        """SELECT COUNT(*) AS c, COALESCE(SUM(factory_weight), 0) AS weight
           FROM factory_deliveries WHERE delivery_date BETWEEN ? AND ?""",
        (date_from, date_to),
    ).fetchone()

    expense_total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE date BETWEEN ? AND ?", (date_from, date_to)
    ).fetchone()["total"]

    payroll_rows = _compute_payroll_rows(date_from, date_to)
    payroll_cost = sum(r["true_labor_cost"] for r in payroll_rows if r["true_labor_cost"]) or 0

    total_cost = round(expense_total + payroll_cost, 2)
    net_profit = round(total_revenue - total_cost, 2)

    recent_invoices = conn.execute(
        """SELECT i.*, f.name AS factory_name FROM invoices i JOIN factories f ON f.id = i.factory_id
           WHERE i.invoice_date BETWEEN ? AND ? ORDER BY i.invoice_date DESC, i.id DESC LIMIT 10""",
        (date_from, date_to),
    ).fetchall()

    from datetime import date, timedelta

    span_start = date.fromisoformat(date_from)
    span_end = date.fromisoformat(date_to)
    span_days = (span_end - span_start).days + 1

    trend_labels, trend_revenue, trend_cost = [], [], []
    if 0 < span_days <= 62:
        for i in range(span_days):
            d = (span_start + timedelta(days=i)).isoformat()
            trend_labels.append(d[5:])  # MM-DD
            rev = conn.execute(
                "SELECT COALESCE(SUM(total_amount), 0) AS total FROM invoices WHERE invoice_date = ?", (d,)
            ).fetchone()["total"]
            trend_revenue.append(round(rev, 2))
            day_expense = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses WHERE date = ?", (d,)
            ).fetchone()["total"]
            day_payroll_rows = _compute_payroll_rows(d, d)
            day_payroll = sum(r["true_labor_cost"] for r in day_payroll_rows if r["true_labor_cost"]) or 0
            trend_cost.append(round(day_expense + day_payroll, 2))

    return render_template(
        "finance_dashboard.html",
        view=view,
        selected_date=anchor_date,
        date_from=date_from,
        date_to=date_to,
        total_revenue=round(total_revenue, 2),
        total_invoiced_weight=round(total_invoiced_weight, 2),
        payments_received=round(payments_received, 2),
        outstanding_receivables=round(outstanding_receivables, 2),
        deliveries_count=deliveries_row["c"],
        deliveries_weight=round(deliveries_row["weight"], 2),
        expense_total=round(expense_total, 2),
        payroll_cost=round(payroll_cost, 2),
        total_cost=total_cost,
        net_profit=net_profit,
        recent_invoices=recent_invoices,
        trend_labels=trend_labels,
        trend_revenue=trend_revenue,
        trend_cost=trend_cost,
    )


@app.route("/finance/factories")
def factory_list():
    conn = get_connection()
    factories = conn.execute(
        """SELECT f.*,
                  (SELECT COUNT(*) FROM factory_deliveries d WHERE d.factory_id = f.id) AS delivery_count
           FROM factories f ORDER BY f.name"""
    ).fetchall()
    return render_template("factories.html", factories=factories)


@app.route("/finance/factories/new", methods=["GET", "POST"])
def factory_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        contact_person = request.form.get("contact_person", "").strip() or None
        phone_number = request.form.get("phone_number", "").strip() or None
        address = request.form.get("address", "").strip() or None
        default_price_value = request.form.get("default_price_per_kg", "").strip()

        if not name:
            flash("Factory name is required.", "error")
            return render_template("factory_form.html", factory=request.form, mode="new")

        try:
            default_price_per_kg = float(default_price_value) if default_price_value else None
        except ValueError:
            flash("Default price must be a number.", "error")
            return render_template("factory_form.html", factory=request.form, mode="new")

        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO factories (name, contact_person, phone_number, address, default_price_per_kg)
                   VALUES (?, ?, ?, ?, ?)""",
                (name, contact_person, phone_number, address, default_price_per_kg),
            )
            conn.commit()
        except IntegrityError:
            flash("A factory with that name already exists.", "error")
            return render_template("factory_form.html", factory=request.form, mode="new")

        flash(f"Factory {name} added.", "success")
        return redirect(url_for("factory_list"))

    return render_template("factory_form.html", factory={}, mode="new")


@app.route("/finance/factories/<int:factory_id>/edit", methods=["GET", "POST"])
def factory_edit(factory_id):
    conn = get_connection()
    factory = conn.execute("SELECT * FROM factories WHERE id = ?", (factory_id,)).fetchone()
    if not factory:
        flash("Factory not found.", "error")
        return redirect(url_for("factory_list"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        contact_person = request.form.get("contact_person", "").strip() or None
        phone_number = request.form.get("phone_number", "").strip() or None
        address = request.form.get("address", "").strip() or None
        default_price_value = request.form.get("default_price_per_kg", "").strip()

        if not name:
            flash("Factory name is required.", "error")
            merged = dict(factory)
            merged.update(request.form)
            return render_template("factory_form.html", factory=merged, mode="edit", factory_id=factory_id)

        try:
            default_price_per_kg = float(default_price_value) if default_price_value else None
        except ValueError:
            flash("Default price must be a number.", "error")
            merged = dict(factory)
            merged.update(request.form)
            return render_template("factory_form.html", factory=merged, mode="edit", factory_id=factory_id)

        try:
            conn.execute(
                """UPDATE factories SET name=?, contact_person=?, phone_number=?, address=?, default_price_per_kg=?
                   WHERE id=?""",
                (name, contact_person, phone_number, address, default_price_per_kg, factory_id),
            )
            conn.commit()
        except IntegrityError:
            flash("A factory with that name already exists.", "error")
            merged = dict(factory)
            merged.update(request.form)
            return render_template("factory_form.html", factory=merged, mode="edit", factory_id=factory_id)

        flash("Factory updated.", "success")
        return redirect(url_for("factory_list"))

    return render_template("factory_form.html", factory=dict(factory), mode="edit", factory_id=factory_id)


@app.route("/finance/factories/<int:factory_id>/delete", methods=["POST"])
def factory_delete(factory_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM factories WHERE id = ?", (factory_id,))
        conn.commit()
    except IntegrityError:
        flash("Cannot delete a factory that has deliveries or invoices recorded.", "error")
        return redirect(url_for("factory_list"))
    flash("Factory removed.", "success")
    return redirect(url_for("factory_list"))


@app.route("/finance/deliveries")
def delivery_list():
    date_filter = request.args.get("date", "").strip()
    conn = get_connection()
    query = """SELECT d.*, f.name AS factory_name FROM factory_deliveries d
               JOIN factories f ON f.id = d.factory_id"""
    params = []
    if date_filter:
        query += " WHERE d.delivery_date = ?"
        params.append(date_filter)
    query += " ORDER BY d.delivery_date DESC, d.id DESC"
    deliveries = conn.execute(query, params).fetchall()
    return render_template("deliveries.html", deliveries=deliveries, date_filter=date_filter)


@app.route("/finance/deliveries/new", methods=["GET", "POST"])
def delivery_new():
    conn = get_connection()
    factories = conn.execute("SELECT * FROM factories ORDER BY name").fetchall()

    if request.method == "POST":
        delivery_date = request.form.get("delivery_date", "").strip()
        factory_id = request.form.get("factory_id", "").strip()
        estate_weight_value = request.form.get("estate_weight", "").strip()
        factory_weight_value = request.form.get("factory_weight", "").strip()
        vehicle_number = request.form.get("vehicle_number", "").strip() or None
        driver_name = request.form.get("driver_name", "").strip() or None
        notes = request.form.get("notes", "").strip() or None

        if not delivery_date or not factory_id:
            flash("Date and factory are required.", "error")
            return render_template("delivery_form.html", factories=factories, delivery=request.form, mode="new")

        try:
            factory_weight = float(factory_weight_value)
            estate_weight = float(estate_weight_value) if estate_weight_value else None
        except ValueError:
            flash("Weights must be numbers.", "error")
            return render_template("delivery_form.html", factories=factories, delivery=request.form, mode="new")

        if estate_weight is None:
            auto_row = conn.execute(
                "SELECT SUM(actual_output) AS total FROM work_assignments WHERE date = ?", (delivery_date,)
            ).fetchone()
            estate_weight = auto_row["total"]

        conn.execute(
            """INSERT INTO factory_deliveries
               (delivery_date, factory_id, estate_weight, factory_weight, vehicle_number, driver_name, notes, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (delivery_date, factory_id, estate_weight, factory_weight, vehicle_number, driver_name, notes, session.get("user_id")),
        )
        conn.commit()
        flash("Delivery recorded.", "success")
        return redirect(url_for("delivery_list"))

    return render_template("delivery_form.html", factories=factories, delivery={}, mode="new")


@app.route("/finance/deliveries/<int:delivery_id>/delete", methods=["POST"])
def delivery_delete(delivery_id):
    conn = get_connection()
    delivery = conn.execute("SELECT * FROM factory_deliveries WHERE id = ?", (delivery_id,)).fetchone()
    if not delivery:
        flash("Delivery not found.", "error")
        return redirect(url_for("delivery_list"))
    if delivery["invoice_id"]:
        flash("This delivery is already on an invoice — remove it from the invoice first.", "error")
        return redirect(url_for("delivery_list"))

    conn.execute("DELETE FROM factory_deliveries WHERE id = ?", (delivery_id,))
    conn.commit()
    flash("Delivery removed.", "success")
    return redirect(url_for("delivery_list"))


@app.route("/finance/invoices")
def invoice_list():
    conn = get_connection()
    invoices = conn.execute(
        """SELECT i.*, f.name AS factory_name,
                  COALESCE((SELECT SUM(amount) FROM invoice_payments WHERE invoice_id = i.id), 0) AS paid_total
           FROM invoices i JOIN factories f ON f.id = i.factory_id
           ORDER BY i.invoice_date DESC, i.id DESC"""
    ).fetchall()
    return render_template("invoices.html", invoices=invoices)


@app.route("/finance/invoices/new", methods=["GET", "POST"])
def invoice_new():
    from datetime import date

    conn = get_connection()
    factories = conn.execute("SELECT * FROM factories ORDER BY name").fetchall()

    if request.method == "POST":
        factory_id = request.form.get("factory_id", "").strip()
        date_from = request.form.get("from", "").strip()
        date_to = request.form.get("to", "").strip()
        delivery_ids_raw = request.form.getlist("delivery_ids")
        invoice_number = request.form.get("invoice_number", "").strip()
        invoice_date = request.form.get("invoice_date", "").strip()
        price_value = request.form.get("price_per_kg", "").strip()

        if not delivery_ids_raw:
            flash("Select at least one delivery to invoice.", "error")
            return redirect(url_for("invoice_new", factory_id=factory_id, **{"from": date_from, "to": date_to}))
        if not invoice_number or not invoice_date:
            flash("Invoice number and date are required.", "error")
            return redirect(url_for("invoice_new", factory_id=factory_id, **{"from": date_from, "to": date_to}))
        try:
            price_per_kg = float(price_value)
            delivery_ids = [int(x) for x in delivery_ids_raw]
        except ValueError:
            flash("Price per kg must be a number.", "error")
            return redirect(url_for("invoice_new", factory_id=factory_id, **{"from": date_from, "to": date_to}))

        placeholders = ",".join("?" * len(delivery_ids))
        selected_deliveries = conn.execute(
            f"SELECT * FROM factory_deliveries WHERE id IN ({placeholders}) AND invoice_id IS NULL",
            delivery_ids,
        ).fetchall()
        if not selected_deliveries:
            flash("Those deliveries are no longer available to invoice.", "error")
            return redirect(url_for("invoice_list"))

        total_weight = round(sum(d["factory_weight"] for d in selected_deliveries), 2)
        total_amount = round(total_weight * price_per_kg, 2)

        try:
            cursor = conn.execute(
                """INSERT INTO invoices
                   (invoice_number, factory_id, invoice_date, price_per_kg, total_weight, total_amount, created_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (invoice_number, factory_id, invoice_date, price_per_kg, total_weight, total_amount, session.get("user_id")),
            )
        except IntegrityError:
            flash("An invoice with that number already exists.", "error")
            return redirect(url_for("invoice_new", factory_id=factory_id, **{"from": date_from, "to": date_to}))

        new_invoice_id = cursor.lastrowid
        conn.execute(
            f"UPDATE factory_deliveries SET invoice_id = ? WHERE id IN ({placeholders})",
            [new_invoice_id] + delivery_ids,
        )
        conn.commit()
        flash(f"Invoice {invoice_number} created: {total_weight} kg = {total_amount}.", "success")
        return redirect(url_for("invoice_detail", invoice_id=new_invoice_id))

    factory_id = request.args.get("factory_id", "").strip()
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()

    uninvoiced = []
    selected_factory = None
    if factory_id and date_from and date_to:
        selected_factory = conn.execute("SELECT * FROM factories WHERE id = ?", (factory_id,)).fetchone()
        uninvoiced = conn.execute(
            """SELECT * FROM factory_deliveries
               WHERE factory_id = ? AND invoice_id IS NULL AND delivery_date BETWEEN ? AND ?
               ORDER BY delivery_date""",
            (factory_id, date_from, date_to),
        ).fetchall()

    return render_template(
        "invoice_form.html",
        factories=factories,
        factory_id=factory_id,
        date_from=date_from,
        date_to=date_to,
        uninvoiced=uninvoiced,
        default_price=selected_factory["default_price_per_kg"] if selected_factory else None,
        today=date.today().isoformat(),
    )


@app.route("/finance/invoices/<int:invoice_id>")
def invoice_detail(invoice_id):
    conn = get_connection()
    invoice = conn.execute(
        """SELECT i.*, f.name AS factory_name FROM invoices i
           JOIN factories f ON f.id = i.factory_id WHERE i.id = ?""",
        (invoice_id,),
    ).fetchone()
    if not invoice:
        flash("Invoice not found.", "error")
        return redirect(url_for("invoice_list"))

    deliveries = conn.execute(
        "SELECT * FROM factory_deliveries WHERE invoice_id = ? ORDER BY delivery_date", (invoice_id,)
    ).fetchall()
    payments = conn.execute(
        "SELECT * FROM invoice_payments WHERE invoice_id = ? ORDER BY payment_date DESC, id DESC", (invoice_id,)
    ).fetchall()
    paid_total = round(sum(p["amount"] for p in payments), 2)
    balance_due = round(invoice["total_amount"] - paid_total, 2)

    return render_template(
        "invoice_detail.html",
        invoice=invoice,
        deliveries=deliveries,
        payments=payments,
        paid_total=paid_total,
        balance_due=balance_due,
    )


@app.route("/finance/invoices/<int:invoice_id>/pdf")
def invoice_pdf(invoice_id):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    conn = get_connection()
    invoice = conn.execute(
        """SELECT i.*, f.name AS factory_name, f.contact_person, f.phone_number, f.address
           FROM invoices i JOIN factories f ON f.id = i.factory_id WHERE i.id = ?""",
        (invoice_id,),
    ).fetchone()
    if not invoice:
        flash("Invoice not found.", "error")
        return redirect(url_for("invoice_list"))

    deliveries = conn.execute(
        "SELECT * FROM factory_deliveries WHERE invoice_id = ? ORDER BY delivery_date", (invoice_id,)
    ).fetchall()
    payments = conn.execute(
        "SELECT * FROM invoice_payments WHERE invoice_id = ? ORDER BY payment_date", (invoice_id,)
    ).fetchall()
    paid_total = round(sum(p["amount"] for p in payments), 2)
    balance_due = round(invoice["total_amount"] - paid_total, 2)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm, leftMargin=20 * mm, rightMargin=20 * mm
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("InvoiceTitle", parent=styles["Title"], fontSize=18, spaceAfter=2)
    sub_style = ParagraphStyle("InvoiceSub", parent=styles["Normal"], textColor=colors.HexColor("#6b7a67"))
    green = colors.HexColor("#2f5d3a")
    border = colors.HexColor("#dbe5d9")

    elements = [
        Paragraph("DKNS Tea Lands", title_style),
        Paragraph(f"Invoice {invoice['invoice_number']}", sub_style),
        Spacer(1, 10 * mm),
    ]

    info_table = Table(
        [
            ["Factory", invoice["factory_name"]],
            ["Contact", invoice["contact_person"] or "—"],
            ["Phone", invoice["phone_number"] or "—"],
            ["Address", invoice["address"] or "—"],
            ["Invoice date", invoice["invoice_date"]],
            ["Status", invoice["status"]],
        ],
        colWidths=[40 * mm, 120 * mm],
    )
    info_table.setStyle(TableStyle([("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    elements.append(info_table)
    elements.append(Spacer(1, 8 * mm))

    delivery_rows = [["Date", "Estate weight (kg)", "Factory weight (kg)", "Vehicle"]]
    for d in deliveries:
        delivery_rows.append(
            [d["delivery_date"], d["estate_weight"] if d["estate_weight"] is not None else "—", d["factory_weight"], d["vehicle_number"] or "—"]
        )
    delivery_table = Table(delivery_rows, colWidths=[35 * mm, 40 * mm, 40 * mm, 45 * mm])
    delivery_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), green),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, border),
                ("ALIGN", (1, 0), (2, -1), "RIGHT"),
            ]
        )
    )
    elements.append(delivery_table)
    elements.append(Spacer(1, 8 * mm))

    totals_rows = [
        ["Total weight (kg)", invoice["total_weight"]],
        ["Price per kg", invoice["price_per_kg"]],
        ["Total amount", invoice["total_amount"]],
        ["Paid so far", paid_total],
        ["Balance due", balance_due],
    ]
    totals_table = Table(totals_rows, colWidths=[80 * mm, 40 * mm])
    totals_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, border),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )
    elements.append(totals_table)

    if payments:
        elements.append(Spacer(1, 8 * mm))
        payment_rows = [["Payment date", "Amount", "Method", "Reference"]]
        for p in payments:
            payment_rows.append([p["payment_date"], p["amount"], p["method"], p["reference_number"] or "—"])
        payment_table = Table(payment_rows, colWidths=[35 * mm, 30 * mm, 40 * mm, 55 * mm])
        payment_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), green),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.5, border),
                    ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ]
            )
        )
        elements.append(payment_table)

    doc.build(elements)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"invoice_{invoice['invoice_number']}.pdf",
    )


@app.route("/finance/invoices/<int:invoice_id>/delete", methods=["POST"])
def invoice_delete(invoice_id):
    conn = get_connection()
    conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    conn.commit()
    flash("Invoice removed. Its deliveries are now available to invoice again.", "success")
    return redirect(url_for("invoice_list"))


def _recompute_invoice_status(conn, invoice_id, total_amount):
    paid_total = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM invoice_payments WHERE invoice_id = ?", (invoice_id,)
    ).fetchone()["total"]
    if paid_total > 0 and paid_total >= total_amount:
        new_status = "Paid"
    elif paid_total > 0:
        new_status = "Partially Paid"
    else:
        new_status = "Unpaid"
    conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (new_status, invoice_id))


@app.route("/finance/invoices/<int:invoice_id>/payment", methods=["POST"])
def invoice_add_payment(invoice_id):
    conn = get_connection()
    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not invoice:
        flash("Invoice not found.", "error")
        return redirect(url_for("invoice_list"))

    payment_date = request.form.get("payment_date", "").strip()
    amount_value = request.form.get("amount", "").strip()
    method = request.form.get("method", "").strip()
    reference_number = request.form.get("reference_number", "").strip() or None
    note = request.form.get("note", "").strip() or None

    if not payment_date or method not in PAYMENT_METHODS:
        flash("Payment date and a valid method are required.", "error")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))

    try:
        amount = float(amount_value)
    except ValueError:
        flash("Payment amount must be a number.", "error")
        return redirect(url_for("invoice_detail", invoice_id=invoice_id))

    conn.execute(
        """INSERT INTO invoice_payments (invoice_id, payment_date, amount, method, reference_number, note, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (invoice_id, payment_date, amount, method, reference_number, note, session.get("user_id")),
    )
    _recompute_invoice_status(conn, invoice_id, invoice["total_amount"])
    conn.commit()

    flash(f"Payment of {amount} recorded via {method}.", "success")
    return redirect(url_for("invoice_detail", invoice_id=invoice_id))


@app.route("/finance/invoices/<int:invoice_id>/payment/<int:payment_id>/delete", methods=["POST"])
def invoice_delete_payment(invoice_id, payment_id):
    conn = get_connection()
    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not invoice:
        flash("Invoice not found.", "error")
        return redirect(url_for("invoice_list"))

    conn.execute("DELETE FROM invoice_payments WHERE id = ? AND invoice_id = ?", (payment_id, invoice_id))
    _recompute_invoice_status(conn, invoice_id, invoice["total_amount"])
    conn.commit()

    flash("Payment removed.", "success")
    return redirect(url_for("invoice_detail", invoice_id=invoice_id))


# ---------- Finance & Factory Statement (unified ledger) ----------

TRANSACTION_TYPES = ["Expense", "Payroll", "Salary Advance", "Delivery", "Invoice", "Factory Payment", "Depreciation"]


def _build_payroll_ledger_entries(conn):
    """One Payroll debit entry per employee per processed pay cycle. Sourced
    from the frozen payroll_transactions snapshot rather than computed live,
    so payroll only becomes a real ledger expense once a cycle has actually
    been marked Paid on its weekly Monday pay date — a still-in-progress,
    unpaid cycle isn't a committed cost yet and doesn't appear here at all."""
    entries = []
    rows = conn.execute(
        """SELECT t.* FROM payroll_transactions t
           JOIN payroll_cycles c ON c.id = t.cycle_id
           ORDER BY t.payment_date"""
    ).fetchall()

    for t in rows:
        true_cost = round((t["total_pay"] or 0) + (t["employer_epf"] or 0) + (t["employer_etf"] or 0), 2)
        if true_cost <= 0 or not t["payment_date"]:
            continue  # no date to place it on the timeline (e.g. cleared during a payment correction)

        note = ""
        if t["employer_epf"] or t["employer_etf"]:
            note = f" (incl. employer EPF/ETF {round((t['employer_epf'] or 0) + (t['employer_etf'] or 0), 2)})"

        entries.append(
            {
                "date": t["payment_date"],
                "type": "Payroll",
                "reference_no": f"PAY-{t['id']}",
                "description": f"Payroll — {t['full_name']} ({t['employee_number']}): cycle processed{note}",
                "debit": true_cost,
                "credit": 0,
                "payment_method": t["payment_method"],
                "user": None,
                "factory": None,
                "factory_id": None,
                "employee": t["full_name"],
                "employee_id": t["employee_id"],
                "status": t["payment_status"],
            }
        )
    return entries


def _build_ledger(conn):
    """Normalizes every financial event across the app (expenses, payroll cost,
    salary advances, factory deliveries, invoices, and invoice payments) into
    one common transaction shape, sorted chronologically. This is computed on
    the fly from the existing source tables rather than stored in its own
    table, so it can never drift out of sync with them.

    Revenue is recognized only when a factory *payment* is actually received
    (not when tea is harvested or an invoice is merely raised) — accrued daily
    harvest income was deliberately removed, since it isn't real cash until the
    factory pays the invoice. Payroll cost is likewise only recognized once a
    pay cycle has been processed and marked Paid (see _build_payroll_ledger_entries)
    — an unpaid, still-in-progress cycle isn't a committed expense yet."""
    usernames = {u["id"]: u["username"] for u in conn.execute("SELECT id, username FROM users").fetchall()}

    entries = []

    for r in conn.execute("SELECT * FROM expenses").fetchall():
        desc = r["category"]
        if r["note"]:
            desc += f" — {r['note']}"
        entries.append(
            {
                "date": r["date"],
                "type": "Expense",
                "reference_no": f"EXP-{r['id']}",
                "description": desc,
                "debit": round(r["amount"], 2),
                "credit": 0,
                "payment_method": r["payment_method"],
                "user": usernames.get(r["created_by"]),
                "factory": None,
                "factory_id": None,
                "employee": None,
                "employee_id": None,
                "status": None,
            }
        )

    entries.extend(_build_payroll_ledger_entries(conn))
    entries.extend(_build_depreciation_ledger_entries(conn))

    for r in conn.execute(
        """SELECT sa.*, e.full_name, e.employee_number FROM salary_advances sa
           JOIN employees e ON e.id = sa.employee_id"""
    ).fetchall():
        desc = f"Salary advance — {r['full_name']} ({r['employee_number']})"
        if r["note"]:
            desc += f" — {r['note']}"
        entries.append(
            {
                "date": r["date"],
                "type": "Salary Advance",
                "reference_no": f"ADV-{r['id']}",
                "description": desc,
                "debit": round(r["amount"], 2),
                "credit": 0,
                "payment_method": r["payment_method"],
                "user": usernames.get(r["created_by"]),
                "factory": None,
                "factory_id": None,
                "employee": r["full_name"],
                "employee_id": r["employee_id"],
                "status": None,
            }
        )

    for r in conn.execute(
        "SELECT d.*, f.name AS factory_name FROM factory_deliveries d JOIN factories f ON f.id = d.factory_id"
    ).fetchall():
        status_note = "invoiced" if r["invoice_id"] else "awaiting invoice"
        entries.append(
            {
                "date": r["delivery_date"],
                "type": "Delivery",
                "reference_no": f"DEL-{r['id']}",
                "description": f"Delivery to {r['factory_name']} — {r['factory_weight']} kg ({status_note})",
                "debit": 0,
                "credit": 0,
                "payment_method": None,
                "user": usernames.get(r["created_by"]),
                "factory": r["factory_name"],
                "factory_id": r["factory_id"],
                "employee": None,
                "employee_id": None,
                "status": None,
            }
        )

    for r in conn.execute(
        "SELECT i.*, f.name AS factory_name FROM invoices i JOIN factories f ON f.id = i.factory_id"
    ).fetchall():
        entries.append(
            {
                "date": r["invoice_date"],
                "type": "Invoice",
                "reference_no": f"INV-{r['invoice_number']}",
                "description": (
                    f"Invoice raised to {r['factory_name']} — {r['total_weight']} kg "
                    f"@ {r['price_per_kg']}/kg = {r['total_amount']}"
                ),
                "debit": 0,
                "credit": 0,
                "payment_method": None,
                "user": usernames.get(r["created_by"]),
                "factory": r["factory_name"],
                "factory_id": r["factory_id"],
                "employee": None,
                "employee_id": None,
                "status": r["status"],
            }
        )

    for r in conn.execute(
        """SELECT p.*, i.invoice_number, i.status AS invoice_status, f.name AS factory_name, f.id AS factory_id
           FROM invoice_payments p
           JOIN invoices i ON i.id = p.invoice_id
           JOIN factories f ON f.id = i.factory_id"""
    ).fetchall():
        desc = f"Payment received — Invoice {r['invoice_number']} ({r['factory_name']})"
        if r["note"]:
            desc += f" — {r['note']}"
        entries.append(
            {
                "date": r["payment_date"],
                "type": "Factory Payment",
                "reference_no": f"PMT-{r['id']}",
                "description": desc,
                "debit": 0,
                "credit": round(r["amount"], 2),
                "payment_method": r["method"],
                "user": usernames.get(r["created_by"]),
                "factory": r["factory_name"],
                "factory_id": r["factory_id"],
                "employee": None,
                "employee_id": None,
                "status": r["invoice_status"],
            }
        )

    entries.sort(key=lambda t: (t["date"], t["type"], t["reference_no"]))
    return entries


def _get_statement_data():
    """Shared by the HTML page, CSV export, and PDF export, so all three always
    show exactly the same filtered rows and the same opening/closing balance."""
    view, date_from, date_to, anchor_date = _income_date_range()
    conn = get_connection()

    txn_type = request.args.get("type", "").strip()
    factory_id = request.args.get("factory_id", "").strip()
    employee_id = request.args.get("employee_id", "").strip()
    payment_status = request.args.get("payment_status", "").strip()
    search = request.args.get("q", "").strip().lower()
    sort_by = request.args.get("sort", "date").strip()
    sort_dir = request.args.get("dir", "asc").strip()

    all_entries = _build_ledger(conn)

    def matches_filters(t):
        if txn_type and t["type"] != txn_type:
            return False
        if factory_id and str(t.get("factory_id")) != factory_id:
            return False
        if employee_id and str(t.get("employee_id")) != employee_id:
            return False
        if payment_status:
            if t["type"] not in ("Invoice", "Factory Payment") or t.get("status") != payment_status:
                return False
        return True

    filtered = [t for t in all_entries if matches_filters(t)]
    before_range = [t for t in filtered if t["date"] < date_from]
    in_range = [t for t in filtered if date_from <= t["date"] <= date_to]

    opening_balance = round(sum(t["credit"] - t["debit"] for t in before_range), 2)

    running = opening_balance
    for seq, t in enumerate(in_range):
        running = round(running + t["credit"] - t["debit"], 2)
        t["balance"] = running
        t["_seq"] = seq  # preserves the balance-computation order as the default display order

    total_debit = round(sum(t["debit"] for t in in_range), 2)
    total_credit = round(sum(t["credit"] for t in in_range), 2)
    closing_balance = round(opening_balance + total_credit - total_debit, 2)

    rows = in_range
    if search:
        rows = [t for t in rows if search in t["description"].lower() or search in t["reference_no"].lower()]

    sort_key_map = {
        "date": lambda t: (t["date"], t["_seq"]),
        "reference_no": lambda t: t["reference_no"],
        "type": lambda t: t["type"],
        "debit": lambda t: t["debit"],
        "credit": lambda t: t["credit"],
        "balance": lambda t: t["balance"],
    }
    rows = sorted(rows, key=sort_key_map.get(sort_by, sort_key_map["date"]), reverse=(sort_dir == "desc"))

    factories = conn.execute("SELECT * FROM factories ORDER BY name").fetchall()
    employees = conn.execute("SELECT * FROM employees ORDER BY full_name").fetchall()

    return {
        "view": view,
        "selected_date": anchor_date,
        "date_from": date_from,
        "date_to": date_to,
        "txn_type": txn_type,
        "factory_id": factory_id,
        "employee_id": employee_id,
        "payment_status": payment_status,
        "search": search,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "rows": rows,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "factories": factories,
        "employees": employees,
        "transaction_types": TRANSACTION_TYPES,
    }


@app.route("/finance/statement")
def finance_statement():
    return render_template("finance_statement.html", **_get_statement_data())


@app.route("/finance/statement/export.csv")
def finance_statement_csv():
    import csv

    data = _get_statement_data()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Date", "Reference No.", "Type", "Description", "Debit", "Credit", "Balance", "Payment Method", "User"]
    )
    for t in data["rows"]:
        writer.writerow(
            [
                t["date"], t["reference_no"], t["type"], t["description"],
                t["debit"] or "", t["credit"] or "", t["balance"],
                t["payment_method"] or "", t["user"] or "",
            ]
        )
    writer.writerow([])
    writer.writerow(["", "", "", "Opening Balance", "", "", data["opening_balance"], "", ""])
    writer.writerow(["", "", "", "Total", data["total_debit"], data["total_credit"], "", "", ""])
    writer.writerow(["", "", "", "Closing Balance", "", "", data["closing_balance"], "", ""])

    csv_bytes = io.BytesIO(buffer.getvalue().encode("utf-8"))
    return send_file(
        csv_bytes,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"statement_{data['date_from']}_to_{data['date_to']}.csv",
    )


@app.route("/finance/statement/pdf")
def finance_statement_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.pdfgen import canvas as pdfcanvas

    data = _get_statement_data()
    orientation = request.args.get("orientation", "landscape").strip()
    pagesize = landscape(A4) if orientation == "landscape" else A4
    page_width = pagesize[0]

    green = colors.HexColor("#2f5d3a")
    dark_green = colors.HexColor("#1f4028")
    border = colors.HexColor("#dbe5d9")
    muted = colors.HexColor("#6b7a67")

    class NumberedCanvas(pdfcanvas.Canvas):
        """Defers writing 'Page X of Y' until save(), since the total page count
        isn't known while pages are still being drawn."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total_pages = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self.setFont("Helvetica", 8)
                self.setFillColor(muted)
                self.drawRightString(
                    page_width - 15 * mm, 10 * mm, f"Page {self._pageNumber} of {total_pages}"
                )
                super().showPage()
            super().save()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=pagesize, topMargin=15 * mm, bottomMargin=18 * mm, leftMargin=15 * mm, rightMargin=15 * mm
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("StatementTitle", parent=styles["Title"], fontSize=18, spaceAfter=0, textColor=dark_green)
    sub_style = ParagraphStyle("StatementSub", parent=styles["Normal"], textColor=muted, fontSize=9)
    section_style = ParagraphStyle("StatementSection", parent=styles["Heading2"], fontSize=11, textColor=dark_green)

    elements = []

    header_table = Table(
        [
            [
                Table([["DKNS"]], colWidths=[16 * mm], rowHeights=[16 * mm],
                      style=TableStyle([
                          ("BACKGROUND", (0, 0), (-1, -1), green),
                          ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                          ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                          ("FONTSIZE", (0, 0), (-1, -1), 9),
                          ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                          ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                      ])),
                Paragraph(
                    f"{COMPANY_NAME}<br/><font size=9 color='#6b7a67'>{COMPANY_TAGLINE}</font>", title_style
                ),
                Paragraph(
                    f"{COMPANY_ADDRESS}<br/>{COMPANY_PHONE} &middot; {COMPANY_EMAIL}", sub_style
                ),
            ]
        ],
        colWidths=[20 * mm, page_width - 20 * mm - 90 * mm - 30 * mm, 90 * mm],
    )
    header_table.setStyle(
        TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (2, 0), (2, 0), "RIGHT")])
    )
    elements.append(header_table)
    elements.append(Spacer(1, 4 * mm))

    period_label = f"{data['date_from']} to {data['date_to']}" if data["date_from"] != data["date_to"] else data["date_from"]
    elements.append(Paragraph("FINANCE &amp; FACTORY STATEMENT", section_style))
    elements.append(Paragraph(f"Statement period: {period_label}", sub_style))

    filter_bits = []
    if data["txn_type"]:
        filter_bits.append(f"Type: {data['txn_type']}")
    if data["payment_status"]:
        filter_bits.append(f"Status: {data['payment_status']}")
    if data["search"]:
        filter_bits.append(f"Search: \"{data['search']}\"")
    if filter_bits:
        elements.append(Paragraph(" &middot; ".join(filter_bits), sub_style))
    elements.append(Spacer(1, 6 * mm))

    table_rows = [["Date", "Ref No.", "Type", "Description", "Debit", "Credit", "Balance", "Method", "User"]]
    table_rows.append(["", "", "", "Opening Balance", "", "", data["opening_balance"], "", ""])
    for t in data["rows"]:
        table_rows.append(
            [
                t["date"], t["reference_no"], t["type"], t["description"],
                t["debit"] or "", t["credit"] or "", t["balance"],
                t["payment_method"] or "—", t["user"] or "—",
            ]
        )
    table_rows.append(["", "", "", "Totals", data["total_debit"], data["total_credit"], "", "", ""])
    table_rows.append(["", "", "", "Closing Balance", "", "", data["closing_balance"], "", ""])

    available_width = page_width - 30 * mm
    col_widths = [w * available_width for w in (0.09, 0.11, 0.08, 0.30, 0.09, 0.09, 0.10, 0.08, 0.06)]

    ledger_table = Table(table_rows, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), green),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.4, border),
        ("ALIGN", (4, 0), (6, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("FONTNAME", (0, -2), (-1, -2), "Helvetica-Bold"),
        ("LINEABOVE", (0, -2), (-1, -2), 0.75, dark_green),
    ]
    ledger_table.setStyle(TableStyle(style_cmds))
    elements.append(ledger_table)
    elements.append(Spacer(1, 14 * mm))

    sig_table = Table(
        [
            ["_________________________", "", "_________________________"],
            ["Prepared by", "", "Authorized by"],
            ["Name / Date", "", "Name / Date"],
        ],
        colWidths=[available_width * 0.4, available_width * 0.2, available_width * 0.4],
    )
    sig_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 1), (-1, -1), muted),
                ("TOPPADDING", (0, 1), (-1, -1), 2),
            ]
        )
    )
    elements.append(sig_table)

    doc.build(elements, canvasmaker=NumberedCanvas)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"statement_{data['date_from']}_to_{data['date_to']}.pdf",
    )


# ---------- Inventory & Stock Management ----------


def _inventory_balance(conn, item_id):
    row = conn.execute(
        """SELECT
               COALESCE(SUM(CASE WHEN transaction_type = 'In' THEN quantity ELSE 0 END), 0) AS stock_in,
               COALESCE(SUM(CASE WHEN transaction_type = 'Out' THEN quantity ELSE 0 END), 0) AS stock_out
           FROM inventory_transactions WHERE item_id = ?""",
        (item_id,),
    ).fetchone()
    return round(row["stock_in"] - row["stock_out"], 2)


@app.route("/inventory")
def inventory_list():
    conn = get_connection()
    category_filter = request.args.get("category", "").strip()

    query = "SELECT * FROM inventory_items"
    params = []
    if category_filter:
        query += " WHERE category = ?"
        params.append(category_filter)
    query += " ORDER BY name"
    items = [dict(i) for i in conn.execute(query, params).fetchall()]

    total_valuation = 0
    low_stock_count = 0
    for item in items:
        item["balance"] = _inventory_balance(conn, item["id"])
        item["valuation"] = round(item["balance"] * item["unit_cost"], 2)
        item["low_stock"] = item["balance"] < item["minimum_stock_level"]
        total_valuation += item["valuation"]
        if item["low_stock"]:
            low_stock_count += 1

    return render_template(
        "inventory.html",
        items=items,
        category_filter=category_filter,
        total_valuation=round(total_valuation, 2),
        low_stock_count=low_stock_count,
    )


@app.route("/inventory/items/new", methods=["GET", "POST"])
def inventory_item_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        unit = request.form.get("unit", "").strip()
        min_level_value = request.form.get("minimum_stock_level", "").strip()
        unit_cost_value = request.form.get("unit_cost", "").strip()

        if not name or category not in INVENTORY_CATEGORIES or not unit:
            flash("Name, a valid category, and a unit are required.", "error")
            return render_template("inventory_item_form.html", item=request.form, mode="new")
        try:
            minimum_stock_level = float(min_level_value) if min_level_value else 0
            unit_cost = float(unit_cost_value) if unit_cost_value else 0
        except ValueError:
            flash("Minimum stock level and unit cost must be numbers.", "error")
            return render_template("inventory_item_form.html", item=request.form, mode="new")

        conn = get_connection()
        conn.execute(
            """INSERT INTO inventory_items (name, category, unit, minimum_stock_level, unit_cost, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, category, unit, minimum_stock_level, unit_cost, session.get("user_id")),
        )
        conn.commit()
        flash(f"{name} added to inventory.", "success")
        return redirect(url_for("inventory_list"))

    return render_template("inventory_item_form.html", item={}, mode="new")


@app.route("/inventory/items/<int:item_id>/edit", methods=["GET", "POST"])
def inventory_item_edit(item_id):
    conn = get_connection()
    item = conn.execute("SELECT * FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        flash("Inventory item not found.", "error")
        return redirect(url_for("inventory_list"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        unit = request.form.get("unit", "").strip()
        min_level_value = request.form.get("minimum_stock_level", "").strip()
        unit_cost_value = request.form.get("unit_cost", "").strip()

        if not name or category not in INVENTORY_CATEGORIES or not unit:
            flash("Name, a valid category, and a unit are required.", "error")
            merged = dict(item)
            merged.update(request.form)
            return render_template("inventory_item_form.html", item=merged, mode="edit", item_id=item_id)
        try:
            minimum_stock_level = float(min_level_value) if min_level_value else 0
            unit_cost = float(unit_cost_value) if unit_cost_value else 0
        except ValueError:
            flash("Minimum stock level and unit cost must be numbers.", "error")
            merged = dict(item)
            merged.update(request.form)
            return render_template("inventory_item_form.html", item=merged, mode="edit", item_id=item_id)

        conn.execute(
            """UPDATE inventory_items SET name=?, category=?, unit=?, minimum_stock_level=?, unit_cost=?, updated_at=?
               WHERE id=?""",
            (name, category, unit, minimum_stock_level, unit_cost, _now_text(), item_id),
        )
        conn.commit()
        flash("Inventory item updated.", "success")
        return redirect(url_for("inventory_list"))

    return render_template("inventory_item_form.html", item=dict(item), mode="edit", item_id=item_id)


@app.route("/inventory/items/<int:item_id>")
def inventory_item_detail(item_id):
    conn = get_connection()
    item = conn.execute("SELECT * FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        flash("Inventory item not found.", "error")
        return redirect(url_for("inventory_list"))

    transactions = conn.execute(
        "SELECT * FROM inventory_transactions WHERE item_id = ? ORDER BY transaction_date DESC, id DESC", (item_id,)
    ).fetchall()
    balance = _inventory_balance(conn, item_id)
    valuation = round(balance * item["unit_cost"], 2)

    return render_template(
        "inventory_item_detail.html", item=item, transactions=transactions, balance=balance, valuation=valuation
    )


@app.route("/inventory/items/<int:item_id>/transaction", methods=["POST"])
def inventory_transaction_new(item_id):
    conn = get_connection()
    item = conn.execute("SELECT * FROM inventory_items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        flash("Inventory item not found.", "error")
        return redirect(url_for("inventory_list"))

    transaction_type = request.form.get("transaction_type", "").strip()
    quantity_value = request.form.get("quantity", "").strip()
    transaction_date = request.form.get("transaction_date", "").strip()
    unit_cost_value = request.form.get("unit_cost", "").strip()
    supplier = request.form.get("supplier", "").strip() or None
    batch_number = request.form.get("batch_number", "").strip() or None
    expiry_date = request.form.get("expiry_date", "").strip() or None
    note = request.form.get("note", "").strip() or None

    if transaction_type not in INVENTORY_TRANSACTION_TYPES or not transaction_date:
        flash("A valid transaction type and date are required.", "error")
        return redirect(url_for("inventory_item_detail", item_id=item_id))

    try:
        quantity = float(quantity_value)
        unit_cost = float(unit_cost_value) if unit_cost_value else None
    except ValueError:
        flash("Quantity and unit cost must be numbers.", "error")
        return redirect(url_for("inventory_item_detail", item_id=item_id))

    if quantity <= 0:
        flash("Quantity must be greater than zero.", "error")
        return redirect(url_for("inventory_item_detail", item_id=item_id))

    if transaction_type == "Out":
        current_balance = _inventory_balance(conn, item_id)
        if quantity > current_balance:
            flash(f"Cannot remove {quantity} {item['unit']} — only {current_balance} in stock.", "error")
            return redirect(url_for("inventory_item_detail", item_id=item_id))

    conn.execute(
        """INSERT INTO inventory_transactions
           (item_id, transaction_type, quantity, unit_cost, supplier, batch_number, expiry_date,
            transaction_date, note, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item_id, transaction_type, quantity, unit_cost, supplier, batch_number, expiry_date,
            transaction_date, note, session.get("user_id"),
        ),
    )
    if transaction_type == "In" and unit_cost is not None:
        conn.execute(
            "UPDATE inventory_items SET unit_cost = ?, updated_at = ? WHERE id = ?",
            (unit_cost, _now_text(), item_id),
        )
    conn.commit()
    flash(f"Stock {transaction_type.lower()} of {quantity} {item['unit']} recorded.", "success")
    return redirect(url_for("inventory_item_detail", item_id=item_id))


@app.route("/inventory/items/<int:item_id>/delete", methods=["POST"])
def inventory_item_delete(item_id):
    conn = get_connection()
    conn.execute("DELETE FROM inventory_items WHERE id = ?", (item_id,))
    conn.commit()
    flash("Inventory item removed.", "success")
    return redirect(url_for("inventory_list"))


@app.route("/inventory/transactions")
def inventory_transaction_list():
    conn = get_connection()
    rows = conn.execute(
        """SELECT t.*, i.name AS item_name, i.unit AS item_unit FROM inventory_transactions t
           JOIN inventory_items i ON i.id = t.item_id
           ORDER BY t.transaction_date DESC, t.id DESC LIMIT 500"""
    ).fetchall()
    return render_template("inventory_transactions.html", transactions=rows)


# ---------- Asset & Equipment Register ----------


def _months_between(start_date, end_date):
    """Whole calendar months elapsed from start_date to end_date (0 if end is before start)."""
    if end_date < start_date:
        return 0
    months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
    if end_date.day < start_date.day:
        months -= 1
    return max(0, months)


def _asset_depreciation(asset, as_of=None):
    """Straight-line depreciation as of a given date (defaults to today). Returns
    None if the asset doesn't have enough information to depreciate (no cost,
    no purchase date, or no depreciation period set)."""
    if not asset["purchase_cost"] or not asset["purchase_date"] or not asset["depreciation_period_months"]:
        return None

    as_of = as_of or _colombo_today()
    purchase_date = date.fromisoformat(asset["purchase_date"])
    depreciable_amount = asset["purchase_cost"] - (asset["salvage_value"] or 0)
    monthly_amount = round(depreciable_amount / asset["depreciation_period_months"], 2)
    months_elapsed = min(_months_between(purchase_date, as_of), asset["depreciation_period_months"])
    accumulated = round(monthly_amount * months_elapsed, 2)
    book_value = round(asset["purchase_cost"] - accumulated, 2)
    fully_depreciated = months_elapsed >= asset["depreciation_period_months"]

    return {
        "monthly_amount": monthly_amount,
        "months_elapsed": months_elapsed,
        "accumulated": accumulated,
        "book_value": book_value,
        "fully_depreciated": fully_depreciated,
    }


def _add_months(d, n):
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _step_months_for_period(period_type):
    return {"Monthly": 1, "Quarterly": 3, "Yearly": 12}.get(period_type, 1)


def _build_depreciation_ledger_entries(conn):
    """Straight-line depreciation for registered assets, plus amortization of
    standalone prepaid expenses (e.g. a bulk fertilizer purchase covering
    several months' worth of use) — one Depreciation debit entry per elapsed
    period, so each period's expense is recognized only once it's actually
    due, e.g. a 3-month fertilizer purchase allocates one third of its cost
    as an expense each month rather than all at once on the purchase date."""
    entries = []
    today = _colombo_today()

    assets = conn.execute(
        """SELECT * FROM assets
           WHERE purchase_cost IS NOT NULL AND purchase_date IS NOT NULL
             AND depreciation_period_months IS NOT NULL AND depreciation_period_months > 0"""
    ).fetchall()
    for a in assets:
        purchase_date = date.fromisoformat(a["purchase_date"])
        depreciable_amount = (a["purchase_cost"] or 0) - (a["salvage_value"] or 0)
        if depreciable_amount <= 0:
            continue
        monthly_amount = round(depreciable_amount / a["depreciation_period_months"], 2)
        months_elapsed = min(_months_between(purchase_date, today), a["depreciation_period_months"])
        for i in range(1, months_elapsed + 1):
            period_date = _add_months(purchase_date, i)
            entries.append(
                {
                    "date": period_date.isoformat(),
                    "type": "Depreciation",
                    "reference_no": f"DEP-AST-{a['id']}-{i}",
                    "description": (
                        f"Depreciation — {a['name']} ({a['asset_code']}), "
                        f"month {i} of {a['depreciation_period_months']}"
                    ),
                    "debit": monthly_amount,
                    "credit": 0,
                    "payment_method": None,
                    "user": None,
                    "factory": None,
                    "factory_id": None,
                    "employee": None,
                    "employee_id": None,
                    "status": None,
                }
            )

    prepaid_rows = conn.execute("SELECT * FROM prepaid_expenses").fetchall()
    for p in prepaid_rows:
        if p["period_count"] <= 0 or p["total_cost"] <= 0:
            continue
        start_date = date.fromisoformat(p["start_date"])
        step = _step_months_for_period(p["period_type"])
        per_period = round(p["total_cost"] / p["period_count"], 2)
        periods_elapsed = min(_months_between(start_date, today) // step, p["period_count"])
        for i in range(1, periods_elapsed + 1):
            period_date = _add_months(start_date, i * step)
            entries.append(
                {
                    "date": period_date.isoformat(),
                    "type": "Depreciation",
                    "reference_no": f"DEP-PPD-{p['id']}-{i}",
                    "description": f"Amortization — {p['description']}, period {i} of {p['period_count']}",
                    "debit": per_period,
                    "credit": 0,
                    "payment_method": None,
                    "user": None,
                    "factory": None,
                    "factory_id": None,
                    "employee": None,
                    "employee_id": None,
                    "status": None,
                }
            )
    return entries


@app.route("/assets")
def asset_list():
    conn = get_connection()
    status_filter = request.args.get("status", "").strip()

    query = """SELECT a.*, e.full_name AS assigned_employee_name FROM assets a
               LEFT JOIN employees e ON e.id = a.assigned_employee_id"""
    params = []
    if status_filter in ASSET_STATUSES:
        query += " WHERE a.status = ?"
        params.append(status_filter)
    query += " ORDER BY a.name"
    assets = [dict(a) for a in conn.execute(query, params).fetchall()]

    total_book_value = 0
    for asset in assets:
        dep = _asset_depreciation(asset)
        asset["book_value"] = dep["book_value"] if dep else asset["purchase_cost"]
        total_book_value += asset["book_value"] or 0

    return render_template(
        "assets.html", assets=assets, status_filter=status_filter, total_book_value=round(total_book_value, 2)
    )


@app.route("/assets/new", methods=["GET", "POST"])
def asset_new():
    conn = get_connection()
    employees = conn.execute("SELECT id, employee_number, full_name FROM employees ORDER BY full_name").fetchall()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Asset name is required.", "error")
            return render_template("asset_form.html", asset=request.form, mode="new", employees=employees)

        data = {
            "category": request.form.get("category", "").strip() or None,
            "purchase_date": request.form.get("purchase_date", "").strip() or None,
            "supplier": request.form.get("supplier", "").strip() or None,
            "serial_number": request.form.get("serial_number", "").strip() or None,
            "assigned_location": request.form.get("assigned_location", "").strip() or None,
            "assigned_employee_id": request.form.get("assigned_employee_id", "").strip() or None,
            "warranty_expiry": request.form.get("warranty_expiry", "").strip() or None,
            "service_schedule": request.form.get("service_schedule", "").strip() or None,
            "next_service_date": request.form.get("next_service_date", "").strip() or None,
            "status": request.form.get("status", "").strip() or "Active",
        }
        try:
            purchase_cost_value = request.form.get("purchase_cost", "").strip()
            salvage_value_value = request.form.get("salvage_value", "").strip()
            period_value = request.form.get("depreciation_period_months", "").strip()
            data["purchase_cost"] = float(purchase_cost_value) if purchase_cost_value else None
            data["salvage_value"] = float(salvage_value_value) if salvage_value_value else 0
            data["depreciation_period_months"] = int(period_value) if period_value else None
        except ValueError:
            flash("Purchase cost, salvage value, and depreciation period must be numbers.", "error")
            return render_template("asset_form.html", asset=request.form, mode="new", employees=employees)

        cursor = conn.execute(
            """INSERT INTO assets
               (asset_code, name, category, purchase_date, purchase_cost, salvage_value, supplier, serial_number,
                assigned_location, assigned_employee_id, warranty_expiry, service_schedule, next_service_date,
                status, depreciation_period_months, created_by)
               VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name, data["category"], data["purchase_date"], data["purchase_cost"], data["salvage_value"],
                data["supplier"], data["serial_number"], data["assigned_location"], data["assigned_employee_id"],
                data["warranty_expiry"], data["service_schedule"], data["next_service_date"], data["status"],
                data["depreciation_period_months"], session.get("user_id"),
            ),
        )
        asset_code = f"AST-{cursor.lastrowid:04d}"
        conn.execute("UPDATE assets SET asset_code = ? WHERE id = ?", (asset_code, cursor.lastrowid))
        conn.commit()
        flash(f"Asset {asset_code} registered.", "success")
        return redirect(url_for("asset_list"))

    return render_template("asset_form.html", asset={}, mode="new", employees=employees)


@app.route("/assets/<int:asset_id>/edit", methods=["GET", "POST"])
def asset_edit(asset_id):
    conn = get_connection()
    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not asset:
        flash("Asset not found.", "error")
        return redirect(url_for("asset_list"))
    employees = conn.execute("SELECT id, employee_number, full_name FROM employees ORDER BY full_name").fetchall()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Asset name is required.", "error")
            merged = dict(asset)
            merged.update(request.form)
            return render_template("asset_form.html", asset=merged, mode="edit", asset_id=asset_id, employees=employees)

        data = {
            "category": request.form.get("category", "").strip() or None,
            "purchase_date": request.form.get("purchase_date", "").strip() or None,
            "supplier": request.form.get("supplier", "").strip() or None,
            "serial_number": request.form.get("serial_number", "").strip() or None,
            "assigned_location": request.form.get("assigned_location", "").strip() or None,
            "assigned_employee_id": request.form.get("assigned_employee_id", "").strip() or None,
            "warranty_expiry": request.form.get("warranty_expiry", "").strip() or None,
            "service_schedule": request.form.get("service_schedule", "").strip() or None,
            "next_service_date": request.form.get("next_service_date", "").strip() or None,
            "status": request.form.get("status", "").strip() or "Active",
        }
        try:
            purchase_cost_value = request.form.get("purchase_cost", "").strip()
            salvage_value_value = request.form.get("salvage_value", "").strip()
            period_value = request.form.get("depreciation_period_months", "").strip()
            data["purchase_cost"] = float(purchase_cost_value) if purchase_cost_value else None
            data["salvage_value"] = float(salvage_value_value) if salvage_value_value else 0
            data["depreciation_period_months"] = int(period_value) if period_value else None
        except ValueError:
            flash("Purchase cost, salvage value, and depreciation period must be numbers.", "error")
            merged = dict(asset)
            merged.update(request.form)
            return render_template("asset_form.html", asset=merged, mode="edit", asset_id=asset_id, employees=employees)

        conn.execute(
            """UPDATE assets SET name=?, category=?, purchase_date=?, purchase_cost=?, salvage_value=?, supplier=?,
                   serial_number=?, assigned_location=?, assigned_employee_id=?, warranty_expiry=?,
                   service_schedule=?, next_service_date=?, status=?, depreciation_period_months=?, updated_at=?
               WHERE id=?""",
            (
                name, data["category"], data["purchase_date"], data["purchase_cost"], data["salvage_value"],
                data["supplier"], data["serial_number"], data["assigned_location"], data["assigned_employee_id"],
                data["warranty_expiry"], data["service_schedule"], data["next_service_date"], data["status"],
                data["depreciation_period_months"], _now_text(), asset_id,
            ),
        )
        conn.commit()
        flash("Asset updated.", "success")
        return redirect(url_for("asset_list"))

    return render_template("asset_form.html", asset=dict(asset), mode="edit", asset_id=asset_id, employees=employees)


@app.route("/assets/<int:asset_id>")
def asset_detail(asset_id):
    conn = get_connection()
    asset = conn.execute(
        """SELECT a.*, e.full_name AS assigned_employee_name FROM assets a
           LEFT JOIN employees e ON e.id = a.assigned_employee_id WHERE a.id = ?""",
        (asset_id,),
    ).fetchone()
    if not asset:
        flash("Asset not found.", "error")
        return redirect(url_for("asset_list"))

    maintenance_log = conn.execute(
        "SELECT * FROM asset_maintenance_log WHERE asset_id = ? ORDER BY maintenance_date DESC, id DESC", (asset_id,)
    ).fetchall()
    depreciation = _asset_depreciation(asset)

    return render_template(
        "asset_detail.html", asset=asset, maintenance_log=maintenance_log, depreciation=depreciation
    )


@app.route("/assets/<int:asset_id>/maintenance", methods=["POST"])
def asset_maintenance_new(asset_id):
    conn = get_connection()
    asset = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not asset:
        flash("Asset not found.", "error")
        return redirect(url_for("asset_list"))

    maintenance_date = request.form.get("maintenance_date", "").strip()
    description = request.form.get("description", "").strip()
    cost_value = request.form.get("cost", "").strip()
    performed_by = request.form.get("performed_by", "").strip() or None
    next_service_date = request.form.get("next_service_date", "").strip() or None

    if not maintenance_date or not description:
        flash("Maintenance date and description are required.", "error")
        return redirect(url_for("asset_detail", asset_id=asset_id))
    try:
        cost = float(cost_value) if cost_value else None
    except ValueError:
        flash("Cost must be a number.", "error")
        return redirect(url_for("asset_detail", asset_id=asset_id))

    conn.execute(
        """INSERT INTO asset_maintenance_log (asset_id, maintenance_date, description, cost, performed_by,
               next_service_date, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (asset_id, maintenance_date, description, cost, performed_by, next_service_date, session.get("user_id")),
    )
    if next_service_date:
        conn.execute("UPDATE assets SET next_service_date = ? WHERE id = ?", (next_service_date, asset_id))
    conn.commit()
    flash("Maintenance record added.", "success")
    return redirect(url_for("asset_detail", asset_id=asset_id))


@app.route("/assets/<int:asset_id>/delete", methods=["POST"])
def asset_delete(asset_id):
    conn = get_connection()
    conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    conn.commit()
    flash("Asset removed.", "success")
    return redirect(url_for("asset_list"))


# ---------- Depreciation & Amortization ----------


@app.route("/finance/depreciation")
def depreciation_list():
    conn = get_connection()
    today = _colombo_today()

    assets = conn.execute(
        """SELECT * FROM assets
           WHERE purchase_cost IS NOT NULL AND purchase_date IS NOT NULL AND depreciation_period_months IS NOT NULL
           ORDER BY name"""
    ).fetchall()
    asset_rows = []
    total_monthly = 0
    for a in assets:
        dep = _asset_depreciation(a, today)
        if not dep:
            continue
        asset_rows.append({"asset": a, "dep": dep})
        if not dep["fully_depreciated"]:
            total_monthly += dep["monthly_amount"]

    prepaid_rows = []
    for p in conn.execute("SELECT * FROM prepaid_expenses ORDER BY start_date DESC").fetchall():
        start_date = date.fromisoformat(p["start_date"])
        step = _step_months_for_period(p["period_type"])
        per_period = round(p["total_cost"] / p["period_count"], 2) if p["period_count"] else 0
        periods_elapsed = min(_months_between(start_date, today) // step, p["period_count"])
        accumulated = round(per_period * periods_elapsed, 2)
        remaining = round(p["total_cost"] - accumulated, 2)
        prepaid_rows.append(
            {
                "prepaid": p,
                "per_period": per_period,
                "periods_elapsed": periods_elapsed,
                "accumulated": accumulated,
                "remaining": remaining,
                "fully_amortized": periods_elapsed >= p["period_count"],
            }
        )

    return render_template(
        "depreciation.html",
        asset_rows=asset_rows,
        prepaid_rows=prepaid_rows,
        total_monthly=round(total_monthly, 2),
    )


@app.route("/finance/depreciation/new", methods=["POST"])
def prepaid_expense_new():
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "").strip() or None
    total_cost_value = request.form.get("total_cost", "").strip()
    start_date_value = request.form.get("start_date", "").strip()
    period_type = request.form.get("period_type", "").strip()
    period_count_value = request.form.get("period_count", "").strip()

    if not description or not start_date_value or period_type not in DEPRECIATION_PERIOD_TYPES:
        flash("Description, start date, and a valid period type are required.", "error")
        return redirect(url_for("depreciation_list"))
    try:
        total_cost = float(total_cost_value)
        period_count = int(period_count_value)
    except ValueError:
        flash("Total cost and number of periods must be numbers.", "error")
        return redirect(url_for("depreciation_list"))
    if total_cost <= 0 or period_count <= 0:
        flash("Total cost and number of periods must be greater than zero.", "error")
        return redirect(url_for("depreciation_list"))

    conn = get_connection()
    conn.execute(
        """INSERT INTO prepaid_expenses (description, category, total_cost, start_date, period_type, period_count, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (description, category, total_cost, start_date_value, period_type, period_count, session.get("user_id")),
    )
    conn.commit()
    flash(f"Prepaid expense '{description}' will amortize over {period_count} {period_type.lower()} period(s).", "success")
    return redirect(url_for("depreciation_list"))


@app.route("/finance/depreciation/<int:prepaid_id>/delete", methods=["POST"])
def prepaid_expense_delete(prepaid_id):
    conn = get_connection()
    conn.execute("DELETE FROM prepaid_expenses WHERE id = ?", (prepaid_id,))
    conn.commit()
    flash("Prepaid expense schedule removed.", "success")
    return redirect(url_for("depreciation_list"))


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
