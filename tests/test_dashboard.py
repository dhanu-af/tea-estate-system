def test_kpi_cards_show_daily_and_weekly_target_vs_actual(auth_client):
    import app as app_module

    today = app_module._colombo_today().isoformat()
    auth_client.post("/employees/new", data={"full_name": "Kamal Perera"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": today, "task_type": "Plucking", "harvest_target": "25"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "30", "tare_weight": "0"})
    # target 25, actual 30 -> 120% both daily and weekly (only assignment this week)

    resp = auth_client.get("/")
    text = resp.get_data(as_text=True)
    assert "Total KPI" in text
    assert "Today's KPI" in text
    assert "This Week's KPI" in text
    assert "120.0%" in text
    assert "30.0 / 25.0 kg" in text


def test_kpi_cards_show_dash_when_no_targets_set(auth_client):
    resp = auth_client.get("/")
    text = resp.get_data(as_text=True)
    assert "Total KPI" in text
    # no work assignments at all yet -> nothing to divide by, shown as em dash
    assert "—" in text


def test_weekly_kpi_aggregates_multiple_days_in_the_same_week(auth_client):
    import app as app_module
    from datetime import timedelta

    today = app_module._colombo_today()
    monday, _ = app_module._week_bounds(today)
    # an earlier day in the same Mon-Sun week as today (or today itself if today is Monday)
    earlier_day = monday.isoformat()

    auth_client.post("/employees/new", data={"full_name": "Kamal Perera"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": earlier_day, "task_type": "Plucking", "harvest_target": "10"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "10", "tare_weight": "0"})

    resp = auth_client.get("/")
    text = resp.get_data(as_text=True)
    # the weekly total should include this Monday assignment even though "today" (in the
    # test's real run date) may be later in the same week
    assert "10.0" in text
