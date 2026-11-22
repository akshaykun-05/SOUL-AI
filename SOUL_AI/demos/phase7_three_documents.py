"""Reproducible Phase 7 integration run over three generated synthetic PDFs."""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rule_based_text_provider import RuleBasedTextProvider
from soul_ai.integration import PdfUpload, SOULAIEngineService


def make_pdf(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


async def main() -> None:
    provider = RuleBasedTextProvider()
    service = SOULAIEngineService(claim_provider=provider, verifier_provider=RuleBasedTextProvider())
    facts = [
        ("agreement_original.pdf", "Acme Ltd loan amount is INR 600,000."),
        ("bank_letter.pdf", "Acme Ltd loan amount is INR 550,000."),
        ("agreement_copy.pdf", "Acme Ltd loan amount is INR 600,000."),
    ]
    uploads = [PdfUpload(document_id=f"doc-{i}", filename=name,
                         content_base64=base64.b64encode(make_pdf(text)).decode("ascii"))
               for i, (name, text) in enumerate(facts, start=1)]
    extracted = service.extract_documents("demo-extract", uploads)
    if extracted.status.value != "success":
        raise RuntimeError(extracted.model_dump_json())
    result = await service.answer("demo-answer", "What is Acme Ltd loan amount?")
    if result.status.value != "success" or result.data.get("status") != "conflict":
        raise RuntimeError("Expected the pipeline to detect planted source conflict: " + result.model_dump_json())
    report = {"extraction": extracted.model_dump(mode="json"), "answer": result.model_dump(mode="json")}
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
