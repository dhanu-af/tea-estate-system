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
