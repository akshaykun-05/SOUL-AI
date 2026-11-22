"""PDF text extraction with page citations and optional Tesseract OCR."""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from enum import Enum
from pathlib import Path
from typing import BinaryIO

import pymupdf as fitz
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExtractionStatus(str, Enum):
    complete = "complete"
    low_confidence = "low_confidence"
    empty = "empty"


class ExtractionMethod(str, Enum):
    text = "text"
    ocr = "ocr"


class PdfEvidence(BaseModel):
    """A text span grounded in one PDF page."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    page_number: int = Field(ge=1)
    extracted_text: str = Field(min_length=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=1)
    bounding_box: tuple[float, float, float, float] | None = None
    extraction_method: ExtractionMethod
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def offsets_match_text(self) -> "PdfEvidence":
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        if len(self.extracted_text) != self.end_offset - self.start_offset:
            raise ValueError("offset span must equal extracted_text length")
        if self.bounding_box is not None:
            x0, y0, x1, y1 = self.bounding_box
            if x1 < x0 or y1 < y0:
                raise ValueError("bounding_box coordinates must be ordered")
        return self


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=0)
    status: ExtractionStatus
    evidence: list[PdfEvidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PdfIngestionError(ValueError):
    """Raised when the supplied file cannot be read as a PDF."""


class PdfIngestor:
    """Extract paragraph evidence from PDF bytes, streams, or filesystem paths.

    OCR is opt-in. If enabled, ``pytesseract`` and the Tesseract executable must
    be installed. When either is unavailable the result records a warning and
    remains empty/low confidence; no synthetic text is returned.
    """

    def __init__(
        self,
        *,
        enable_ocr: bool = False,
        min_text_chars_per_page: int = 20,
        min_ocr_confidence: float = 0.35,
    ) -> None:
        if min_text_chars_per_page < 1:
            raise ValueError("min_text_chars_per_page must be positive")
        if not 0.0 <= min_ocr_confidence <= 1.0:
            raise ValueError("min_ocr_confidence must be between 0 and 1")
        self.enable_ocr = enable_ocr
        self.min_text_chars_per_page = min_text_chars_per_page
        self.min_ocr_confidence = min_ocr_confidence

    def ingest(
        self,
        source: bytes | bytearray | BinaryIO | str | Path,
        *,
        document_id: str,
        filename: str | None = None,
    ) -> ExtractionResult:
        if not document_id.strip():
            raise ValueError("document_id must not be empty")
        data, resolved_name = self._read_source(source, filename)
        if not data:
            raise PdfIngestionError("PDF input is empty")
        try:
            document = fitz.open(stream=data, filetype="pdf")
        except (fitz.FileDataError, ValueError) as exc:
            raise PdfIngestionError("Input is not a readable PDF") from exc

        evidence: list[PdfEvidence] = []
        warnings: list[str] = []
        weak_page_count = 0
        try:
            for page_index, page in enumerate(document):
                page_number = page_index + 1
                page_text = page.get_text("text", sort=True)
                if page_text.strip():
                    chunks = self._text_chunks(page, page_number, document_id, resolved_name, page_text)
                    evidence.extend(chunks)
                    if len(page_text.strip()) >= self.min_text_chars_per_page:
                        if chunks:
                            continue
                        weak_page_count += 1
                    else:
                        weak_page_count += 1
                    # Keep embedded text when it is sparse, but also try OCR if requested.
                    if not self.enable_ocr:
                        warnings.append(
                            f"Page {page_number}: little embedded text; extraction may be incomplete."
                        )
                        continue
                else:
                    chunks = []

                if not page_text.strip() and not self.enable_ocr:
                    weak_page_count += 1
                    warnings.append(
                        f"Page {page_number}: little or no embedded text; page may be scanned."
                    )
                    continue

                if self.enable_ocr:
                    try:
                        ocr_text, confidence = self._ocr_page(page)
                    except (ImportError, RuntimeError, OSError) as exc:
                        warnings.append(f"Page {page_number}: OCR unavailable ({exc}).")
                        continue
                    if ocr_text.strip():
                        if confidence is None or confidence < self.min_ocr_confidence:
                            weak_page_count += 1
                        evidence.extend(
                            self._ocr_chunks(
                                ocr_text,
                                confidence,
                                page_number,
                                document_id,
                                resolved_name,
                                page.rect,
                            )
                        )
                        if confidence is not None and confidence < self.min_ocr_confidence:
                            warnings.append(
                                f"Page {page_number}: OCR confidence {confidence:.2f} is below threshold."
                            )
                    else:
                        weak_page_count += 1
                        warnings.append(f"Page {page_number}: OCR returned no text.")
        finally:
            document.close()

        if not evidence:
            status = ExtractionStatus.empty
        elif weak_page_count or any(
            item.confidence is not None and item.confidence < self.min_ocr_confidence
            for item in evidence
        ):
            status = ExtractionStatus.low_confidence
        else:
            status = ExtractionStatus.complete
        return ExtractionResult(
            document_id=document_id,
            filename=resolved_name,
            sha256=hashlib.sha256(data).hexdigest(),
            page_count=page_index + 1 if "page_index" in locals() else 0,
            status=status,
            evidence=evidence,
            warnings=warnings,
        )

    @staticmethod
    def _read_source(
        source: bytes | bytearray | BinaryIO | str | Path, filename: str | None
    ) -> tuple[bytes, str]:
        if isinstance(source, (str, Path)):
            path = Path(source)
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise PdfIngestionError(f"Could not read PDF: {path.name}") from exc
            return data, filename or path.name
        if isinstance(source, (bytes, bytearray)):
            return bytes(source), filename or "upload.pdf"
        try:
            data = source.read()
        except (AttributeError, OSError) as exc:
            raise PdfIngestionError("Source must be PDF bytes, a binary stream, or a path") from exc
        if not isinstance(data, bytes):
            raise PdfIngestionError("PDF stream must be opened in binary mode")
        return data, filename or Path(getattr(source, "name", "upload.pdf")).name

    @staticmethod
    def _text_chunks(
        page: fitz.Page,
        page_number: int,
        document_id: str,
        filename: str,
        page_text: str,
    ) -> list[PdfEvidence]:
        chunks: list[PdfEvidence] = []
        # Match paragraph spans against the exact page text used for offsets.
        cursor = 0
        for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", page_text, re.S):
            text = match.group().strip()
            if not text:
                continue
            start = page_text.find(text, cursor)
            if start < 0:
                continue
            end = start + len(text)
            cursor = end
            box = PdfIngestor._find_bbox(page, text)
            chunks.append(
                PdfEvidence(
                    evidence_id=str(uuid.uuid4()),
                    document_id=document_id,
                    filename=filename,
                    page_number=page_number,
                    extracted_text=text,
                    start_offset=start,
                    end_offset=end,
                    bounding_box=box,
                    extraction_method=ExtractionMethod.text,
                )
            )
        return chunks

    @staticmethod
    def _find_bbox(page: fitz.Page, text: str) -> tuple[float, float, float, float] | None:
        for block in page.get_text("dict", sort=True).get("blocks", []):
            if block.get("type") != 0:
                continue
            block_text = "\n".join(
                "".join(span.get("text", "") for span in line.get("spans", []))
                for line in block.get("lines", [])
            ).strip()
            if text in block_text or block_text in text:
                return tuple(float(value) for value in block["bbox"])
        return None

    @staticmethod
    def _ocr_page(page: fitz.Page) -> tuple[str, float | None]:
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:
            raise ImportError("install the optional OCR dependencies: pip install '.[ocr]'") from exc
        image = Image.open(io.BytesIO(page.get_pixmap(dpi=200).tobytes("png")))
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        rows: dict[tuple[int, int, int], list[str]] = {}
        confidences: list[float] = []
        for i, word in enumerate(data["text"]):
            word = word.strip()
            if not word:
                continue
            rows.setdefault(
                (data["block_num"][i], data["par_num"][i], data["line_num"][i]), []
            ).append(word)
            try:
                value = float(data["conf"][i])
                if value >= 0:
                    confidences.append(value / 100.0)
            except (TypeError, ValueError):
                pass
        text = "\n".join(" ".join(words) for words in rows.values())
        confidence = sum(confidences) / len(confidences) if confidences else None
        return text, confidence

    @staticmethod
    def _ocr_chunks(
        text: str,
        confidence: float | None,
        page_number: int,
        document_id: str,
        filename: str,
        rect: fitz.Rect,
    ) -> list[PdfEvidence]:
        normalized = text.strip()
        x0, y0, x1, y1 = (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
        chunks: list[PdfEvidence] = []
        cursor = 0
        for part in re.split(r"\n\s*\n", normalized):
            chunk = part.strip()
            if not chunk:
                continue
            start = normalized.find(chunk, cursor)
            end = start + len(chunk)
            cursor = end
            chunks.append(
                PdfEvidence(
                    evidence_id=str(uuid.uuid4()),
                    document_id=document_id,
                    filename=filename,
                    page_number=page_number,
                    extracted_text=chunk,
                    start_offset=start,
                    end_offset=end,
                    bounding_box=(x0, y0, x1, y1),
                    extraction_method=ExtractionMethod.ocr,
                    confidence=confidence,
                )
            )
        return chunks
