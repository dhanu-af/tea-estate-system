import io
import sqlite3
from datetime import datetime

import qrcode
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
from werkzeug.security import generate_password_hash, check_password_hash

from database import (
    get_connection,
    close_connection,
    init_db,
    JOB_POSITIONS,
    EMPLOYMENT_TYPES,
    PAY_CYCLES,
    ATTENDANCE_STATUSES,
    VERIFICATION_METHODS,
    TASK_TYPES,
    WORK_STATUSES,
    EXPENSE_CATEGORIES,
)
from utils import (
    compute_attendance_hours,
    compute_productivity,
    next_work_status,
    compute_harvest_pay,
    compute_epf_etf,
)

app = Flask(__name__)
app.secret_key = "tea-estate-dev-secret"
app.teardown_appcontext(close_connection)

PUBLIC_ENDPOINTS = {"checkin", "employee_badge", "login", "setup", "static"}


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
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, generate_password_hash(password)),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            flash("That username is already taken.", "error")
            return render_template("setup.html")

        flash("Admin account created. Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("setup.html")


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
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            flash(f"Welcome, {user['username']}.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "error")
        return render_template("login.html")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Logged out.", "success")
    return redirect(url_for("login"))


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
    return render_template("employees.html", employees=rows, q=q)


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
        if not data["full_name"]:
            flash("Full name is required.", "error")
            merged = dict(employee)
            merged.update(data)
            return render_template("employee_form.html", employee=merged, mode="edit", employee_id=employee_id)

        set_clause = ", ".join(f"{f} = ?" for f in EMPLOYEE_FIELDS)
        conn.execute(
            f"UPDATE employees SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            [data[f] for f in EMPLOYEE_FIELDS] + [employee_id],
        )
        conn.commit()
        flash("Employee updated.", "success")
        return redirect(url_for("employee_list"))

    recent_work = conn.execute(
        """SELECT * FROM work_assignments WHERE employee_id = ?
           ORDER BY date DESC, id DESC LIMIT 5""",
        (employee_id,),
    ).fetchall()
    return render_template(
        "employee_form.html", employee=dict(employee), mode="edit", employee_id=employee_id, recent_work=recent_work
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
            "UPDATE attendance SET check_out = ?, total_break_hours = ?, total_work_hours = ?, updated_at = datetime('now') WHERE id = ?",
            (now_time, total_break, net_work, record["id"]),
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
    return render_template("attendance.html", records=rows, date_filter=date_filter)


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
               status=?, verification_method=?, updated_at=datetime('now') WHERE id=?""",
            (employee_id, date, check_in, check_out, break_start, break_end,
             total_break, net_work, status, verification_method, record_id),
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


def _payroll_date_range():
    from datetime import date

    today = date.today()
    date_from = request.args.get("from", "").strip() or today.replace(day=1).isoformat()
    date_to = request.args.get("to", "").strip() or today.isoformat()
    return date_from, date_to


def _compute_payroll_rows(date_from, date_to, include_advances=False):
    """Harvest pay is computed per work assignment (each has its own daily target),
    not from an aggregated total, since kg beyond a given day's target earns a
    commission bonus that a single SUM-based calculation couldn't apply correctly."""
    conn = get_connection()
    employees = conn.execute(
        """SELECT e.id, e.employee_number, e.full_name, e.rate_per_kg, e.hourly_rate,
                  e.over_target_commission_percent, e.epf_etf_applicable,
                  (SELECT COUNT(*) FROM attendance a
                     WHERE a.employee_id = e.id AND a.date BETWEEN ? AND ? AND a.status = 'Present') AS present_days,
                  (SELECT COALESCE(SUM(a.total_work_hours), 0) FROM attendance a
                     WHERE a.employee_id = e.id AND a.date BETWEEN ? AND ?) AS total_hours
           FROM employees e
           ORDER BY e.full_name""",
        (date_from, date_to, date_from, date_to),
    ).fetchall()

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
        parts = [p for p in (row["harvest_payment"], row["hourly_pay"]) if p is not None]
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
    date_from, date_to = _payroll_date_range()
    payroll_rows = _compute_payroll_rows(date_from, date_to, include_advances=True)
    total_payment = sum(r["total_pay"] for r in payroll_rows if r["total_pay"])
    total_advances = sum(r["advance_total"] for r in payroll_rows if r["advance_total"])
    total_employee_epf = sum(r["employee_epf"] for r in payroll_rows if r["employee_epf"])
    total_employer_epf_etf = sum(
        (r["employer_epf"] or 0) + (r["employer_etf"] or 0) for r in payroll_rows
    )
    total_net_pay = sum(r["net_pay"] for r in payroll_rows if r["net_pay"])

    conn = get_connection()
    employees = conn.execute("SELECT id, employee_number, full_name FROM employees ORDER BY full_name").fetchall()
    advances = conn.execute(
        """SELECT sa.*, e.full_name, e.employee_number FROM salary_advances sa
           JOIN employees e ON e.id = sa.employee_id
           WHERE sa.date BETWEEN ? AND ?
           ORDER BY sa.date DESC, sa.id DESC""",
        (date_from, date_to),
    ).fetchall()

    return render_template(
        "payroll.html",
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


@app.route("/payroll/advance", methods=["POST"])
def payroll_add_advance():
    employee_id = request.form.get("employee_id", "").strip()
    date_value = request.form.get("date", "").strip()
    amount_value = request.form.get("amount", "").strip()
    note = request.form.get("note", "").strip() or None

    if not employee_id or not date_value:
        flash("Employee and date are required.", "error")
        return redirect(url_for("payroll", **{"from": date_value, "to": date_value}))

    try:
        amount = float(amount_value)
    except ValueError:
        flash("Advance amount must be a number.", "error")
        return redirect(url_for("payroll"))

    conn = get_connection()
    conn.execute(
        "INSERT INTO salary_advances (employee_id, date, amount, note) VALUES (?, ?, ?, ?)",
        (employee_id, date_value, amount, note),
    )
    conn.commit()
    flash(f"Salary advance of {amount} recorded.", "success")
    return redirect(url_for("payroll", **{"from": date_value, "to": date_value}))


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
    return redirect(url_for("payroll", **{"from": advance["date"], "to": advance["date"]}))


@app.route("/payroll/export.csv")
def payroll_export():
    import csv

    date_from, date_to = _payroll_date_range()
    payroll_rows = _compute_payroll_rows(date_from, date_to, include_advances=True)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Employee Number", "Full Name", "Present Days", "Hours Worked", "Hourly Rate",
            "Time-based Pay", "Harvest (kg)", "Rate/kg", "Bonus (kg)", "Harvest Pay", "Total Pay",
            "Salary Advance", "Employee EPF (8%)", "Net Pay", "Employer EPF (12%)", "Employer ETF (3%)",
        ]
    )
    for r in payroll_rows:
        writer.writerow(
            [
                r["employee_number"], r["full_name"], r["present_days"], r["total_hours"], r["hourly_rate"],
                r["hourly_pay"], r["total_output"], r["rate_per_kg"], r["bonus_kg"], r["harvest_payment"], r["total_pay"],
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


@app.route("/payroll/payslip/<int:employee_id>")
def payroll_payslip(employee_id):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    date_from, date_to = _payroll_date_range()

    conn = get_connection()
    employee = conn.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
    if not employee:
        flash("Employee not found.", "error")
        return redirect(url_for("payroll"))

    payroll_rows = _compute_payroll_rows(date_from, date_to, include_advances=True)
    row = next((r for r in payroll_rows if r["id"] == employee_id), None)
    if row is None:
        flash("No payroll data for this employee in the selected period.", "error")
        return redirect(url_for("payroll", **{"from": date_from, "to": date_to}))

    advances = conn.execute(
        "SELECT * FROM salary_advances WHERE employee_id = ? AND date BETWEEN ? AND ? ORDER BY date",
        (employee_id, date_from, date_to),
    ).fetchall()

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm, leftMargin=20 * mm, rightMargin=20 * mm
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("PayslipTitle", parent=styles["Title"], fontSize=18, spaceAfter=2)
    sub_style = ParagraphStyle("PayslipSub", parent=styles["Normal"], textColor=colors.HexColor("#6b7a67"))
    green = colors.HexColor("#2f5d3a")
    red = colors.HexColor("#b3413a")
    border = colors.HexColor("#dbe5d9")

    elements = [
        Paragraph("DKNS Tea Lands", title_style),
        Paragraph("Payslip", sub_style),
        Spacer(1, 10 * mm),
    ]

    info_table = Table(
        [
            ["Employee", row["full_name"]],
            ["Employee ID", row["employee_number"]],
            ["Position", employee["job_position"] or "—"],
            ["Division", employee["estate_division"] or "—"],
            ["Pay period", f"{date_from} to {date_to}"],
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
            deduction_rows.append(["Salary advance", a["date"], a["amount"]])
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

    doc.build(elements)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"payslip_{row['employee_number']}_{date_from}_to_{date_to}.pdf",
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
        "INSERT INTO expenses (date, category, amount, note) VALUES (?, ?, ?, ?)",
        (date_value, category, amount, note),
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
        """INSERT INTO daily_prices (date, price_per_kg) VALUES (?, ?)
           ON CONFLICT(date) DO UPDATE SET price_per_kg = excluded.price_per_kg, updated_at = datetime('now')""",
        (date_value, price),
    )
    conn.commit()
    flash(f"Tea price for {date_value} set to {price}.", "success")
    return redirect(url_for("income", date=date_value))


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(debug=True, host="0.0.0.0", port=5000)
