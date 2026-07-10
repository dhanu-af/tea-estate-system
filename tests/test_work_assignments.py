def test_harvest_weighing_updates_output_and_productivity(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )

    resp = auth_client.post(
        "/work-assignments/1/weigh",
        data={"gross_weight": "25", "tare_weight": "1"},
        follow_redirects=True,
    )
    # Spec example: target 20kg, collected 24kg -> 120% productivity, auto-completed.
    assert b"Output is now 24.0 kg" in resp.data

    detail = auth_client.get("/work-assignments/1")
    assert b"120.0%" in detail.data
    assert b"Completed" in detail.data


def test_multiple_weighings_accumulate(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "10", "tare_weight": "0"})
    resp = auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "5", "tare_weight": "0"}, follow_redirects=True)
    assert b"Output is now 15.0 kg" in resp.data


def test_deleting_a_weighing_corrects_output(auth_client):
    import database
    import app as app_module

    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})

    resp = auth_client.post("/work-assignments/1/weighings/1/delete", follow_redirects=True)
    assert b"Weighing entry removed" in resp.data

    with app_module.app.app_context():
        conn = database.get_connection()
        row = conn.execute("SELECT actual_output, status FROM work_assignments WHERE id = 1").fetchone()
    assert row["actual_output"] == 0
    assert row["status"] == "Pending"


def test_non_numeric_weight_is_rejected(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    resp = auth_client.post(
        "/work-assignments/1/weigh", data={"gross_weight": "not-a-number"}, follow_redirects=True
    )
    assert b"must be numbers" in resp.data


def test_non_harvest_task_can_be_marked_completed_manually(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Pruning"},
    )
    detail = auth_client.get("/work-assignments/1")
    assert b"Pending" in detail.data

    resp = auth_client.post(
        "/work-assignments/1/status", data={"status": "Completed"}, follow_redirects=True
    )
    assert b"Status set to Completed" in resp.data

    detail = auth_client.get("/work-assignments/1")
    assert b'value="Completed" selected' in detail.data


def test_invalid_status_value_is_rejected(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Pruning"},
    )
    resp = auth_client.post(
        "/work-assignments/1/status", data={"status": "Bogus"}, follow_redirects=True
    )
    assert b"Invalid status" in resp.data


def test_over_target_commission_applies_only_to_excess_kg(auth_client):
    # User's example: target 25kg, rate 50/kg, 20% commission, completed 29kg
    # -> 25kg x 50 + 4kg x 50 x 1.20 = 1250 + 240 = 1490
    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "rate_per_kg": "50", "over_target_commission_percent": "20"},
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "25"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "29", "tare_weight": "0"})

    detail = auth_client.get("/work-assignments/1")
    assert b"1490.0" in detail.data
    assert b"4.0 kg" in detail.data  # bonus kg
    assert b"240.0" in detail.data  # bonus pay
    assert b"20.0% commission" in detail.data


def test_no_commission_below_target_pays_normal_rate(auth_client):
    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "rate_per_kg": "50", "over_target_commission_percent": "20"},
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "25"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "20", "tare_weight": "0"})

    detail = auth_client.get("/work-assignments/1")
    # 20kg is under the 25kg target, so no bonus applies: 20 x 50 = 1000
    assert b"1000.0" in detail.data


def test_commission_flows_into_payroll_total(auth_client):
    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "rate_per_kg": "50", "over_target_commission_percent": "20"},
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "25"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "29", "tare_weight": "0"})

    resp = auth_client.get("/payroll?from=2026-07-01&to=2026-07-10")
    assert b"1490.0" in resp.data
    assert b"4.0" in resp.data  # bonus kg column
