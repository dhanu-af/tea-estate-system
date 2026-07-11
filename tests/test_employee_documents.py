import io
import re


def _upload(client, employee_id=1, document_type="ID Copy", filename="nic.pdf", content=b"%PDF-1.4 fake", note=""):
    return client.post(
        f"/employees/{employee_id}/documents",
        data={
            "document_type": document_type,
            "file": (io.BytesIO(content), filename),
            "note": note,
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_upload_document_shows_in_list(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Kamal Perera"})
    resp = _upload(auth_client, note="Front and back")
    assert b"ID Copy uploaded" in resp.data
    assert b"nic.pdf" in resp.data
    assert b"Front and back" in resp.data


def test_download_returns_original_bytes(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Kamal Perera"})
    content = b"%PDF-1.4 the actual file content"
    resp = _upload(auth_client, content=content)
    m = re.search(rb"/employees/1/documents/(\d+)\"", resp.data)
    doc_id = m.group(1).decode()

    resp = auth_client.get(f"/employees/1/documents/{doc_id}")
    assert resp.status_code == 200
    assert resp.data == content
    assert resp.content_type == "application/pdf"


def test_disallowed_extension_is_rejected(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Kamal Perera"})
    resp = _upload(auth_client, filename="virus.exe", content=b"MZ")
    assert b"Only PDF, JPG, or PNG" in resp.data
    resp = auth_client.get("/employees/1/edit")
    assert b"virus.exe" not in resp.data


def test_missing_document_type_is_rejected(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Kamal Perera"})
    resp = _upload(auth_client, document_type="")
    assert b"Select a valid document type" in resp.data


def test_missing_file_is_rejected(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Kamal Perera"})
    resp = auth_client.post(
        "/employees/1/documents",
        data={"document_type": "ID Copy"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"Choose a file to upload" in resp.data


def test_oversized_file_is_rejected(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Kamal Perera"})
    import app as app_module

    too_big = b"0" * (app_module.EMPLOYEE_DOCUMENT_MAX_BYTES + 1)
    resp = _upload(auth_client, content=too_big)
    assert b"too large" in resp.data


def test_delete_document_removes_it(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Kamal Perera"})
    resp = _upload(auth_client)
    m = re.search(rb"/employees/1/documents/(\d+)/delete", resp.data)
    doc_id = m.group(1).decode()

    resp = auth_client.post(f"/employees/1/documents/{doc_id}/delete", follow_redirects=True)
    assert b"Document removed" in resp.data
    resp = auth_client.get("/employees/1/edit")
    assert b"nic.pdf" not in resp.data


def test_multiple_document_types_can_coexist(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Kamal Perera"})
    _upload(auth_client, document_type="Employment Agreement", filename="agreement.pdf")
    _upload(auth_client, document_type="ID Copy", filename="nic.jpg", content=b"\xff\xd8\xff fake jpeg")
    _upload(auth_client, document_type="Certificate", filename="cert.png", content=b"\x89PNG fake")

    resp = auth_client.get("/employees/1/edit")
    text = resp.get_data(as_text=True)
    assert "agreement.pdf" in text
    assert "nic.jpg" in text
    assert "cert.png" in text


def test_documents_are_deleted_when_employee_is_deleted(auth_client):
    auth_client.post("/employees/new", data={"full_name": "Kamal Perera"})
    resp = _upload(auth_client)
    m = re.search(rb"/employees/1/documents/(\d+)\"", resp.data)
    doc_id = m.group(1).decode()

    auth_client.post("/employees/1/delete", follow_redirects=True)

    resp = auth_client.get("/employees")
    assert b"Kamal Perera" not in resp.data

    # the document row should have cascade-deleted along with the employee
    resp = auth_client.get(f"/employees/1/documents/{doc_id}", follow_redirects=True)
    assert b"Document not found" in resp.data
