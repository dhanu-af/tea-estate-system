def register(client, full_name, **extra):
    data = {"full_name": full_name}
    data.update(extra)
    return client.post("/employees/new", data=data, follow_redirects=True)


def test_register_employee_assigns_sequential_number(auth_client):
    register(auth_client, "Alice")
    resp = register(auth_client, "Bob")
    assert b"EMP-0002 registered successfully" in resp.data


def test_register_employee_requires_full_name(auth_client):
    resp = auth_client.post("/employees/new", data={"full_name": ""})
    assert b"Full name is required" in resp.data


def test_employee_number_never_collides_after_deleting_a_non_last_employee(auth_client):
    # Regression test: next_employee_number used to be derived from COUNT(*),
    # which collides with an existing employee_number once a non-last row is deleted.
    register(auth_client, "Alice")   # EMP-0001
    register(auth_client, "Bob")     # EMP-0002
    auth_client.post("/employees/1/delete", follow_redirects=True)

    resp = register(auth_client, "Carol")
    assert b"EMP-0003 registered successfully" in resp.data

    list_resp = auth_client.get("/employees")
    # Bob's number must still be intact — a collision would have raised a 500 or duplicated the number.
    assert b"EMP-0002" in list_resp.data
    assert b"EMP-0003" in list_resp.data


def test_edit_employee_updates_fields(auth_client):
    register(auth_client, "Alice")
    edit_page = auth_client.get("/employees/1/edit")
    assert b'value="Alice"' in edit_page.data

    resp = auth_client.post(
        "/employees/1/edit",
        data={"full_name": "Alice Updated", "job_position": "Supervisor"},
        follow_redirects=True,
    )
    assert b"Employee updated" in resp.data
    assert b"Alice Updated" in resp.data


def test_stale_edit_is_rejected_instead_of_overwriting(auth_client):
    register(auth_client, "Alice", rate_per_kg="50")

    stale_resp = auth_client.post(
        "/employees/1/edit",
        data={"full_name": "Alice", "rate_per_kg": "999", "expected_updated_at": "2000-01-01 00:00:00"},
        follow_redirects=True,
    )
    assert b"Someone else updated this employee" in stale_resp.data

    edit_page = auth_client.get("/employees/1/edit")
    assert b'value="50.0"' in edit_page.data
    assert b'value="999"' not in edit_page.data
    assert b"None" not in edit_page.data


def test_delete_employee_removes_it(auth_client):
    register(auth_client, "Alice")
    auth_client.post("/employees/1/delete", follow_redirects=True)
    resp = auth_client.get("/employees")
    assert b"Alice" not in resp.data
