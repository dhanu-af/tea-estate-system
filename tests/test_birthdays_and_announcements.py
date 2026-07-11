import app as app_module


def _register_employee(client, full_name, dob_iso):
    return client.post(
        "/employees/new",
        data={"full_name": full_name, "date_of_birth": dob_iso, "job_position": "Field Worker"},
        follow_redirects=True,
    )


def test_upcoming_birthday_appears_on_employees_page(auth_client):
    from datetime import timedelta

    dob = (app_module._colombo_today() + timedelta(days=5)).replace(year=1990)
    _register_employee(auth_client, "Birthday Soon", dob.isoformat())

    resp = auth_client.get("/employees")
    assert resp.status_code == 200
    assert b"Upcoming Birthdays" in resp.data
    assert b"Birthday Soon" in resp.data


def test_birthday_today_is_highlighted(auth_client):
    dob = app_module._colombo_today().replace(year=1985)
    _register_employee(auth_client, "Today Birthday Person", dob.isoformat())

    resp = auth_client.get("/employees")
    assert b"Today!" in resp.data


def test_far_off_birthday_not_shown_in_widget(auth_client):
    from datetime import timedelta

    dob = (app_module._colombo_today() + timedelta(days=200)).replace(year=1990)
    _register_employee(auth_client, "Far Off Birthday", dob.isoformat())

    resp = auth_client.get("/employees")
    # 200 days out is beyond the 30-day upcoming window, so the widget shouldn't render at all.
    assert b"Upcoming Birthdays" not in resp.data


def test_employee_detail_shows_next_birthday_widget(auth_client):
    from datetime import timedelta

    dob = (app_module._colombo_today() + timedelta(days=10)).replace(year=1992)
    resp = _register_employee(auth_client, "Detail Page Birthday", dob.isoformat())

    resp = auth_client.get("/employees")
    import re
    m = re.search(rb"/employees/(\d+)/edit", resp.data)
    employee_id = m.group(1).decode()

    resp = auth_client.get(f"/employees/{employee_id}/edit")
    assert b"Next birthday" in resp.data


def test_dashboard_shows_auto_updates_section(auth_client):
    resp = auth_client.get("/")
    assert resp.status_code == 200
    assert b"Auto Updates" in resp.data
    assert b"Birthday Reminders" in resp.data
    assert b"Announcements" in resp.data


def test_admin_can_post_and_delete_announcement(auth_client):
    resp = auth_client.post(
        "/announcements/new", data={"message": "Monthly staff meeting on Friday."}, follow_redirects=True
    )
    assert b"Announcement posted" in resp.data
    assert b"Monthly staff meeting on Friday." in resp.data

    resp = auth_client.get("/")
    assert b"Monthly staff meeting on Friday." in resp.data

    import re
    m = re.search(rb"/announcements/(\d+)/delete", resp.data)
    announcement_id = m.group(1).decode()
    resp = auth_client.post(f"/announcements/{announcement_id}/delete", follow_redirects=True)
    assert b"removed" in resp.data.lower()
    resp = auth_client.get("/")
    assert b"Monthly staff meeting on Friday." not in resp.data


def test_announcement_requires_message(auth_client):
    resp = auth_client.post("/announcements/new", data={"message": ""}, follow_redirects=True)
    assert b"required" in resp.data.lower()


def test_operations_role_cannot_post_announcements(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    ops_client = auth_client.application.test_client()
    ops_client.post("/login", data={"username": "opsuser", "password": "secret123"})

    resp = ops_client.get("/")
    assert b"Post Announcement" not in resp.data

    ops_client.post("/announcements/new", data={"message": "sneaky"})
    resp = auth_client.get("/")
    assert b"sneaky" not in resp.data


def test_operations_role_sees_auto_updates_and_birthdays(auth_client):
    from datetime import timedelta

    dob = (app_module._colombo_today() + timedelta(days=2)).replace(year=1990)
    _register_employee(auth_client, "Ops Visible Birthday", dob.isoformat())

    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    ops_client = auth_client.application.test_client()
    ops_client.post("/login", data={"username": "opsuser", "password": "secret123"})

    resp = ops_client.get("/")
    assert b"Auto Updates" in resp.data
    assert b"Ops Visible Birthday" in resp.data
