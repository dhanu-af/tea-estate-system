import pytest


@pytest.fixture
def super_admin_client(client):
    client.post("/setup", data={"username": "DKNS", "password": "secret123", "confirm_password": "secret123"})
    client.post("/login", data={"username": "DKNS", "password": "secret123"})
    return client


def _create_second_admin(client, username="otheradmin"):
    return client.post(
        "/users/new",
        data={"username": username, "password": "secret123", "confirm_password": "secret123", "role": "Admin"},
        follow_redirects=True,
    )


def test_dkns_can_manage_users(super_admin_client):
    resp = _create_second_admin(super_admin_client)
    assert b"User otheradmin created" in resp.data


def test_other_admin_is_locked_out_of_user_management_once_dkns_exists(super_admin_client):
    _create_second_admin(super_admin_client)
    other_client = super_admin_client.application.test_client()
    other_client.post("/login", data={"username": "otheradmin", "password": "secret123"})

    for path in ("/users", "/users/new"):
        resp = other_client.get(path, follow_redirects=True)
        assert b"Only the DKNS" in resp.data or b"System Owner" in resp.data, path

    resp = other_client.get(f"/users/2/edit", follow_redirects=True)
    assert b"System Owner" in resp.data


def test_other_admin_can_still_view_login_history(super_admin_client):
    _create_second_admin(super_admin_client)
    other_client = super_admin_client.application.test_client()
    other_client.post("/login", data={"username": "otheradmin", "password": "secret123"})

    resp = other_client.get("/users/login-history")
    assert resp.status_code == 200


def test_dkns_cannot_be_disabled_even_by_itself(super_admin_client):
    import re

    resp = super_admin_client.get("/users")
    m = re.search(rb"/users/(\d+)/toggle", resp.data)
    # DKNS is the only user, so no toggle form should even be rendered for it.
    assert m is None

    resp = super_admin_client.post("/users/1/toggle", follow_redirects=True)
    assert b"Access Denied" in resp.data
    assert b"Protected System Account" in resp.data
    assert b"cannot be disabled" in resp.data

    resp = super_admin_client.get("/users")
    assert b"Active" in resp.data  # still active


def test_dkns_cannot_be_deleted_even_by_itself(super_admin_client):
    resp = super_admin_client.post("/users/1/delete", follow_redirects=True)
    assert b"Access Denied" in resp.data
    assert b"Protected System Account" in resp.data
    assert b"cannot be deleted" in resp.data

    resp = super_admin_client.get("/users")
    assert b"DKNS" in resp.data


def test_dkns_role_and_username_cannot_be_changed(super_admin_client):
    resp = super_admin_client.post(
        "/users/1/edit", data={"username": "DKNS", "role": "Dhanu Operations"}, follow_redirects=True
    )
    assert b"Access Denied" in resp.data
    assert b"Protected System Account" in resp.data

    resp = super_admin_client.get("/users")
    assert b"Super Admin" in resp.data or b"DKNS" in resp.data


def test_dkns_username_cannot_be_renamed_away(super_admin_client):
    resp = super_admin_client.post(
        "/users/1/edit", data={"username": "NotDKNS", "role": "Admin"}, follow_redirects=True
    )
    assert b"Access Denied" in resp.data

    # confirm the rename never took effect
    resp = super_admin_client.get("/users")
    assert b"NotDKNS" not in resp.data
    assert b"DKNS" in resp.data


def test_dkns_can_still_change_its_own_password(super_admin_client):
    resp = super_admin_client.post(
        "/users/1/password",
        data={"new_password": "newSecret456", "confirm_new_password": "newSecret456"},
        follow_redirects=True,
    )
    assert b"Password updated" in resp.data

    super_admin_client.post("/logout")
    resp = super_admin_client.post(
        "/login", data={"username": "DKNS", "password": "newSecret456"}, follow_redirects=True
    )
    assert b"Welcome, DKNS" in resp.data


def test_cannot_create_a_second_dkns_account_via_username_case_variant(super_admin_client):
    resp = super_admin_client.post(
        "/users/new",
        data={"username": "dkns", "password": "secret123", "confirm_password": "secret123", "role": "Admin"},
        follow_redirects=True,
    )
    assert b"Access Denied" in resp.data
    assert b"Protected System Account" in resp.data


def test_users_list_hides_management_controls_for_dkns_row(super_admin_client):
    resp = super_admin_client.get("/users")
    assert b"Protected system account" in resp.data
    # No Disable/Delete form should target the DKNS row (id 1, the only user here).
    import re
    assert re.search(rb"/users/1/toggle", resp.data) is None
    assert re.search(rb"/users/1/delete", resp.data) is None


def test_user_management_nav_link_hidden_from_non_dkns_admin(super_admin_client):
    _create_second_admin(super_admin_client)
    other_client = super_admin_client.application.test_client()
    other_client.post("/login", data={"username": "otheradmin", "password": "secret123"})

    resp = other_client.get("/")
    assert b"User Management" not in resp.data
    assert b"Login History" in resp.data


def test_user_management_nav_link_visible_to_dkns(super_admin_client):
    resp = super_admin_client.get("/")
    assert b"User Management" in resp.data


def test_bootstrap_admin_can_manage_users_before_dkns_exists(auth_client):
    # No DKNS account exists in this DB (auth_client bootstraps as "admin"), so the
    # existing single-Admin-role behavior must keep working for fresh installs.
    resp = auth_client.get("/users")
    assert resp.status_code == 200
    resp = _create_second_admin(auth_client, username="anotheradmin")
    assert b"User anotheradmin created" in resp.data
