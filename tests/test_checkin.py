def test_first_scan_checks_in(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    resp = auth_client.get("/checkin/EMP-0001")
    assert b"Checked in" in resp.data

    attendance = auth_client.get("/attendance")
    assert b"QR code" in attendance.data


def test_second_scan_checks_out(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.get("/checkin/EMP-0001")
    resp = auth_client.get("/checkin/EMP-0001")
    assert b"Checked out" in resp.data


def test_third_scan_reports_already_done(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.get("/checkin/EMP-0001")
    auth_client.get("/checkin/EMP-0001")
    resp = auth_client.get("/checkin/EMP-0001")
    assert b"Already checked out today" in resp.data


def test_unknown_employee_number_returns_404(auth_client):
    resp = auth_client.get("/checkin/EMP-9999")
    assert resp.status_code == 404
    assert b"Employee not found" in resp.data


def test_checkin_shows_todays_task(auth_client):
    import app as app_module

    today = app_module._colombo_today().isoformat()
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": today, "task_type": "Plucking", "harvest_target": "20"},
    )
    resp = auth_client.get("/checkin/EMP-0001")
    assert b"Plucking" in resp.data


def test_checkin_shows_daily_sinhala_instructions_only_on_check_in(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Shayamali Kanthi"})

    resp = auth_client.get("/checkin/EMP-0001")
    text = resp.get_data(as_text=True)
    assert "දෛනික කාර්යයන්" in text

    resp = auth_client.get("/checkin/EMP-0001")
    text = resp.get_data(as_text=True)
    assert "දෛනික කාර්යයන්" not in text  # not shown again on check-out

    resp = auth_client.get("/checkin/EMP-0001")
    text = resp.get_data(as_text=True)
    assert "දෛනික කාර්යයන්" not in text  # not shown on "already checked out"
