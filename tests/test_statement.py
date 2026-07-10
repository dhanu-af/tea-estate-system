def _seed_full_day(auth_client, date="2026-07-10"):
    """One employee with a harvest + hourly shift, an expense, a salary advance,
    a factory delivery, an invoice for that delivery, and a full payment against
    it — enough to exercise every ledger transaction type on a single day."""
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50", "hourly_rate": "150"})
    auth_client.post(
        "/attendance/new",
        data={
            "employee_id": "1", "date": date, "status": "Present",
            "check_in": "07:30", "check_out": "16:30", "break_start": "12:00", "break_end": "13:00",
        },
    )
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": date, "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    # harvest pay 24*50=1200, hourly pay 8*150=1200, total payroll debit = 2400

    auth_client.post("/income/price", data={"date": date, "price_per_kg": "200"})
    # income credit = 24*200 = 4800

    auth_client.post(
        "/income/expense",
        data={"date": date, "category": "Transport", "amount": "300", "payment_method": "Cash"},
    )
    auth_client.post(
        "/payroll/advance",
        data={"employee_id": "1", "date": date, "amount": "500", "payment_method": "Cash"},
    )

    auth_client.post("/finance/factories/new", data={"name": "Greenfield Factory"})
    auth_client.post(
        "/finance/deliveries/new",
        data={"delivery_date": date, "factory_id": "1", "factory_weight": "24"},
    )
    auth_client.post(
        "/finance/invoices/new",
        data={
            "factory_id": "1", "from": date, "to": date, "delivery_ids": ["1"],
            "invoice_number": "INV-001", "invoice_date": date, "price_per_kg": "200",
        },
    )
    # invoice total = 24*200 = 4800
    auth_client.post(
        "/finance/invoices/1/payment",
        data={"payment_date": date, "amount": "4800", "method": "Cash"},
    )
    # factory payment credit = 4800; invoice becomes Paid

    auth_client.get("/")  # flush the queued flash messages from all the setup posts above


def test_all_transaction_types_appear_in_statement(auth_client):
    _seed_full_day(auth_client)
    resp = auth_client.get("/finance/statement?view=daily&date=2026-07-10")
    body = resp.data.decode()

    assert "Tea income" in body  # Income
    assert "Transport" in body  # Expense
    assert "Payroll" in body  # Payroll
    assert "Salary advance" in body  # Salary Advance
    assert "Delivery to Greenfield Factory" in body  # Delivery
    assert "Invoice raised to Greenfield Factory" in body  # Invoice
    assert "Payment received" in body  # Factory Payment


def test_statement_totals_and_closing_balance(auth_client):
    _seed_full_day(auth_client)
    resp = auth_client.get("/finance/statement?view=daily&date=2026-07-10")
    body = resp.data.decode()

    # credits: income 4800 + payment 4800 = 9600; debits: expense 300 + payroll 2400 + advance 500 = 3200
    assert "9600.0" in body
    assert "3200.0" in body
    assert "6400.0" in body  # closing balance = 9600 - 3200


def test_opening_balance_carries_forward_from_earlier_day(auth_client):
    _seed_full_day(auth_client, date="2026-07-10")
    # Day 2: just one small expense, viewed alone
    auth_client.post("/income/expense", data={"date": "2026-07-11", "category": "Fuel", "amount": "100"})

    resp = auth_client.get("/finance/statement?view=daily&date=2026-07-11")
    body = resp.data.decode()
    # opening balance should be day 1's closing balance (6400.0), closing = 6400 - 100 = 6300
    assert "6400.0" in body
    assert "6300.0" in body


def test_filter_by_transaction_type(auth_client):
    _seed_full_day(auth_client)
    resp = auth_client.get("/finance/statement?view=daily&date=2026-07-10&type=Expense")
    body = resp.data.decode()
    assert "Transport" in body
    assert "Payroll —" not in body  # em-dash distinguishes an actual row from the type-filter dropdown option
    assert "Tea income" not in body


def test_filter_by_factory(auth_client):
    _seed_full_day(auth_client)
    auth_client.post("/finance/factories/new", data={"name": "Other Factory"})
    resp = auth_client.get("/finance/statement?view=daily&date=2026-07-10&factory_id=1")
    body = resp.data.decode()
    assert "Greenfield Factory" in body


def test_filter_by_employee(auth_client):
    _seed_full_day(auth_client)
    auth_client.post("/employees/new", data={"full_name": "Bob"})
    auth_client.post("/payroll/advance", data={"employee_id": "2", "date": "2026-07-10", "amount": "250"})

    resp = auth_client.get("/finance/statement?view=daily&date=2026-07-10&employee_id=1")
    body = resp.data.decode()
    assert "Payroll —" in body  # Alice's payroll debit
    assert "Salary advance — Bob" not in body  # Bob's advance excluded by the employee filter


def test_filter_by_payment_status_only_shows_matching_invoice_linked_rows(auth_client):
    _seed_full_day(auth_client)
    resp = auth_client.get("/finance/statement?view=daily&date=2026-07-10&payment_status=Paid")
    body = resp.data.decode()
    assert "Invoice raised to Greenfield Factory" in body
    assert "Payment received" in body
    # non-invoice-linked rows (Income, Expense, Payroll, Salary Advance) have no status, so they drop out
    assert "Transport" not in body


def test_search_filters_by_description(auth_client):
    _seed_full_day(auth_client)
    resp = auth_client.get("/finance/statement?view=daily&date=2026-07-10&q=transport")
    body = resp.data.decode()
    assert "Transport" in body
    assert "Payroll —" not in body


def test_sort_by_debit_descending(auth_client):
    _seed_full_day(auth_client)
    resp = auth_client.get("/finance/statement?view=daily&date=2026-07-10&sort=debit&dir=desc")
    body = resp.data.decode()
    payroll_pos = body.find("Payroll —")
    transport_pos = body.find("Transport")
    assert payroll_pos != -1 and transport_pos != -1
    assert payroll_pos < transport_pos  # 2400 debit sorts before 300 debit when descending


def test_created_by_username_shown_in_statement(auth_client):
    _seed_full_day(auth_client)
    resp = auth_client.get("/finance/statement?view=daily&date=2026-07-10")
    assert b"admin" in resp.data


def test_statement_csv_export_matches_page(auth_client):
    _seed_full_day(auth_client)
    resp = auth_client.get("/finance/statement/export.csv?view=daily&date=2026-07-10")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    body = resp.data.decode("utf-8")
    assert "Tea income" in body
    assert "6400.0" in body


def _pdf_text(response_data):
    import io as _io
    from pypdf import PdfReader

    reader = PdfReader(_io.BytesIO(response_data))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_statement_pdf_contains_header_totals_and_signature_section(auth_client):
    _seed_full_day(auth_client)
    resp = auth_client.get("/finance/statement/pdf?view=daily&date=2026-07-10&orientation=landscape")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"

    text = _pdf_text(resp.data)
    assert "DKNS Tea Lands" in text
    assert "FINANCE" in text and "STATEMENT" in text
    assert "2026-07-10" in text
    assert "6400.0" in text  # closing balance
    assert "Prepared by" in text
    assert "Authorized by" in text
    assert "Page 1 of 1" in text


def test_statement_pdf_portrait_orientation(auth_client):
    _seed_full_day(auth_client)
    resp = auth_client.get("/finance/statement/pdf?view=daily&date=2026-07-10&orientation=portrait")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"


def test_delivery_and_invoice_do_not_affect_balance(auth_client):
    # Deliveries/invoices are informational only — only the actual payment moves money.
    auth_client.post("/finance/factories/new", data={"name": "Greenfield Factory"})
    auth_client.post(
        "/finance/deliveries/new",
        data={"delivery_date": "2026-07-10", "factory_id": "1", "factory_weight": "24"},
    )
    auth_client.post(
        "/finance/invoices/new",
        data={
            "factory_id": "1", "from": "2026-07-10", "to": "2026-07-10", "delivery_ids": ["1"],
            "invoice_number": "INV-001", "invoice_date": "2026-07-10", "price_per_kg": "200",
        },
    )
    resp = auth_client.get("/finance/statement?view=daily&date=2026-07-10")
    body = resp.data.decode()
    assert "Delivery to Greenfield Factory" in body
    assert "Invoice raised to Greenfield Factory" in body
    assert "0.0" in body  # closing balance stays at 0 since nothing was actually paid
