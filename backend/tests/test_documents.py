import json
from uuid import UUID, uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.document_models import Document, DocumentPage as StoredPage
from app.document_storage import DocumentStorage
from app.main import create_app
from app.migrate_documents import migrate_documents

DOCUMENTS = "/api/documents"


def make_pdf(*texts: str, password: str | None = None) -> bytes:
    with pymupdf.open() as pdf:
        for text in texts:
            page = pdf.new_page()
            if text:
                page.insert_text((50, 50), text)
        if password:
            return pdf.tobytes(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                owner_pw=password,
                user_pw=password,
            )
        return pdf.tobytes()


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, upload_dir=tmp_path / "documents",
                    max_upload_size_mb=1, max_files_per_upload=5,
                    database_url=f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def upload(client, data, filename="report.pdf"):
    return client.post(
        f"{DOCUMENTS}/upload",
        files={"file": (filename, data, "application/pdf")},
    )


def test_upload_valid_pdf(client):
    response = upload(client, make_pdf("Café evidence"), "../reports/report.pdf")
    assert response.status_code == 201
    body = response.json()
    assert body["filename"] == "report.pdf"
    assert body["page_count"] == 1
    assert body["extraction_status"] == "extracted"
    assert body["ocr_required"] is False
    assert body["pages"][0]["text"].strip() == "Café evidence"


def test_multipage_extraction(client):
    response = upload(client, make_pdf("Page A", "Page B", "Page C"))
    assert response.status_code == 201
    body = response.json()
    assert body["page_count"] == 3
    assert [page["page_number"] for page in body["pages"]] == [1, 2, 3]
    assert [page["text"].strip() for page in body["pages"]] == [
        "Page A", "Page B", "Page C"
    ]
    for page in body["pages"]:
        assert page["has_text"] is True
        assert page["character_count"] == len(page["text"])
    assert body["total_characters"] == sum(len(page["text"]) for page in body["pages"])


def test_blank_pdf_requires_ocr(client):
    response = upload(client, make_pdf(""))
    assert response.status_code == 201
    body = response.json()
    assert body["ocr_required"] is True
    assert body["extraction_status"] == "ocr_required"
    assert body["pages"] == [{
        "page_number": 1, "text": "", "character_count": 0, "has_text": False
    }]


@pytest.mark.parametrize(("filename", "data", "status"), [
    ("note.txt", b"plain text", 415),
    ("fake.pdf", b"plain text", 415),
    ("empty.pdf", b"", 422),
    ("corrupt.pdf", b"%PDF-1.7\ninvalid document structure\n%%EOF", 422),
])
def test_rejects_invalid_upload_and_cleans_storage(client, settings, filename, data, status):
    response = upload(client, data, filename)
    assert response.status_code == status
    assert not list(settings.upload_dir.glob("*"))


def test_rejects_repaired_pdf(client, settings):
    pdf = make_pdf("Recoverable page")
    corrupted = pdf.split(b"\nxref\n", 1)[0] + b"\n%%EOF\n"
    assert upload(client, corrupted).status_code == 422
    assert not list(settings.upload_dir.glob("*"))


def test_rejects_truncated_pdf(client, settings):
    data = make_pdf("Truncated").rsplit(b"%%EOF", 1)[0]
    assert upload(client, data).status_code == 422
    assert not list(settings.upload_dir.glob("*"))


def test_rejects_password_protected_pdf(client, settings):
    response = upload(client, make_pdf("Secret", password="test-password"))
    assert response.status_code == 422
    assert "Password-protected" in response.json()["detail"]
    assert not list(settings.upload_dir.glob("*"))


def test_rejects_oversized_file(client, settings):
    data = make_pdf("Small") + b" " * (1024 * 1024)
    assert upload(client, data).status_code == 413
    assert not list(settings.upload_dir.glob("*"))


def test_bounds_multipart_body_without_content_length(client, settings):
    def stream():
        yield b'--limit\r\nContent-Disposition: form-data; name="file"; filename="large.pdf"\r\n\r\n%PDF-1.7\n'
        for _ in range(33):
            yield b"x" * (64 * 1024)
        yield b"\r\n--limit--\r\n"

    response = client.post(
        f"{DOCUMENTS}/upload", content=stream(),
        headers={"Content-Type": "multipart/form-data; boundary=limit"},
    )
    assert response.status_code == 413
    assert not list(settings.upload_dir.glob("*"))


def test_rejects_incomplete_multipart(client, settings):
    response = client.post(
        f"{DOCUMENTS}/upload",
        content=b'--limit\r\nContent-Disposition: form-data; name="file"; filename="test.pdf"\r\n\r\n'
        + make_pdf("Incomplete"),
        headers={"Content-Type": "multipart/form-data; boundary=limit"},
    )
    assert response.status_code == 400
    assert not list(settings.upload_dir.glob("*"))


def test_unknown_document(client):
    base = f"{DOCUMENTS}/{uuid4()}"
    assert client.get(base).status_code == 404
    assert client.get(f"{base}/file").status_code == 404


def test_cannot_use_non_uuid_as_filename(client):
    assert client.get(f"{DOCUMENTS}/metadata.json/file").status_code == 422
    assert client.get(f"{DOCUMENTS}/%2e%2e%2f%2eenv/file").status_code == 404


def test_retrieves_metadata_from_disk_in_new_app(client, settings):
    body = upload(client, make_pdf("Persistent")).json()
    with TestClient(create_app(settings)) as reopened:
        response = reopened.get(f"{DOCUMENTS}/{body['document_id']}")
    assert response.status_code == 200
    assert response.json() == body


def test_retrieves_original_pdf(client):
    data = make_pdf("Original bytes")
    body = upload(client, data).json()
    response = client.get(f"{DOCUMENTS}/{body['document_id']}/file")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="report.pdf"'
    assert response.content == data


def batch(client, *files):
    return client.post(
        f"{DOCUMENTS}/upload-multiple",
        files=[("files", (name, data, "application/pdf")) for name, data in files],
    )


def test_batch_valid_pdfs(client):
    response = batch(client, ("first.pdf", make_pdf("First")), ("last.pdf", make_pdf("Last")))
    assert response.status_code == 200
    first, last = response.json()["results"]
    assert [item["status"] for item in (first, last)] == ["success", "success"]
    assert [item["filename"] for item in (first, last)] == ["first.pdf", "last.pdf"]
    assert first["document"]["document_id"] != last["document"]["document_id"]


def test_batch_keeps_valid_pdfs_when_other_files_fail(client, settings):
    response = batch(client, ("fake.pdf", b"fake"), ("valid.pdf", make_pdf("Good")))
    assert response.status_code == 200
    failed, succeeded = response.json()["results"]
    assert failed["status"] == "error"
    assert failed["filename"] == "fake.pdf"
    assert failed["status_code"] == 415
    assert succeeded["status"] == "success"
    assert client.get(f"{DOCUMENTS}/{succeeded['document']['document_id']}").status_code == 200
    assert len(list(settings.upload_dir.iterdir())) == 1


def test_batch_rejects_more_than_five_before_storing_any(client, settings):
    data = make_pdf("Valid")
    response = batch(client, *((f"{index}.pdf", data) for index in range(6)))
    assert response.status_code == 400
    assert not list(settings.upload_dir.glob("*"))


def test_upload_cors_and_swagger(client):
    response = client.options(f"{DOCUMENTS}/upload", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
    })
    assert response.status_code == 200
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert {"post"} <= paths[f"{DOCUMENTS}/upload"].keys()
    assert {"post"} <= paths[f"{DOCUMENTS}/upload-multiple"].keys()
    assert {"get"} <= paths[DOCUMENTS].keys()
    assert {"get", "delete"} <= paths[f"{DOCUMENTS}/{{document_id}}"].keys()
    assert {"get"} <= paths[f"{DOCUMENTS}/{{document_id}}/file"].keys()
    single = paths[f"{DOCUMENTS}/upload"]["post"]["requestBody"]["content"]
    multiple = paths[f"{DOCUMENTS}/upload-multiple"]["post"]["requestBody"]["content"]
    assert single["multipart/form-data"]["schema"]["properties"]["file"]["format"] == "binary"
    files = multiple["multipart/form-data"]["schema"]["properties"]["files"]
    assert files["items"]["format"] == "binary"
    assert files["maxItems"] == 5

def test_sqlite_persists_document_fields_and_pages(client):
    data = make_pdf("Café", "Second page")
    body = upload(client, data, "report.pdf").json()
    database = client.app.state.database
    with database.sessions() as session:
        document = session.get(Document, UUID(body["document_id"]))
        assert document.original_filename == "report.pdf"
        assert document.stored_filename == f"soul-document-{body['document_id']}/original.pdf"
        assert document.file_size_bytes == len(data)
        assert document.mime_type == "application/pdf"
        assert document.created_at == document.updated_at
        assert [page.page_number for page in document.pages] == [1, 2]
        assert [page.text.strip() for page in document.pages] == ["Café", "Second page"]


def test_listing_pagination_and_filters(client):
    first = upload(client, make_pdf("First"), "first.pdf").json()
    second = upload(client, make_pdf(""), "Scan%_A.pdf").json()

    response = client.get(DOCUMENTS, params={"limit": 1, "offset": 1})
    assert response.status_code == 200
    page = response.json()
    assert (page["total"], page["limit"], page["offset"]) == (2, 1, 1)
    assert page["items"][0]["document_id"] == first["document_id"]
    assert "pages" not in page["items"][0]
    assert client.get(DOCUMENTS, params={"offset": 10}).json()["items"] == []

    filtered = client.get(DOCUMENTS, params={
        "extraction_status": "ocr_required", "search": "scan%_a",
    }).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["document_id"] == second["document_id"]
    assert client.get(DOCUMENTS, params={"search": "%x"}).json()["total"] == 0
    assert client.get(DOCUMENTS, params={"search": "' OR 1=1 --"}).json()["total"] == 0


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": 101}, {"offset": -1}, {"extraction_status": "unknown"},
])
def test_list_validates_query_parameters(client, params):
    assert client.get(DOCUMENTS, params=params).status_code == 422


def test_deletes_files_and_cascades_pages(client, settings):
    body = upload(client, make_pdf("Delete", "Both pages")).json()
    document_id = body["document_id"]
    route = f"{DOCUMENTS}/{document_id}"
    response = client.delete(route)
    assert response.status_code == 204
    assert response.content == b""
    assert client.get(route).status_code == 404
    assert client.get(f"{route}/file").status_code == 404
    assert client.get(DOCUMENTS).json()["total"] == 0
    assert not list(settings.upload_dir.iterdir())
    with client.app.state.database.sessions() as session:
        assert session.get(Document, UUID(document_id)) is None
        assert session.scalar(select(func.count()).select_from(StoredPage)) == 0


def test_delete_unknown_and_invalid_ids(client):
    assert client.delete(f"{DOCUMENTS}/{uuid4()}").status_code == 404
    assert client.delete(f"{DOCUMENTS}/invalid").status_code == 422
    assert client.get(f"{DOCUMENTS}/invalid").status_code == 422


def test_batch_persists_independent_records(client):
    response = batch(
        client, ("one.pdf", make_pdf("One")), ("bad.pdf", b"fake"),
        ("three.pdf", make_pdf("Three")),
    )
    ids = {
        UUID(item["document"]["document_id"])
        for item in response.json()["results"] if item["status"] == "success"
    }
    with client.app.state.database.sessions() as session:
        assert set(session.scalars(select(Document.document_id))) == ids
        assert len(ids) == 2


def test_migration_is_idempotent_and_preserves_source_files(client, settings):
    storage = DocumentStorage(settings.upload_dir)
    document_id = uuid4()
    source = storage.directory(document_id)
    source.mkdir(parents=True)
    pdf = make_pdf("Legacy one", "Legacy two")
    (source / "original.pdf").write_bytes(pdf)
    legacy = {
        "document_id": str(document_id),
        "filename": "legacy.pdf",
        "page_count": 2,
        "total_characters": 20,
        "extraction_status": "extracted",
        "ocr_required": False,
        "pages": [
            {"page_number": 1, "text": "Legacy one", "character_count": 10, "has_text": True},
            {"page_number": 2, "text": "Legacy two", "character_count": 10, "has_text": True},
        ],
    }
    metadata = json.dumps(legacy).encode()
    (source / "metadata.json").write_bytes(metadata)
    missing = storage.directory(uuid4())
    missing.mkdir()
    (missing / "metadata.json").write_bytes(metadata)
    invalid = storage.directory(uuid4())
    invalid.mkdir()
    (invalid / "metadata.json").write_bytes(b"invalid JSON")
    (invalid / "original.pdf").write_bytes(pdf)

    database = client.app.state.database
    first = migrate_documents(database, storage)
    assert (first.imported, first.duplicates, first.missing, first.invalid) == (1, 0, 1, 1)
    second = migrate_documents(database, storage)
    assert (second.imported, second.duplicates, second.missing, second.invalid) == (0, 1, 1, 1)
    assert client.get(f"{DOCUMENTS}/{document_id}").json() == legacy
    assert client.get(f"{DOCUMENTS}/{document_id}/file").content == pdf
    assert (source / "metadata.json").read_bytes() == metadata
    assert (source / "original.pdf").read_bytes() == pdf


def test_upload_db_commit_failure_removes_pdf_and_rolls_back(client, settings):
    database = client.app.state.database

    def fail_commit(_session):
        assert len(list(settings.upload_dir.glob("soul-document-*"))) == 1
        raise SQLAlchemyError("private commit failure")

    event.listen(database.sessions, "before_commit", fail_commit)
    try:
        response = upload(client, make_pdf("Rollback"))
    finally:
        event.remove(database.sessions, "before_commit", fail_commit)

    assert response.status_code == 500
    assert response.json() == {"detail": "Could not store the uploaded PDF."}
    assert not list(settings.upload_dir.iterdir())
    with database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Document)) == 0
        assert session.scalar(select(func.count()).select_from(StoredPage)) == 0


def test_delete_db_commit_failure_restores_document_and_pdf(client, settings):
    data = make_pdf("Restore")
    body = upload(client, data).json()
    route = f"{DOCUMENTS}/{body['document_id']}"
    database = client.app.state.database

    def fail_commit(_session):
        assert len(list(settings.upload_dir.glob(".soul-delete-*"))) == 1
        raise SQLAlchemyError("private delete failure")

    event.listen(database.sessions, "before_commit", fail_commit)
    try:
        response = client.delete(route)
    finally:
        event.remove(database.sessions, "before_commit", fail_commit)

    assert response.status_code == 500
    assert response.json() == {"detail": "Document database operation failed."}
    assert client.get(route).json() == body
    assert client.get(f"{route}/file").content == data
    assert not list(settings.upload_dir.glob(".soul-delete-*"))
