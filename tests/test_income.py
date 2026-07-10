def test_setting_price_computes_income_and_profit(auth_client):
    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "rate_per_kg": "50", "hourly_rate": "150"},
    )
    auth_client.post(
        "/attendance/new",
        data={
            "employee_id": "1",
            "date": "2026-07-10",
            "status": "Present",
            "check_in": "07:30",
            "check_out": "16:30",
            "break_start": "12:00",
            "break_end": "13:00",
        },
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})

    auth_client.post("/income/price", data={"date": "2026-07-10", "price_per_kg": "211"})

    resp = auth_client.get("/income?date=2026-07-10")
    # 24kg x 211 = 5064 income; cost = 1200 (hourly) + 1200 (harvest, 24kg x 50) = 2400; profit = 2664
    assert b"5064.0" in resp.data
    assert b"2400.0" in resp.data
    assert b"2664.0" in resp.data


def test_price_is_per_day_not_shared_across_dates(auth_client):
    auth_client.post("/income/price", data={"date": "2026-07-10", "price_per_kg": "200"})
    auth_client.post("/income/price", data={"date": "2026-07-11", "price_per_kg": "250"})

    resp_10 = auth_client.get("/income?date=2026-07-10")
    resp_11 = auth_client.get("/income?date=2026-07-11")
    assert b'value="200.0"' in resp_10.data or b"200.0" in resp_10.data
    assert b'value="250.0"' in resp_11.data or b"250.0" in resp_11.data


def test_updating_price_overwrites_not_duplicates(auth_client):
    auth_client.post("/income/price", data={"date": "2026-07-10", "price_per_kg": "200"})
    resp = auth_client.post(
        "/income/price", data={"date": "2026-07-10", "price_per_kg": "230"}, follow_redirects=True
    )
    assert b"230.0" in resp.data
    assert b"set to 230.0" in resp.data


def test_non_numeric_price_is_rejected(auth_client):
    resp = auth_client.post(
        "/income/price", data={"date": "2026-07-10", "price_per_kg": "abc"}, follow_redirects=True
    )
    assert b"must be a number" in resp.data


def test_income_with_no_price_set_shows_placeholder(auth_client):
    resp = auth_client.get("/income?date=2026-07-10")
    assert b"set a price above" in resp.data


def test_expense_reduces_profit_and_shows_in_cost_breakdown(auth_client):
    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "rate_per_kg": "50"},
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "100", "tare_weight": "0"})
    auth_client.post("/income/price", data={"date": "2026-07-10", "price_per_kg": "200"})
    # Income = 100 x 200 = 20000; employee pay = 100 x 50 = 5000; no expenses yet -> profit 15000
    resp = auth_client.get("/income?date=2026-07-10")
    assert b"15000.0" in resp.data

    resp = auth_client.post(
        "/income/expense",
        data={"date": "2026-07-10", "category": "Fertilizer", "amount": "3000", "note": "NPK"},
        follow_redirects=True,
    )
    assert b"Fertilizer expense of 3000.0 added" in resp.data
    # Total cost now 5000 + 3000 = 8000; profit = 20000 - 8000 = 12000
    assert b"12000.0" in resp.data
    assert b"8000.0" in resp.data
    # Cost breakdown: employee pay 5000/8000 = 62.5%, fertilizer 3000/8000 = 37.5%
    assert b"62.5%" in resp.data
    assert b"37.5%" in resp.data


def test_deleting_expense_restores_profit(auth_client):
    auth_client.post("/income/price", data={"date": "2026-07-10", "price_per_kg": "200"})
    auth_client.post(
        "/income/expense",
        data={"date": "2026-07-10", "category": "Fuel", "amount": "1000"},
    )
    resp = auth_client.post("/income/expense/1/delete", follow_redirects=True)
    assert b"Expense removed" in resp.data
    assert b"No expenses logged" in resp.data


def test_invalid_expense_category_is_rejected(auth_client):
    resp = auth_client.post(
        "/income/expense",
        data={"date": "2026-07-10", "category": "Bogus", "amount": "100"},
        follow_redirects=True,
    )
    assert b"Invalid expense category" in resp.data


def test_non_numeric_expense_amount_is_rejected(auth_client):
    resp = auth_client.post(
        "/income/expense",
        data={"date": "2026-07-10", "category": "Fuel", "amount": "abc"},
        follow_redirects=True,
    )
    assert b"must be a number" in resp.data


def test_weekly_view_spans_monday_to_sunday(auth_client):
    # 2026-07-10 is a Friday; its week should be Mon 2026-07-06 to Sun 2026-07-12.
    resp = auth_client.get("/income?view=weekly&date=2026-07-10")
    assert b"Showing 2026-07-06 to 2026-07-12" in resp.data


def test_monthly_view_spans_full_calendar_month(auth_client):
    resp = auth_client.get("/income?view=monthly&date=2026-07-10")
    assert b"Showing 2026-07-01 to 2026-07-31" in resp.data


def test_custom_view_uses_explicit_range(auth_client):
    resp = auth_client.get("/income?view=custom&from=2026-06-15&to=2026-07-20")
    assert b"Showing 2026-06-15 to 2026-07-20" in resp.data


def test_income_multiplies_each_day_by_that_days_price(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-09", "task_type": "Plucking"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "55", "tare_weight": "0"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking"},
    )
    auth_client.post("/work-assignments/2/weigh", data={"gross_weight": "249", "tare_weight": "0"})

    auth_client.post("/income/price", data={"date": "2026-07-09", "price_per_kg": "200"})
    auth_client.post("/income/price", data={"date": "2026-07-10", "price_per_kg": "211"})

    resp = auth_client.get("/income?view=weekly&date=2026-07-10")
    # 55kg x 200 + 249kg x 211 = 11000 + 52539 = 63539
    assert b"63539.0" in resp.data
    assert b"304.0" in resp.data  # total harvest kg for the week


def test_unpriced_days_are_excluded_from_income_and_flagged(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-09", "task_type": "Plucking"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "55", "tare_weight": "0"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking"},
    )
    auth_client.post("/work-assignments/2/weigh", data={"gross_weight": "249", "tare_weight": "0"})

    # Only price 07-10, leave 07-09 unpriced.
    auth_client.post("/income/price", data={"date": "2026-07-10", "price_per_kg": "211"})

    resp = auth_client.get("/income?view=weekly&date=2026-07-10")
    assert b"55.0 kg harvested on days with no price set" in resp.data
    # Income should only count the priced day: 249 x 211 = 52539, not the unpriced 55kg.
    assert b"52539.0" in resp.data
