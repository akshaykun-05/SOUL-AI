import shutil
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID


class DocumentStorage:
    PDF_NAME = "original.pdf"

    def __init__(self, root: Path):
        self.root = root.resolve()

    @contextmanager
    def stage(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".soul-upload-", dir=self.root) as staging:
            yield Path(staging)

    def stored_filename(self, document_id: UUID) -> str:
        return (self.directory(document_id) / self.PDF_NAME).relative_to(self.root).as_posix()

    def directory(self, document_id: UUID) -> Path:
        return self.root / f"soul-document-{document_id}"

    def publish(self, staging: Path, document_id: UUID) -> Path:
        directory = self.directory(document_id)
        if directory.exists() or directory.is_symlink():
            raise FileExistsError(directory)
        return staging.rename(directory)

    def pdf_path(self, document_id: UUID) -> Path:
        return self._stored_file(document_id, self.PDF_NAME)

    def legacy_metadata(self, document_id: UUID) -> Path:
        return self._stored_file(document_id, "metadata.json")

    def quarantine(self, document_id: UUID) -> Path | None:
        directory = self.directory(document_id)
        if directory.is_symlink() or directory.resolve() != directory:
            raise OSError("Unsafe document directory.")
        pending = self.root / f".soul-delete-{document_id}"
        if pending.exists() or pending.is_symlink():
            raise FileExistsError(pending)
        if not directory.exists():
            return None
        if not directory.is_dir():
            raise OSError("Invalid document directory.")
        return directory.rename(pending)

    def restore(self, pending: Path, document_id: UUID) -> None:
        pending.rename(self.directory(document_id))

    def purge(self, directory: Path) -> None:
        shutil.rmtree(directory)

    def _stored_file(self, document_id: UUID, name: str) -> Path:
        path = self.directory(document_id) / name
        if (
            path.parent.is_symlink()
            or path.is_symlink()
            or path.resolve() != path
            or not path.is_file()
        ):
            raise FileNotFoundError(path)
        return path
