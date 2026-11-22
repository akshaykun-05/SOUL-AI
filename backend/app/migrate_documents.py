"""Import Phase 2 JSON metadata while leaving the source files in place."""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from pydantic import ValidationError

from .config import BACKEND_DIR, Settings
from .database import Database
from .document_models import Document
from .document_repository import add_document
from .document_schemas import DocumentResponse
from .document_storage import DocumentStorage
from .pdf_ingestion import display_filename


@dataclass
class MigrationResult:
    imported: int = 0
    duplicates: int = 0
    missing: int = 0
    invalid: int = 0


def migrate_documents(database: Database, storage: DocumentStorage) -> MigrationResult:
    result = MigrationResult()
    for directory in sorted(storage.root.glob("soul-document-*")):
        try:
            document_id = UUID(directory.name.removeprefix("soul-document-"))
        except ValueError:
            result.invalid += 1
            continue
        if directory != storage.directory(document_id) or directory.is_symlink():
            result.invalid += 1
            continue

        with database.sessions.begin() as session:
            session.connection(execution_options={"sqlite_immediate": True})
            if session.get(Document, document_id) is not None:
                result.duplicates += 1
                continue
            try:
                path = storage.pdf_path(document_id)
                with path.open("rb") as pdf:
                    if not pdf.read(5).startswith(b"%PDF-"):
                        result.invalid += 1
                        continue
                stat = path.stat()
                payload = storage.legacy_metadata(document_id).read_bytes()
                document = DocumentResponse.model_validate_json(payload)
            except FileNotFoundError:
                result.missing += 1
                continue
            except (OSError, ValidationError):
                result.invalid += 1
                continue
            if (
                document.document_id != document_id
                or document.page_count < 1
                or document.page_count != len(document.pages)
                or any(page.page_number != number for number, page in enumerate(document.pages, 1))
            ):
                result.invalid += 1
                continue
            document.filename = display_filename(document.filename)
            timestamp = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            add_document(session, storage, document, stat.st_size, timestamp)
        result.imported += 1
    return result


def main() -> None:
    settings = Settings()
    database = Database(settings.database_url)
    try:
        database.initialize()
        result = migrate_documents(database, DocumentStorage(BACKEND_DIR / settings.upload_dir))
    finally:
        database.dispose()
    print(
        f"Imported: {result.imported}, duplicates: {result.duplicates}, "
        f"missing: {result.missing}, invalid: {result.invalid}"
    )


if __name__ == "__main__":
    main()
