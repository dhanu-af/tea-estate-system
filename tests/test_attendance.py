import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def _cycle_bounds():
    today = datetime.now(ZoneInfo("Asia/Colombo")).date()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def _stat_for_label(html, label):
    """Extracts the number shown in a `.card` for a given `.label` text, e.g.
    '<div class="stat">3</div>\\n<div class="label">Days Absent</div>' -> '3'."""
    match = re.search(r'<div class="stat"[^>]*>\s*([^<]*?)\s*</div>\s*<div class="label">' + re.escape(label), html)
    return match.group(1).strip() if match else None


def test_attendance_computes_net_hours(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})

    resp = auth_client.post(
        "/attendance/new",
        data={
            "employee_id": "1",
            "date": "2026-07-10",
            "status": "Present",
            "check_in": "07:30",
            "check_out": "16:30",
            "break_start": "12:00",
            "break_end": "13:00",
            "verification_method": "Manual entry",
        },
        follow_redirects=True,
    )
    assert b"Attendance recorded" in resp.data

    list_resp = auth_client.get("/attendance")
    assert b"8.0" in list_resp.data  # 9h total - 1h break = 8h net


def test_attendance_requires_employee_date_status(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    resp = auth_client.post("/attendance/new", data={"employee_id": "1"})
    assert b"required" in resp.data


def test_stale_attendance_edit_is_rejected(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/attendance/new",
        data={"employee_id": "1", "date": "2026-07-10", "status": "Present"},
    )
    resp = auth_client.post(
        "/attendance/1/edit",
        data={
            "employee_id": "1",
            "date": "2026-07-10",
            "status": "Absent",
            "expected_updated_at": "2000-01-01 00:00:00",
        },
        follow_redirects=True,
    )
    assert b"Someone else updated this attendance record" in resp.data


# ---------- Attendance Summary: days worked/absent, No-Pay (LOP), paid leave, leave balance ----------


def test_attendance_summary_counts_worked_absent_and_leave(auth_client):
    cycle_start, cycle_end = _cycle_bounds()
    day1 = cycle_start
    day2 = (date.fromisoformat(cycle_start) + timedelta(days=1)).isoformat()
    day3 = (date.fromisoformat(cycle_start) + timedelta(days=2)).isoformat()

    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post("/attendance/new", data={"employee_id": "1", "date": day1, "status": "Present"})
    auth_client.post("/attendance/new", data={"employee_id": "1", "date": day2, "status": "Absent"})
    auth_client.post("/attendance/new", data={"employee_id": "1", "date": day3, "status": "Sick Leave"})

    resp = auth_client.get("/attendance")
    body = resp.data.decode()
    # 1 worked, 1 absent, 1 paid (sick leave always paid), 1 no-pay (the absence)
    assert _stat_for_label(body, "Total Days Worked") == "1"
    assert _stat_for_label(body, "Days Absent") == "1"
    assert _stat_for_label(body, "Paid Leave Days") == "1"
    assert _stat_for_label(body, "No Pay (LOP) Days") == "1"


def test_leave_balance_and_no_pay_once_entitlement_exhausted(auth_client):
    cycle_start, cycle_end = _cycle_bounds()
    day1 = cycle_start
    day2 = (date.fromisoformat(cycle_start) + timedelta(days=1)).isoformat()
    day3 = (date.fromisoformat(cycle_start) + timedelta(days=2)).isoformat()

    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "annual_leave_entitlement": "2", "hourly_rate": "150", "required_daily_hours": "8"},
    )
    auth_client.post("/attendance/new", data={"employee_id": "1", "date": day1, "status": "Leave"})
    auth_client.post("/attendance/new", data={"employee_id": "1", "date": day2, "status": "Leave"})
    auth_client.post("/attendance/new", data={"employee_id": "1", "date": day3, "status": "Leave"})
    # entitlement is 2 days/year; 3rd Leave day this year exceeds it -> unpaid (LOP)

    resp = auth_client.get("/attendance")
    body = resp.data.decode()
    assert "0" in body  # leave balance should now be 0 (2 - 2 taken, capped at 0)

    # payroll: 2 paid leave days compensated at day rate (8h x 150 = 1200/day = 2400 total),
    # the 3rd (no-pay) day contributes nothing extra
    payroll_resp = auth_client.get("/payroll")
    assert b"2400.0" in payroll_resp.data


def test_overtime_hours_calculated_against_required_daily_hours(auth_client):
    cycle_start, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "required_daily_hours": "8"})
    auth_client.post(
        "/attendance/new",
        data={
            "employee_id": "1", "date": cycle_start, "status": "Present",
            "check_in": "07:00", "check_out": "18:00", "break_start": "12:00", "break_end": "13:00",
        },
    )
    # 11h gross - 1h break = 10h worked, 2h over the 8h requirement = overtime

    resp = auth_client.get("/attendance")
    assert b"2.0" in resp.data


def test_incomplete_hours_highlighted(auth_client):
    cycle_start, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "required_daily_hours": "8"})
    auth_client.post(
        "/attendance/new",
        data={
            "employee_id": "1", "date": cycle_start, "status": "Present",
            "check_in": "09:00", "check_out": "13:00",
        },
    )
    # only 4h worked against an 8h requirement -> flagged as under hours

    resp = auth_client.get("/attendance")
    assert b"Under hours" in resp.data


def test_no_incomplete_flag_without_required_hours_configured(auth_client):
    cycle_start, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice"})  # no required_daily_hours set
    auth_client.post(
        "/attendance/new",
        data={"employee_id": "1", "date": cycle_start, "status": "Present", "check_in": "09:00", "check_out": "11:00"},
    )
    resp = auth_client.get("/attendance")
    assert b"Under hours" not in resp.data


def test_payroll_row_shows_no_pay_days_for_absences(auth_client):
    cycle_start, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post("/attendance/new", data={"employee_id": "1", "date": cycle_start, "status": "Absent"})

    resp = auth_client.get("/payroll/payslip/1")
    assert resp.status_code == 200
    import io as _io
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() for page in PdfReader(_io.BytesIO(resp.data)).pages)
    assert "No-Pay (LOP)" in text


def test_overall_attendance_percentage_does_not_double_count_absences(auth_client):
    # Regression test: no_pay_days already includes absences, so the aggregate
    # "total recorded" must not add days_absent a second time on top of it,
    # or the percentage comes out too low.
    cycle_start, _ = _cycle_bounds()
    day2 = (date.fromisoformat(cycle_start) + timedelta(days=1)).isoformat()
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post("/attendance/new", data={"employee_id": "1", "date": cycle_start, "status": "Present"})
    auth_client.post("/attendance/new", data={"employee_id": "1", "date": day2, "status": "Absent"})

    resp = auth_client.get("/attendance")
    body = resp.data.decode()
    assert _stat_for_label(body, "Attendance Percentage") == "50.0%"
