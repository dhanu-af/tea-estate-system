def test_payroll_combines_harvest_and_hourly_pay(auth_client):
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

    resp = auth_client.get("/payroll?from=2026-07-01&to=2026-07-10")
    # 8h x 150 = 1200 time-based, 24kg x 50 = 1200 harvest, total 2400
    assert b"1200.0" in resp.data
    assert b"2400.0" in resp.data


def test_payroll_flags_missing_rate_instead_of_guessing(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})

    resp = auth_client.get("/payroll?from=2026-07-01&to=2026-07-10")
    assert b"Set a rate on employee" in resp.data


def test_payroll_csv_export_matches_page(auth_client):
    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "rate_per_kg": "50"},
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})

    resp = auth_client.get("/payroll/export.csv?from=2026-07-01&to=2026-07-10")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    body = resp.data.decode("utf-8")
    assert "EMP-0001,Alice" in body
    assert "1200.0" in body


def test_salary_advance_reduces_net_pay(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    # Total pay = 24 x 50 = 1200

    resp = auth_client.post(
        "/payroll/advance",
        data={"employee_id": "1", "date": "2026-07-10", "amount": "500", "note": "Advance"},
        follow_redirects=True,
    )
    assert b"Salary advance of 500.0 recorded" in resp.data
    assert b"500.0" in resp.data  # advance shown
    assert b"700.0" in resp.data  # net pay = 1200 - 500


def test_salary_advance_does_not_affect_income_page_cost(auth_client):
    # Advances are a prepayment of the same earned pay, not an extra cost —
    # the Income & Profit page's cost figure must stay based on total_pay, unaffected by advances.
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    auth_client.post("/income/price", data={"date": "2026-07-10", "price_per_kg": "200"})

    resp_before = auth_client.get("/income?view=daily&date=2026-07-10")
    assert b"1200.0" in resp_before.data  # employee cost card

    auth_client.post(
        "/payroll/advance",
        data={"employee_id": "1", "date": "2026-07-10", "amount": "500"},
    )

    resp_after = auth_client.get("/income?view=daily&date=2026-07-10")
    assert b"1200.0" in resp_after.data  # unchanged despite the advance


def test_deleting_advance_removes_it(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/payroll/advance", data={"employee_id": "1", "date": "2026-07-10", "amount": "300"}
    )
    resp = auth_client.post("/payroll/advance/1/delete", follow_redirects=True)
    assert b"Salary advance removed" in resp.data
    assert b"No advances recorded" in resp.data


def test_non_numeric_advance_amount_is_rejected(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    resp = auth_client.post(
        "/payroll/advance",
        data={"employee_id": "1", "date": "2026-07-10", "amount": "abc"},
        follow_redirects=True,
    )
    assert b"must be a number" in resp.data


def _pdf_text(response_data):
    import io as _io
    from pypdf import PdfReader

    reader = PdfReader(_io.BytesIO(response_data))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_payslip_pdf_contains_correct_figures(auth_client):
    auth_client.post(
        "/employees/new",
        data={
            "full_name": "Alice",
            "job_position": "Tea picker",
            "estate_division": "North",
            "rate_per_kg": "50",
            "hourly_rate": "150",
        },
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
    auth_client.post(
        "/payroll/advance", data={"employee_id": "1", "date": "2026-07-10", "amount": "500", "note": "Advance"}
    )

    resp = auth_client.get("/payroll/payslip/1?from=2026-07-01&to=2026-07-10")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert "payslip_EMP-0001_2026-07-01_to_2026-07-10.pdf" in resp.headers["Content-Disposition"]

    text = _pdf_text(resp.data)
    assert "DKNS Tea Lands" in text
    assert "Alice" in text
    assert "EMP-0001" in text
    assert "Tea picker" in text
    assert "North" in text
    assert "1200.0" in text  # time-based pay
    assert "1200.0" in text  # harvest pay (same value, both present)
    assert "2400.0" in text  # total pay
    assert "500.0" in text  # advance
    assert "1900.0" in text  # net pay = 2400 - 500


def test_payslip_for_employee_with_no_rate_does_not_crash(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Bob"})
    resp = auth_client.get("/payroll/payslip/1?from=2026-07-01&to=2026-07-10")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    text = _pdf_text(resp.data)
    assert "Bob" in text


def test_payslip_for_unknown_employee_redirects(auth_client):
    resp = auth_client.get("/payroll/payslip/999", follow_redirects=True)
    assert b"Employee not found" in resp.data


def test_epf_etf_deducted_and_contributed_when_applicable(auth_client):
    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "rate_per_kg": "50", "epf_etf_applicable": "1"},
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    # Total pay = 24 x 50 = 1200. Employee EPF 8% = 96, employer EPF 12% = 144, ETF 3% = 36.

    resp = auth_client.get("/payroll?from=2026-07-01&to=2026-07-10")
    assert b"96.0" in resp.data  # employee EPF deducted
    assert b"1104.0" in resp.data  # net pay = 1200 - 96 (no advance)
    assert b"180.0" in resp.data  # employer EPF + ETF combined (144 + 36), shown as one column here

    # Separate employer EPF (144) and ETF (36) figures are broken out in the CSV export.
    csv_resp = auth_client.get("/payroll/export.csv?from=2026-07-01&to=2026-07-10")
    csv_body = csv_resp.data.decode("utf-8")
    assert "144.0" in csv_body
    assert "36.0" in csv_body


def test_epf_etf_not_deducted_when_not_applicable(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})

    resp = auth_client.get("/payroll?from=2026-07-01&to=2026-07-10")
    # Net pay should equal total pay exactly (1200.0) since no EPF was deducted.
    assert b"1200.0" in resp.data


def test_employer_epf_etf_counted_as_real_cost_on_income_page(auth_client):
    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "rate_per_kg": "50", "epf_etf_applicable": "1"},
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    auth_client.post("/income/price", data={"date": "2026-07-10", "price_per_kg": "200"})

    resp = auth_client.get("/income?view=daily&date=2026-07-10")
    # Cost = total_pay (1200) + employer EPF (144) + employer ETF (36) = 1380, not just 1200.
    assert b"1380.0" in resp.data


def test_payslip_pdf_shows_epf_deduction(auth_client):
    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "rate_per_kg": "50", "epf_etf_applicable": "1"},
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})

    resp = auth_client.get("/payroll/payslip/1?from=2026-07-01&to=2026-07-10")
    text = _pdf_text(resp.data)
    assert "96.0" in text  # employee EPF deduction
    assert "1104.0" in text  # net pay
    assert "144.0" in text  # employer EPF mentioned in the footnote
    assert "36.0" in text  # employer ETF mentioned in the footnote
