from datetime import date, timedelta


def _create_item(client, **overrides):
    data = {
        "name": "Urea Fertilizer",
        "category": "Fertilizer",
        "unit": "kg",
        "minimum_stock_level": "50",
        "unit_cost": "100",
    }
    data.update(overrides)
    return client.post("/inventory/items/new", data=data, follow_redirects=True)


def test_create_inventory_item(auth_client):
    resp = _create_item(auth_client)
    assert resp.status_code == 200
    assert b"Urea Fertilizer" in resp.data


def test_stock_in_increases_balance_and_updates_last_cost(auth_client):
    _create_item(auth_client)
    import re
    resp = auth_client.get("/inventory")
    m = re.search(rb"/inventory/items/(\d+)", resp.data)
    item_id = m.group(1).decode()

    today = date.today().isoformat()
    auth_client.post(
        f"/inventory/items/{item_id}/transaction",
        data={
            "transaction_type": "In",
            "transaction_date": today,
            "quantity": "200",
            "unit_cost": "110",
            "supplier": "Lanka Agro",
            "batch_number": "B-001",
        },
        follow_redirects=True,
    )
    resp = auth_client.get(f"/inventory/items/{item_id}")
    assert b"200.0" in resp.data or b"200" in resp.data
    assert b"110.0" in resp.data or b"110" in resp.data


def test_stock_out_cannot_exceed_balance(auth_client):
    _create_item(auth_client)
    import re
    resp = auth_client.get("/inventory")
    m = re.search(rb"/inventory/items/(\d+)", resp.data)
    item_id = m.group(1).decode()

    today = date.today().isoformat()
    auth_client.post(
        f"/inventory/items/{item_id}/transaction",
        data={"transaction_type": "In", "transaction_date": today, "quantity": "50", "unit_cost": "100"},
    )
    resp = auth_client.post(
        f"/inventory/items/{item_id}/transaction",
        data={"transaction_type": "Out", "transaction_date": today, "quantity": "999"},
        follow_redirects=True,
    )
    assert b"exceed" in resp.data.lower() or b"insufficient" in resp.data.lower() or b"balance" in resp.data.lower()


def test_low_stock_flagged_below_minimum(auth_client):
    _create_item(auth_client, name="Chlorpyrifos", category="Chemicals", minimum_stock_level="100")
    import re
    resp = auth_client.get("/inventory")
    m = re.search(rb"/inventory/items/(\d+)", resp.data)
    item_id = m.group(1).decode()

    today = date.today().isoformat()
    auth_client.post(
        f"/inventory/items/{item_id}/transaction",
        data={"transaction_type": "In", "transaction_date": today, "quantity": "20", "unit_cost": "50"},
    )
    resp = auth_client.get("/inventory")
    assert b"Low" in resp.data


def test_inventory_transaction_log_lists_all_items(auth_client):
    _create_item(auth_client)
    import re
    resp = auth_client.get("/inventory")
    m = re.search(rb"/inventory/items/(\d+)", resp.data)
    item_id = m.group(1).decode()
    today = date.today().isoformat()
    auth_client.post(
        f"/inventory/items/{item_id}/transaction",
        data={"transaction_type": "In", "transaction_date": today, "quantity": "30", "unit_cost": "100"},
    )
    resp = auth_client.get("/inventory/transactions")
    assert resp.status_code == 200
    assert b"Urea Fertilizer" in resp.data


def test_inventory_accessible_to_operations_role(auth_client):
    auth_client.post(
        "/users/new",
        data={"username": "opsuser", "password": "secret123", "confirm_password": "secret123", "role": "Dhanu Operations"},
    )
    ops_client = auth_client.application.test_client()
    ops_client.post("/login", data={"username": "opsuser", "password": "secret123"})
    resp = ops_client.get("/inventory")
    assert resp.status_code == 200


def test_expiry_date_and_batch_recorded(auth_client):
    _create_item(auth_client, name="Packaging Sacks", category="Packaging Materials")
    import re
    resp = auth_client.get("/inventory")
    m = re.search(rb"/inventory/items/(\d+)", resp.data)
    item_id = m.group(1).decode()

    today = date.today().isoformat()
    expiry = (date.today() + timedelta(days=180)).isoformat()
    auth_client.post(
        f"/inventory/items/{item_id}/transaction",
        data={
            "transaction_type": "In",
            "transaction_date": today,
            "quantity": "500",
            "unit_cost": "5",
            "batch_number": "BATCH-42",
            "expiry_date": expiry,
        },
    )
    resp = auth_client.get(f"/inventory/items/{item_id}")
    assert b"BATCH-42" in resp.data
