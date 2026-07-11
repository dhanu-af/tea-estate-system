import re
from datetime import date

import app as app_module


def test_prepaid_expense_amortization_matches_fertilizer_example(auth_client):
    # Fertilizer bought for a 3-month supply should expense one third of the
    # cost each month — the user's exact example from the requirements.
    start = app_module._add_months(date.today(), -2).isoformat()  # 2 whole months elapsed
    resp = auth_client.post(
        "/finance/depreciation/new",
        data={
            "description": "Fertilizer - 3 month supply",
            "category": "Fertilizer",
            "total_cost": "300",
            "start_date": start,
            "period_type": "Monthly",
            "period_count": "3",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"amortize over 3 monthly period" in resp.data

    resp = auth_client.get("/finance/depreciation")
    assert resp.status_code == 200
    assert b"Fertilizer - 3 month supply" in resp.data
    assert b"100.0" in resp.data  # 300 / 3 = 100 per month
    assert b"200.0" in resp.data  # 2 months elapsed => 200 accumulated
    assert b"100.0" in resp.data  # 100 remaining

    resp = auth_client.get(f"/finance/statement?view=custom&from={start}&to={date.today().isoformat()}")
    assert resp.status_code == 200
    assert b"Amortization" in resp.data
    assert b"Depreciation" in resp.data


def test_asset_depreciation_appears_in_ledger(auth_client):
    purchase_date = app_module._add_months(date.today(), -6).isoformat()
    auth_client.post(
        "/assets/new",
        data={
            "name": "Weighing Scale",
            "category": "Equipment",
            "purchase_date": purchase_date,
            "purchase_cost": "6000",
            "salvage_value": "0",
            "depreciation_period_months": "12",
            "status": "Active",
        },
        follow_redirects=True,
    )
    resp = auth_client.get(f"/finance/statement?view=custom&from={purchase_date}&to={date.today().isoformat()}")
    assert resp.status_code == 200
    assert b"Weighing Scale" in resp.data
    assert b"Depreciation" in resp.data


def test_prepaid_expense_can_be_deleted(auth_client):
    start = date.today().isoformat()
    auth_client.post(
        "/finance/depreciation/new",
        data={
            "description": "Insurance premium",
            "total_cost": "1200",
            "start_date": start,
            "period_type": "Monthly",
            "period_count": "12",
        },
        follow_redirects=True,
    )
    resp = auth_client.get("/finance/depreciation")
    m = re.search(rb"/finance/depreciation/(\d+)/delete", resp.data)
    prepaid_id = m.group(1).decode()

    resp = auth_client.post(f"/finance/depreciation/{prepaid_id}/delete", follow_redirects=True)
    assert b"removed" in resp.data.lower()
    resp = auth_client.get("/finance/depreciation")
    assert b"Insurance premium" not in resp.data


def test_depreciation_rejects_invalid_input(auth_client):
    resp = auth_client.post(
        "/finance/depreciation/new",
        data={"description": "", "total_cost": "100", "start_date": date.today().isoformat(),
              "period_type": "Monthly", "period_count": "3"},
        follow_redirects=True,
    )
    assert b"required" in resp.data.lower()

    resp = auth_client.post(
        "/finance/depreciation/new",
        data={"description": "Test", "total_cost": "-50", "start_date": date.today().isoformat(),
              "period_type": "Monthly", "period_count": "3"},
        follow_redirects=True,
    )
    assert b"greater than zero" in resp.data


def test_depreciation_is_admin_only(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    ops_client = auth_client.application.test_client()
    ops_client.post("/login", data={"username": "opsuser", "password": "secret123"})

    resp = ops_client.get("/finance/depreciation", follow_redirects=True)
    assert b"have access to that section" in resp.data
