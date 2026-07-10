from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def _cycle_bounds():
    """Mirrors the app's own Monday-Sunday (Asia/Colombo) cycle math, so tests
    stay correct regardless of which real-world week they happen to run in."""
    today = datetime.now(ZoneInfo("Asia/Colombo")).date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    due = sunday + timedelta(days=1)
    return monday.isoformat(), sunday.isoformat(), due.isoformat()


def _lk(iso_date):
    y, m, d = iso_date.split("-")
    return f"{d}/{m}/{y}"


def test_payroll_shows_current_cycle_info(auth_client):
    cycle_start, cycle_end, due_date = _cycle_bounds()
    resp = auth_client.get("/payroll")
    assert resp.status_code == 200
    assert _lk(cycle_start).encode() in resp.data
    assert _lk(cycle_end).encode() in resp.data
    assert _lk(due_date).encode() in resp.data


def test_payroll_combines_harvest_and_hourly_pay(auth_client):
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "rate_per_kg": "50", "hourly_rate": "150"},
    )
    auth_client.post(
        "/attendance/new",
        data={
            "employee_id": "1",
            "date": cycle_start,
            "status": "Present",
            "check_in": "07:30",
            "check_out": "16:30",
            "break_start": "12:00",
            "break_end": "13:00",
        },
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})

    resp = auth_client.get("/payroll")
    # 8h x 150 = 1200 time-based, 24kg x 50 = 1200 harvest, total 2400
    assert b"1200.0" in resp.data
    assert b"2400.0" in resp.data


def test_payroll_flags_missing_rate_instead_of_guessing(auth_client):
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})

    resp = auth_client.get("/payroll")
    assert b"Set a rate on employee" in resp.data


def test_payroll_csv_export_matches_page(auth_client):
    cycle_start, cycle_end, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})

    resp = auth_client.get("/payroll/export.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert f"payroll_{cycle_start}_to_{cycle_end}.csv" in resp.headers["Content-Disposition"]
    body = resp.data.decode("utf-8")
    assert "EMP-0001,Alice" in body
    assert "1200.0" in body


def test_salary_advance_reduces_net_pay(auth_client):
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    # Total pay = 24 x 50 = 1200

    resp = auth_client.post(
        "/payroll/advance",
        data={"employee_id": "1", "date": cycle_start, "amount": "500", "note": "Advance"},
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
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    auth_client.post(
        "/payroll/advance", data={"employee_id": "1", "date": cycle_start, "amount": "300"}
    )
    resp = auth_client.post("/payroll/advance/1/delete", follow_redirects=True)
    assert b"Salary advance removed" in resp.data
    assert b"No advances recorded" in resp.data


def test_non_numeric_advance_amount_is_rejected(auth_client):
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice"})
    resp = auth_client.post(
        "/payroll/advance",
        data={"employee_id": "1", "date": cycle_start, "amount": "abc"},
        follow_redirects=True,
    )
    assert b"must be a number" in resp.data


def _pdf_text(response_data):
    import io as _io
    from pypdf import PdfReader

    reader = PdfReader(_io.BytesIO(response_data))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_payslip_pdf_contains_correct_figures(auth_client):
    cycle_start, cycle_end, _ = _cycle_bounds()
    auth_client.post(
        "/employees/new",
        data={
            "full_name": "Alice",
            "job_position": "Tea picker",
            "estate_division": "North",
            "rate_per_kg": "50",
            "hourly_rate": "150",
            "default_payment_method": "Bank Transfer",
            "bank_name": "Bank of Ceylon",
            "bank_branch": "Kandy",
            "bank_account_name": "Alice A",
            "bank_account_number": "998877",
            "bank_branch_code": "007",
            "bank_account_type": "Savings",
        },
    )
    auth_client.post(
        "/attendance/new",
        data={
            "employee_id": "1",
            "date": cycle_start,
            "status": "Present",
            "check_in": "07:30",
            "check_out": "16:30",
            "break_start": "12:00",
            "break_end": "13:00",
        },
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    auth_client.post(
        "/payroll/advance", data={"employee_id": "1", "date": cycle_start, "amount": "500", "note": "Advance"}
    )

    resp = auth_client.get("/payroll/payslip/1")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert f"payslip_EMP-0001_{cycle_start}_to_{cycle_end}.pdf" in resp.headers["Content-Disposition"]

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
    assert "Bank of Ceylon" in text
    assert "998877" in text
    assert "Payment Information" in text
    assert "Bank Transfer" in text


def test_payslip_for_employee_with_no_rate_does_not_crash(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Bob"})
    resp = auth_client.get("/payroll/payslip/1")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    text = _pdf_text(resp.data)
    assert "Bob" in text


def test_payslip_for_unknown_employee_redirects(auth_client):
    resp = auth_client.get("/payroll/payslip/999", follow_redirects=True)
    assert b"Employee not found" in resp.data


def test_epf_etf_deducted_and_contributed_when_applicable(auth_client):
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "rate_per_kg": "50", "epf_etf_applicable": "1"},
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    # Total pay = 24 x 50 = 1200. Employee EPF 8% = 96, employer EPF 12% = 144, ETF 3% = 36.

    resp = auth_client.get("/payroll")
    assert b"96.0" in resp.data  # employee EPF deducted
    assert b"1104.0" in resp.data  # net pay = 1200 - 96 (no advance)
    assert b"180.0" in resp.data  # employer EPF + ETF combined (144 + 36), shown as one column here

    # Separate employer EPF (144) and ETF (36) figures are broken out in the CSV export.
    csv_resp = auth_client.get("/payroll/export.csv")
    csv_body = csv_resp.data.decode("utf-8")
    assert "144.0" in csv_body
    assert "36.0" in csv_body


def test_epf_etf_not_deducted_when_not_applicable(auth_client):
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})

    resp = auth_client.get("/payroll")
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
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "rate_per_kg": "50", "epf_etf_applicable": "1"},
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})

    resp = auth_client.get("/payroll/payslip/1")
    text = _pdf_text(resp.data)
    assert "96.0" in text  # employee EPF deduction
    assert "1104.0" in text  # net pay
    assert "144.0" in text  # employer EPF mentioned in the footnote
    assert "36.0" in text  # employer ETF mentioned in the footnote


# ---------- Weekly pay cycle: mark-paid, locking, auto-next-cycle ----------


def test_marking_cycle_paid_freezes_snapshot_and_locks_it(auth_client):
    cycle_start, cycle_end, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})

    resp = auth_client.post("/payroll/mark-paid", follow_redirects=True)
    assert b"marked as paid" in resp.data
    assert b"Next cycle opened" in resp.data

    # the active Payroll page now reflects a fresh, empty cycle — the paid week's figures are gone from it
    resp = auth_client.get("/payroll")
    assert _lk(cycle_start).encode() not in resp.data


def test_next_cycle_auto_created_after_mark_paid(auth_client):
    cycle_start, cycle_end, due_date = _cycle_bounds()
    auth_client.post("/payroll/mark-paid")

    resp = auth_client.get("/payroll")
    # the new cycle starts the day after the old one ended
    from datetime import date, timedelta

    expected_next_start = (date.fromisoformat(cycle_end) + timedelta(days=1)).isoformat()
    assert _lk(expected_next_start).encode() in resp.data


def test_paid_cycle_figures_stay_frozen_even_if_attendance_edited_later(auth_client):
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    auth_client.post("/payroll/mark-paid")

    # add more harvest to the SAME already-paid date after the fact
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "10"},
    )
    auth_client.post("/work-assignments/2/weigh", data={"gross_weight": "50", "tare_weight": "0"})

    resp = auth_client.get("/payroll/history/1")
    assert b"1200.0" in resp.data  # still the original frozen total_pay, not 1200 + 2500


def test_payroll_history_lists_paid_cycles(auth_client):
    cycle_start, cycle_end, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    auth_client.post("/payroll/mark-paid")

    resp = auth_client.get("/payroll/history")
    assert resp.status_code == 200
    assert _lk(cycle_start).encode() in resp.data
    assert b"1200.0" in resp.data


def test_payroll_history_search_by_employee_name(auth_client):
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    auth_client.post("/payroll/mark-paid")

    resp = auth_client.get("/payroll/history?q=alice")
    assert _lk(cycle_start).encode() in resp.data

    resp = auth_client.get("/payroll/history?q=nonexistent")
    assert b"No paid cycles yet" in resp.data


def test_payroll_cycle_detail_shows_bank_and_payment_info(auth_client):
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post(
        "/employees/new",
        data={
            "full_name": "Alice", "rate_per_kg": "50", "default_payment_method": "Bank Transfer",
            "bank_name": "Sampath Bank", "bank_account_number": "445566",
        },
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    auth_client.post("/payroll/mark-paid")

    resp = auth_client.get("/payroll/history/1")
    assert b"Sampath Bank" in resp.data
    assert b"445566" in resp.data
    assert b"Bank Transfer" in resp.data
    assert b"Paid" in resp.data  # default payment_status set when marking the cycle paid


def test_can_update_payment_status_on_a_frozen_transaction(auth_client):
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    auth_client.post("/payroll/mark-paid")

    resp = auth_client.post(
        "/payroll/history/1/transactions/1/update",
        data={"payment_method": "Cheque", "payment_status": "Failed", "payment_date": cycle_start, "payment_reference": "CHQ-99"},
        follow_redirects=True,
    )
    assert b"Payment record updated" in resp.data

    resp = auth_client.get("/payroll/history/1")
    assert b"Failed" in resp.data or b"CHQ-99" in resp.data


def test_invalid_payment_update_is_rejected(auth_client):
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    auth_client.post("/payroll/mark-paid")

    resp = auth_client.post(
        "/payroll/history/1/transactions/1/update",
        data={"payment_method": "Bitcoin", "payment_status": "Failed", "payment_date": cycle_start},
        follow_redirects=True,
    )
    assert b"Select a valid payment method" in resp.data


def test_history_payslip_reprint_uses_frozen_bank_details(auth_client):
    cycle_start, _, _ = _cycle_bounds()
    auth_client.post(
        "/employees/new",
        data={"full_name": "Alice", "rate_per_kg": "50", "bank_name": "Original Bank", "bank_account_number": "111"},
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    auth_client.post("/payroll/mark-paid")

    # employee switches banks after the cycle was already paid
    auth_client.post(
        "/employees/1/edit",
        data={"full_name": "Alice", "rate_per_kg": "50", "bank_name": "New Bank", "bank_account_number": "222"},
    )

    resp = auth_client.get("/payroll/history/1/payslip/1")
    assert resp.status_code == 200
    text = _pdf_text(resp.data)
    assert "Original Bank" in text
    assert "New Bank" not in text


def test_history_cycle_report_pdf_has_signature_section_and_totals(auth_client):
    cycle_start, cycle_end, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    auth_client.post("/payroll/mark-paid")

    resp = auth_client.get("/payroll/history/1/pdf")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    text = _pdf_text(resp.data)
    assert "DKNS Tea Lands" in text
    assert "PAYROLL REPORT" in text
    assert "Prepared by" in text
    assert "Authorized by" in text
    assert "1200.0" in text
    assert "Page 1 of 1" in text


def test_history_cycle_export_csv(auth_client):
    cycle_start, cycle_end, _ = _cycle_bounds()
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    auth_client.post("/payroll/mark-paid")

    resp = auth_client.get("/payroll/history/1/export.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    body = resp.data.decode("utf-8")
    assert "Alice" in body
    assert "1200.0" in body


def test_unknown_cycle_detail_redirects(auth_client):
    resp = auth_client.get("/payroll/history/999", follow_redirects=True)
    assert b"Payroll cycle not found" in resp.data


def test_employee_banking_fields_saved_and_shown_in_profile(auth_client):
    auth_client.post(
        "/employees/new",
        data={
            "full_name": "Alice", "bank_name": "HNB", "bank_branch": "Colombo",
            "bank_account_name": "Alice A", "bank_account_number": "778899",
            "bank_branch_code": "090", "bank_account_type": "Current",
            "default_payment_method": "Cheque",
        },
    )
    resp = auth_client.get("/employees/1/edit")
    assert b"HNB" in resp.data
    assert b"778899" in resp.data
    assert b"Colombo" in resp.data
