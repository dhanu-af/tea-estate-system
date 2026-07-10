def test_root_redirects_to_setup_when_no_users(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/setup" in resp.headers["Location"]


def test_setup_creates_admin_and_redirects_to_login(client):
    resp = client.post(
        "/setup",
        data={"username": "admin", "password": "secret123", "confirm_password": "secret123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_setup_rejects_mismatched_passwords(client):
    resp = client.post(
        "/setup",
        data={"username": "admin", "password": "secret123", "confirm_password": "different"},
    )
    assert resp.status_code == 200
    assert b"do not match" in resp.data


def test_login_with_wrong_password_fails(client):
    client.post("/setup", data={"username": "admin", "password": "secret123", "confirm_password": "secret123"})
    resp = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert b"Invalid username or password" in resp.data


def test_login_then_dashboard_accessible(auth_client):
    resp = auth_client.get("/")
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data


def test_logout_blocks_dashboard_again(auth_client):
    auth_client.post("/logout")
    resp = auth_client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_checkin_and_badge_are_public_without_login(client):
    # No setup/login performed at all — these endpoints must still respond, not redirect to login.
    resp = client.get("/checkin/EMP-9999")
    assert resp.status_code == 404  # employee doesn't exist, but route itself isn't gated by auth


def test_crash_mid_request_does_not_lock_out_subsequent_requests(auth_client):
    # Regression test: a route crashing mid-request used to leave its DB connection open
    # (Flask's debugger keeps crashed frames alive in debug mode), which locked SQLite for
    # every later request until the server was restarted. A non-numeric rate_per_kg crashes
    # employee_new on float() before it would normally reach conn.commit()/close — this is
    # a deterministic way to reproduce "a request dies while holding an open connection".
    import pytest

    with pytest.raises(ValueError):
        auth_client.post("/employees/new", data={"full_name": "Oops", "rate_per_kg": "not-a-number"})

    # A completely unrelated write afterward must still succeed — proving no lingering lock.
    resp = auth_client.post("/employees/new", data={"full_name": "After Crash"}, follow_redirects=True)
    assert b"registered successfully" in resp.data
