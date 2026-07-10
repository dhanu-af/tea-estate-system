from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def _cycle_bounds():
    today = datetime.now(ZoneInfo("Asia/Colombo")).date()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def _seed_full_cycle(auth_client, mark_paid=True):
    """One employee with a harvest + hourly shift, an expense, a salary advance,
    a factory delivery, an invoice for that delivery, and a full payment against
    it, within the current pay cycle. If mark_paid, also processes the cycle so
    its payroll cost becomes a real ledger expense (dated whenever the cycle is
    actually marked paid, which always falls within this same cycle's range).
    Returns (cycle_start, cycle_end)."""
    cycle_start, cycle_end = _cycle_bounds()

    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50", "hourly_rate": "150"})
    auth_client.post(
        "/attendance/new",
        data={
            "employee_id": "1", "date": cycle_start, "status": "Present",
            "check_in": "07:30", "check_out": "16:30", "break_start": "12:00", "break_end": "13:00",
        },
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": cycle_start, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    # harvest pay 24*50=1200, hourly pay 8*150=1200, total = 2400 — only a real
    # ledger expense once the cycle is actually marked paid (see below)

    auth_client.post(
        "/income/expense",
        data={"date": cycle_start, "category": "Transport", "amount": "300", "payment_method": "Cash"},
    )
    auth_client.post(
        "/payroll/advance",
        data={"employee_id": "1", "date": cycle_start, "amount": "500", "payment_method": "Cash"},
    )

    auth_client.post("/finance/factories/new", data={"name": "Greenfield Factory"})
    auth_client.post(
        "/finance/deliveries/new",
        data={"delivery_date": cycle_start, "factory_id": "1", "factory_weight": "24"},
    )
    auth_client.post(
        "/finance/invoices/new",
        data={
            "factory_id": "1", "from": cycle_start, "to": cycle_start, "delivery_ids": ["1"],
            "invoice_number": "INV-001", "invoice_date": cycle_start, "price_per_kg": "200",
        },
    )
    # invoice total = 24*200 = 4800
    auth_client.post(
        "/finance/invoices/1/payment",
        data={"payment_date": cycle_start, "amount": "4800", "method": "Cash"},
    )
    # factory payment credit = 4800; invoice becomes Paid

    if mark_paid:
        auth_client.post("/payroll/mark-paid")

    auth_client.get("/")  # flush the queued flash messages from all the setup posts above
    return cycle_start, cycle_end


def _cycle_query(cycle_start, cycle_end, **extra):
    params = {"view": "custom", "from": cycle_start, "to": cycle_end, **extra}
    return "/finance/statement?" + "&".join(f"{k}={v}" for k, v in params.items())


def test_all_transaction_types_appear_in_statement(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client)
    resp = auth_client.get(_cycle_query(cycle_start, cycle_end))
    body = resp.data.decode()

    assert "Transport" in body  # Expense
    assert "Payroll —" in body  # Payroll (only present because the cycle was marked paid)
    assert "Salary advance" in body  # Salary Advance
    assert "Delivery to Greenfield Factory" in body  # Delivery
    assert "Invoice raised to Greenfield Factory" in body  # Invoice
    assert "Payment received" in body  # Factory Payment
    assert "Tea income" not in body  # accrued harvest income was removed from the ledger


def test_payroll_does_not_appear_until_cycle_is_marked_paid(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client, mark_paid=False)
    resp = auth_client.get(_cycle_query(cycle_start, cycle_end))
    assert "Payroll —" not in resp.data.decode()


def test_statement_totals_and_closing_balance(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client)
    resp = auth_client.get(_cycle_query(cycle_start, cycle_end))
    body = resp.data.decode()

    # credit: factory payment 4800 only (no accrued income); debits: expense 300 + payroll 2400 + advance 500 = 3200
    assert "4800.0" in body
    assert "3200.0" in body
    assert "1600.0" in body  # closing balance = 4800 - 3200


def test_opening_balance_carries_forward_across_cycles(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client)
    # cycle closing balance = 4800 - 3200 = 1600, carried into the next (auto-created) cycle

    next_day = (date.fromisoformat(cycle_end) + timedelta(days=1)).isoformat()
    auth_client.post("/income/expense", data={"date": next_day, "category": "Fuel", "amount": "100"})

    resp = auth_client.get(f"/finance/statement?view=daily&date={next_day}")
    body = resp.data.decode()
    assert "1600.0" in body  # opening balance carried from the prior cycle's close
    assert "1500.0" in body  # closing = 1600 - 100


def test_filter_by_transaction_type(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client)
    resp = auth_client.get(_cycle_query(cycle_start, cycle_end, type="Expense"))
    body = resp.data.decode()
    assert "Transport" in body
    assert "Payroll —" not in body


def test_filter_by_factory(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client)
    auth_client.post("/finance/factories/new", data={"name": "Other Factory"})
    resp = auth_client.get(_cycle_query(cycle_start, cycle_end, factory_id=1))
    assert "Greenfield Factory" in resp.data.decode()


def test_filter_by_employee(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client)
    auth_client.post("/employees/new", data={"full_name": "Bob"})
    auth_client.post("/payroll/advance", data={"employee_id": "2", "date": cycle_start, "amount": "250"})

    resp = auth_client.get(_cycle_query(cycle_start, cycle_end, employee_id=1))
    body = resp.data.decode()
    assert "Payroll —" in body  # Alice's payroll debit
    assert "Salary advance — Bob" not in body  # Bob's advance excluded by the employee filter


def test_filter_by_payment_status_only_shows_matching_invoice_linked_rows(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client)
    resp = auth_client.get(_cycle_query(cycle_start, cycle_end, payment_status="Paid"))
    body = resp.data.decode()
    assert "Invoice raised to Greenfield Factory" in body
    assert "Payment received" in body
    # non-invoice-linked rows (Expense, Payroll, Salary Advance) have no invoice status, so they drop out
    assert "Transport" not in body


def test_search_filters_by_description(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client)
    resp = auth_client.get(_cycle_query(cycle_start, cycle_end, q="transport"))
    body = resp.data.decode()
    assert "Transport" in body
    assert "Payroll —" not in body


def test_sort_by_debit_descending(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client)
    resp = auth_client.get(_cycle_query(cycle_start, cycle_end, sort="debit", dir="desc"))
    body = resp.data.decode()
    payroll_pos = body.find("Payroll —")
    transport_pos = body.find("Transport")
    assert payroll_pos != -1 and transport_pos != -1
    assert payroll_pos < transport_pos  # 2400 debit sorts before 300 debit when descending


def test_created_by_username_shown_in_statement(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client)
    resp = auth_client.get(_cycle_query(cycle_start, cycle_end))
    assert b"admin" in resp.data


def test_statement_csv_export_matches_page(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client)
    resp = auth_client.get(
        f"/finance/statement/export.csv?view=custom&from={cycle_start}&to={cycle_end}"
    )
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    body = resp.data.decode("utf-8")
    assert "Tea income" not in body
    assert "1600.0" in body


def _pdf_text(response_data):
    import io as _io
    from pypdf import PdfReader

    reader = PdfReader(_io.BytesIO(response_data))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_statement_pdf_contains_header_totals_and_signature_section(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client)
    resp = auth_client.get(
        f"/finance/statement/pdf?view=custom&from={cycle_start}&to={cycle_end}&orientation=landscape"
    )
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"

    text = _pdf_text(resp.data)
    assert "DKNS Tea Lands" in text
    assert "FINANCE" in text and "STATEMENT" in text
    assert cycle_start in text
    assert "1600.0" in text  # closing balance
    assert "Prepared by" in text
    assert "Authorized by" in text
    assert "Page 1 of 1" in text


def test_statement_pdf_portrait_orientation(auth_client):
    cycle_start, cycle_end = _seed_full_cycle(auth_client)
    resp = auth_client.get(
        f"/finance/statement/pdf?view=custom&from={cycle_start}&to={cycle_end}&orientation=portrait"
    )
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"


def test_delivery_and_invoice_do_not_affect_balance(auth_client):
    # Deliveries/invoices are informational only — only the actual payment moves money.
    cycle_start, _ = _cycle_bounds()
    auth_client.post("/finance/factories/new", data={"name": "Greenfield Factory"})
    auth_client.post(
        "/finance/deliveries/new",
        data={"delivery_date": cycle_start, "factory_id": "1", "factory_weight": "24"},
    )
    auth_client.post(
        "/finance/invoices/new",
        data={
            "factory_id": "1", "from": cycle_start, "to": cycle_start, "delivery_ids": ["1"],
            "invoice_number": "INV-001", "invoice_date": cycle_start, "price_per_kg": "200",
        },
    )
    resp = auth_client.get(f"/finance/statement?view=daily&date={cycle_start}")
    body = resp.data.decode()
    assert "Delivery to Greenfield Factory" in body
    assert "Invoice raised to Greenfield Factory" in body
    assert "0.0" in body  # closing balance stays at 0 since nothing was actually paid
