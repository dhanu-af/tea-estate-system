import re
from datetime import date, timedelta

import app as app_module


def _create_asset(client, **overrides):
    purchase_date = app_module._add_months(date.today(), -12).isoformat()  # exactly 1 year ago
    data = {
        "name": "Tea Plucking Machine",
        "category": "Machinery",
        "purchase_date": purchase_date,
        "purchase_cost": "120000",
        "salvage_value": "0",
        "depreciation_period_months": "60",  # 5 years
        "supplier": "Colombo Machinery Ltd",
        "serial_number": "SN-9001",
        "assigned_location": "Field 3",
        "status": "Active",
    }
    data.update(overrides)
    return client.post("/assets/new", data=data, follow_redirects=True)


def _extract_asset_id(resp_data):
    m = re.search(rb"/assets/(\d+)", resp_data)
    return m.group(1).decode()


def test_create_asset_generates_asset_code(auth_client):
    resp = _create_asset(auth_client)
    assert resp.status_code == 200
    assert b"AST-" in resp.data
    assert b"Tea Plucking Machine" in resp.data


def test_asset_depreciation_after_one_year(auth_client):
    resp = _create_asset(auth_client)
    asset_id = _extract_asset_id(resp.data)

    resp = auth_client.get(f"/assets/{asset_id}")
    assert resp.status_code == 200
    # 120000 / 60 months = 2000/month; after 12 months elapsed => 24000 accumulated, 96000 book value
    assert b"2000.0" in resp.data
    assert b"24000.0" in resp.data
    assert b"96000.0" in resp.data


def test_asset_without_depreciation_fields_shows_not_depreciable(auth_client):
    resp = _create_asset(auth_client, purchase_cost="", depreciation_period_months="")
    asset_id = _extract_asset_id(resp.data)
    resp = auth_client.get(f"/assets/{asset_id}")
    assert b"Not depreciable" in resp.data


def test_maintenance_log_updates_next_service_date(auth_client):
    resp = _create_asset(auth_client)
    asset_id = _extract_asset_id(resp.data)

    next_service = (date.today() + timedelta(days=90)).isoformat()
    resp = auth_client.post(
        f"/assets/{asset_id}/maintenance",
        data={
            "maintenance_date": date.today().isoformat(),
            "description": "Blade sharpening",
            "cost": "1500",
            "performed_by": "Estate Workshop",
            "next_service_date": next_service,
        },
        follow_redirects=True,
    )
    assert b"Blade sharpening" in resp.data

    resp = auth_client.get(f"/assets/{asset_id}")
    assert next_service.encode() in resp.data or next_service[8:10].encode() in resp.data


def test_asset_can_be_assigned_to_employee(auth_client):
    auth_client.post(
        "/employees/new",
        data={"full_name": "Kamal Perera", "job_position": "Field Worker"},
        follow_redirects=True,
    )
    resp = auth_client.get("/employees")
    m = re.search(rb"/employees/(\d+)/edit", resp.data)
    employee_id = m.group(1).decode()

    resp = _create_asset(auth_client, assigned_employee_id=employee_id)
    assert b"Kamal Perera" in resp.data


def test_assets_are_admin_only(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    ops_client = auth_client.application.test_client()
    ops_client.post("/login", data={"username": "opsuser", "password": "secret123"})

    resp = ops_client.get("/assets", follow_redirects=True)
    assert b"have access to that section" in resp.data


def test_delete_asset_removes_it(auth_client):
    resp = _create_asset(auth_client)
    asset_id = _extract_asset_id(resp.data)
    resp = auth_client.post(f"/assets/{asset_id}/delete", follow_redirects=True)
    assert b"removed" in resp.data.lower()
    resp = auth_client.get("/assets")
    assert b"Tea Plucking Machine" not in resp.data
