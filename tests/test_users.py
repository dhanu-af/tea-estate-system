def test_admin_can_create_operations_user(auth_client):
    resp = auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
        follow_redirects=True,
    )
    assert b"User opsuser created" in resp.data
    assert b"Dhanu Operations" in resp.data


def test_duplicate_username_rejected_on_create(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    resp = auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
        follow_redirects=True,
    )
    assert b"already taken" in resp.data


def test_password_mismatch_rejected_on_create(auth_client):
    resp = auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "different", "role": "Dhanu Operations"},
    )
    assert b"do not match" in resp.data


def test_invalid_role_rejected_on_create(auth_client):
    resp = auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Superuser"},
    )
    assert b"Select a valid role" in resp.data


def test_admin_can_edit_username_and_role(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    resp = auth_client.post(
        "/users/2/edit",
        data={"username": "opsuser2", "role": "Admin"},
        follow_redirects=True,
    )
    assert b"User updated" in resp.data
    assert b"opsuser2" in resp.data


def test_admin_can_change_user_password(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    resp = auth_client.post(
        "/users/2/password",
        data={"new_password": "newpass456", "confirm_new_password": "newpass456"},
        follow_redirects=True,
    )
    assert b"Password updated for opsuser" in resp.data

    # log out admin, log in as opsuser with the new password to confirm it actually took effect
    auth_client.post("/logout")
    resp = auth_client.post("/login", data={"username": "opsuser", "password": "newpass456"}, follow_redirects=True)
    assert b"Welcome, opsuser" in resp.data


def test_operations_user_can_access_operational_pages(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    ops_client = auth_client.application.test_client()
    ops_client.post("/login", data={"username": "opsuser", "password": "secret123"})

    for path in ("/", "/employees", "/attendance", "/work-assignments"):
        resp = ops_client.get(path)
        assert resp.status_code == 200, path


def test_operations_user_blocked_from_admin_only_sections(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    ops_client = auth_client.application.test_client()
    ops_client.post("/login", data={"username": "opsuser", "password": "secret123"})

    for path in ("/payroll", "/income", "/finance", "/users"):
        resp = ops_client.get(path, follow_redirects=True)
        assert b"have access to that section" in resp.data, path
        assert b"Dashboard" in resp.data


def test_disabling_user_blocks_future_login(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    auth_client.post("/users/2/toggle")

    resp = auth_client.application.test_client().post(
        "/login", data={"username": "opsuser", "password": "secret123"}, follow_redirects=True
    )
    assert b"disabled" in resp.data


def test_disabling_user_ends_their_active_session_immediately(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    ops_client = auth_client.application.test_client()
    ops_client.post("/login", data={"username": "opsuser", "password": "secret123"})
    assert ops_client.get("/").status_code == 200

    auth_client.post("/users/2/toggle")  # admin disables opsuser mid-session

    resp = ops_client.get("/", follow_redirects=True)
    assert b"account has been disabled" in resp.data


def test_cannot_disable_own_account_as_sole_admin(auth_client):
    # auth_client is logged in as user id 1, the only admin — the self-lockout
    # guard fires here (before the separate last-admin guard even applies).
    resp = auth_client.post("/users/1/toggle", follow_redirects=True)
    assert b"own account" in resp.data


def test_cannot_delete_own_account_as_sole_admin(auth_client):
    resp = auth_client.post("/users/1/delete", follow_redirects=True)
    assert b"own account" in resp.data


def test_second_admin_cannot_disable_the_only_other_active_admin(auth_client):
    # Two admins exist; admin2 disables admin1, leaving admin2 as the sole active
    # admin. Admin2 must then be blocked from disabling themselves too.
    auth_client.post(
        "/users/new",
        data={"username": "admin2", "password": "secret123", "confirm_password": "secret123", "role": "Admin"},
    )
    admin2_client = auth_client.application.test_client()
    admin2_client.post("/login", data={"username": "admin2", "password": "secret123"})
    admin2_client.post("/users/1/toggle")  # disable admin1 — admin2 is now the sole active admin

    resp = admin2_client.post("/users/2/toggle", follow_redirects=True)
    assert b"own account" in resp.data


def test_second_admin_allows_disabling_first(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "admin2", "password": "secret123", "confirm_password": "secret123", "role": "Admin"},
    )
    admin2_client = auth_client.application.test_client()
    admin2_client.post("/login", data={"username": "admin2", "password": "secret123"})

    resp = admin2_client.post("/users/1/toggle", follow_redirects=True)
    assert b"disabled" in resp.data


def test_cannot_change_role_of_last_admin(auth_client):
    resp = auth_client.post("/users/1/edit", data={"username": "admin", "role": "Dhanu Operations"}, follow_redirects=True)
    assert b"last active Admin" in resp.data


def test_deleting_a_user_removes_them(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    resp = auth_client.post("/users/2/delete", follow_redirects=True)
    assert b"User opsuser removed" in resp.data

    resp = auth_client.get("/users")
    assert b"opsuser" not in resp.data
