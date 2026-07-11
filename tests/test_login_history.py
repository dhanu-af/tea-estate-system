def test_successful_login_is_recorded(auth_client):
    resp = auth_client.get("/users/login-history")
    assert resp.status_code == 200
    assert b"admin" in resp.data
    assert b"Success" in resp.data


def test_failed_login_is_recorded(client):
    client.post("/setup", data={"username": "admin", "password": "secret123", "confirm_password": "secret123"})
    client.post("/login", data={"username": "admin", "password": "wrongpassword"})
    client.post("/login", data={"username": "admin", "password": "secret123"})

    resp = client.get("/users/login-history")
    assert resp.status_code == 200
    rows = resp.data.count(b"admin")
    assert rows >= 2
    assert b"Failed" in resp.data
    assert b"Success" in resp.data


def test_logout_records_logout_time(auth_client):
    auth_client.post("/logout")
    auth_client.post("/login", data={"username": "admin", "password": "secret123"})

    resp = auth_client.get("/users/login-history")
    assert resp.status_code == 200
    # The first (now-closed) session row should show a logout time rather than "—".
    assert b"logout" in resp.data.lower() or b"Logout Time" in resp.data


def test_login_history_filter_by_status(auth_client):
    auth_client.post("/logout")
    auth_client.post("/login", data={"username": "admin", "password": "wrongpassword"})
    auth_client.post("/login", data={"username": "admin", "password": "secret123"})

    resp = auth_client.get("/users/login-history?status=Failed")
    assert resp.status_code == 200
    assert b"Failed" in resp.data


def test_login_history_is_admin_only(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    ops_client = auth_client.application.test_client()
    ops_client.post("/login", data={"username": "opsuser", "password": "secret123"})

    resp = ops_client.get("/users/login-history", follow_redirects=True)
    assert b"have access to that section" in resp.data
