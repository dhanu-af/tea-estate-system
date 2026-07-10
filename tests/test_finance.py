def test_factory_crud(auth_client):
    resp = auth_client.post(
        "/finance/factories/new",
        data={"name": "Greenfield Factory", "contact_person": "Mr. Silva", "phone_number": "0771234567",
              "default_price_per_kg": "300"},
        follow_redirects=True,
    )
    assert b"Greenfield Factory added" in resp.data
    assert b"Greenfield Factory" in resp.data

    resp = auth_client.post(
        "/finance/factories/1/edit",
        data={"name": "Greenfield Factory Ltd", "default_price_per_kg": "320"},
        follow_redirects=True,
    )
    assert b"Factory updated" in resp.data
    assert b"Greenfield Factory Ltd" in resp.data


def test_duplicate_factory_name_rejected(auth_client):
    auth_client.post("/finance/factories/new", data={"name": "Greenfield Factory"})
    resp = auth_client.post("/finance/factories/new", data={"name": "Greenfield Factory"}, follow_redirects=True)
    assert b"already exists" in resp.data


def test_factory_name_required(auth_client):
    resp = auth_client.post("/finance/factories/new", data={"name": ""})
    assert b"Factory name is required" in resp.data


def test_delivery_auto_fills_estate_weight_from_harvest(auth_client):
    auth_client.post("/finance/factories/new", data={"name": "Greenfield Factory"})
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})

    resp = auth_client.post(
        "/finance/deliveries/new",
        data={"delivery_date": "2026-07-10", "factory_id": "1", "factory_weight": "23"},
        follow_redirects=True,
    )
    assert b"Delivery recorded" in resp.data
    assert b"24.0" in resp.data  # auto-filled estate weight
    assert b"23.0" in resp.data  # factory weight


def test_delivery_requires_numeric_weight(auth_client):
    auth_client.post("/finance/factories/new", data={"name": "Greenfield Factory"})
    resp = auth_client.post(
        "/finance/deliveries/new",
        data={"delivery_date": "2026-07-10", "factory_id": "1", "factory_weight": "abc"},
    )
    assert b"Weights must be numbers" in resp.data


def test_invoice_creation_from_deliveries_and_payment_flow(auth_client):
    auth_client.post("/finance/factories/new", data={"name": "Greenfield Factory", "default_price_per_kg": "300"})
    auth_client.post(
        "/finance/deliveries/new",
        data={"delivery_date": "2026-07-10", "factory_id": "1", "factory_weight": "50", "estate_weight": "52"},
    )
    auth_client.post(
        "/finance/deliveries/new",
        data={"delivery_date": "2026-07-11", "factory_id": "1", "factory_weight": "30", "estate_weight": "31"},
    )

    resp = auth_client.post(
        "/finance/invoices/new",
        data={
            "factory_id": "1",
            "from": "2026-07-10",
            "to": "2026-07-11",
            "delivery_ids": ["1", "2"],
            "invoice_number": "INV-001",
            "invoice_date": "2026-07-12",
            "price_per_kg": "300",
        },
        follow_redirects=True,
    )
    # total weight 80kg x 300 = 24000
    assert b"24000.0" in resp.data
    assert b"Invoice INV-001 created" in resp.data

    # deliveries should no longer be available to invoice again
    resp = auth_client.get("/finance/invoices/new?factory_id=1&from=2026-07-10&to=2026-07-11")
    assert b"No un-invoiced deliveries" in resp.data

    # record a partial payment
    resp = auth_client.post(
        "/finance/invoices/1/payment",
        data={"payment_date": "2026-07-13", "amount": "10000", "method": "Cash"},
        follow_redirects=True,
    )
    assert b"Payment of 10000.0 recorded" in resp.data
    assert b"Partially Paid" in resp.data

    # pay the rest
    resp = auth_client.post(
        "/finance/invoices/1/payment",
        data={"payment_date": "2026-07-14", "amount": "14000", "method": "Bank Transfer"},
        follow_redirects=True,
    )
    assert b"Paid" in resp.data


def test_duplicate_invoice_number_rejected(auth_client):
    auth_client.post("/finance/factories/new", data={"name": "Greenfield Factory"})
    auth_client.post(
        "/finance/deliveries/new", data={"delivery_date": "2026-07-10", "factory_id": "1", "factory_weight": "50"}
    )
    auth_client.post(
        "/finance/deliveries/new", data={"delivery_date": "2026-07-11", "factory_id": "1", "factory_weight": "30"}
    )
    auth_client.post(
        "/finance/invoices/new",
        data={"factory_id": "1", "from": "2026-07-10", "to": "2026-07-11", "delivery_ids": ["1"],
              "invoice_number": "INV-001", "invoice_date": "2026-07-12", "price_per_kg": "300"},
    )
    resp = auth_client.post(
        "/finance/invoices/new",
        data={"factory_id": "1", "from": "2026-07-10", "to": "2026-07-11", "delivery_ids": ["2"],
              "invoice_number": "INV-001", "invoice_date": "2026-07-12", "price_per_kg": "300"},
        follow_redirects=True,
    )
    assert b"already exists" in resp.data


def test_deleting_invoice_frees_deliveries_for_reinvoicing(auth_client):
    auth_client.post("/finance/factories/new", data={"name": "Greenfield Factory"})
    auth_client.post(
        "/finance/deliveries/new", data={"delivery_date": "2026-07-10", "factory_id": "1", "factory_weight": "50"}
    )
    auth_client.post(
        "/finance/invoices/new",
        data={"factory_id": "1", "from": "2026-07-10", "to": "2026-07-10", "delivery_ids": ["1"],
              "invoice_number": "INV-001", "invoice_date": "2026-07-12", "price_per_kg": "300"},
    )
    resp = auth_client.post("/finance/invoices/1/delete", follow_redirects=True)
    assert b"Invoice removed" in resp.data

    resp = auth_client.get("/finance/invoices/new?factory_id=1&from=2026-07-10&to=2026-07-10")
    assert b"50.0" in resp.data  # delivery is invoiceable again


def test_delivery_already_invoiced_cannot_be_deleted_directly(auth_client):
    auth_client.post("/finance/factories/new", data={"name": "Greenfield Factory"})
    auth_client.post(
        "/finance/deliveries/new", data={"delivery_date": "2026-07-10", "factory_id": "1", "factory_weight": "50"}
    )
    auth_client.post(
        "/finance/invoices/new",
        data={"factory_id": "1", "from": "2026-07-10", "to": "2026-07-10", "delivery_ids": ["1"],
              "invoice_number": "INV-001", "invoice_date": "2026-07-12", "price_per_kg": "300"},
    )
    resp = auth_client.post("/finance/deliveries/1/delete", follow_redirects=True)
    assert b"remove it from the invoice first" in resp.data


def test_finance_dashboard_shows_revenue_cost_and_profit(auth_client):
    auth_client.post("/finance/factories/new", data={"name": "Greenfield Factory"})
    auth_client.post("/employees/new", data={"full_name": "Alice", "rate_per_kg": "50"})
    auth_client.post(
        "/work-assignments/new",
        data={"employee_id": "1", "date": "2026-07-10", "task_type": "Plucking", "harvest_target": "20"},
    )
    auth_client.post("/work-assignments/1/weigh", data={"gross_weight": "24", "tare_weight": "0"})
    # payroll cost = 24 x 50 = 1200

    auth_client.post(
        "/finance/deliveries/new",
        data={"delivery_date": "2026-07-10", "factory_id": "1", "factory_weight": "24"},
    )
    auth_client.post(
        "/finance/invoices/new",
        data={"factory_id": "1", "from": "2026-07-10", "to": "2026-07-10", "delivery_ids": ["1"],
              "invoice_number": "INV-001", "invoice_date": "2026-07-10", "price_per_kg": "300"},
    )
    # revenue = 24 x 300 = 7200, cost = 1200, profit = 6000

    resp = auth_client.get("/finance?view=daily&date=2026-07-10")
    assert b"7200.0" in resp.data
    assert b"1200.0" in resp.data
    assert b"6000.0" in resp.data


def test_finance_dashboard_handles_no_data(auth_client):
    resp = auth_client.get("/finance?view=daily&date=2026-07-10")
    assert resp.status_code == 200
    assert b"No invoices in this date range yet" in resp.data


def _pdf_text(response_data):
    import io as _io
    from pypdf import PdfReader

    reader = PdfReader(_io.BytesIO(response_data))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_invoice_pdf_contains_correct_figures(auth_client):
    auth_client.post(
        "/finance/factories/new",
        data={"name": "Greenfield Factory", "contact_person": "Mr. Silva", "phone_number": "0771234567"},
    )
    auth_client.post(
        "/finance/deliveries/new",
        data={"delivery_date": "2026-07-10", "factory_id": "1", "factory_weight": "50", "estate_weight": "52"},
    )
    auth_client.post(
        "/finance/invoices/new",
        data={"factory_id": "1", "from": "2026-07-10", "to": "2026-07-10", "delivery_ids": ["1"],
              "invoice_number": "INV-001", "invoice_date": "2026-07-12", "price_per_kg": "300"},
    )
    auth_client.post(
        "/finance/invoices/1/payment",
        data={"payment_date": "2026-07-13", "amount": "5000", "method": "Cash"},
    )

    resp = auth_client.get("/finance/invoices/1/pdf")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"

    text = _pdf_text(resp.data)
    assert "DKNS Tea Lands" in text
    assert "INV-001" in text
    assert "Greenfield Factory" in text
    assert "Mr. Silva" in text
    assert "15000.0" in text  # total amount (50 x 300)
    assert "5000.0" in text  # paid so far
    assert "10000.0" in text  # balance due


def test_invoice_pdf_for_unknown_invoice_redirects(auth_client):
    resp = auth_client.get("/finance/invoices/999/pdf", follow_redirects=True)
    assert b"Invoice not found" in resp.data
