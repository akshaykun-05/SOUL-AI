import asyncio
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from starlette.concurrency import run_in_threadpool

from .ai_adapter import Engine, EngineUnavailable, extract_and_verify, page_evidence
from .ai_schemas import EvidenceReference
from .ai_service import cache_key
from .contradiction_schemas import ComparisonResult, ContradictionSide, VerifiedContradiction
from .database import Database
from .document_models import (
    ContradictionAnalysis, ContradictionSource, VerifiedInputCache, utc_now,
)
from .document_repository import get_documents
from .document_schemas import DocumentResponse

INPUT_REVISION = "comparison-input-v1"
COMPARISON_TIMEOUT = 120
REVIEW_TYPES = {"version_change", "temporal_change", "condition_conflict"}


class SourcesChanged(Exception):
    pass


def load_sources(database: Database, ids: list[UUID]) -> list[DocumentResponse]:
    with database.sessions() as session:
        return get_documents(session, ids)


def source_identity(document: DocumentResponse) -> str:
    return hashlib.sha256(document.model_dump_json().encode()).hexdigest()


def persist_analysis(
    database: Database, result: ComparisonResult, identities: dict[UUID, str],
    pending_cache: list[dict],
) -> None:
    with database.sessions.begin() as session:
        session.connection(execution_options={"sqlite_immediate": True})
        try:
            documents = get_documents(session, result.document_ids)
        except FileNotFoundError as exc:
            raise SourcesChanged from exc
        if any(source_identity(doc) != identities[doc.document_id] for doc in documents):
            raise SourcesChanged
        session.add(ContradictionAnalysis(
            analysis_id=result.analysis_id, payload=result.model_dump_json(),
        ))
        session.flush()
        session.add_all([
            ContradictionSource(analysis_id=result.analysis_id, document_id=doc_id, fingerprint=fingerprint)
            for doc_id, fingerprint in identities.items()
        ])
        for values in pending_cache:
            statement = insert(VerifiedInputCache).values(**values)
            session.execute(statement.on_conflict_do_update(
                index_elements=[VerifiedInputCache.document_id],
                set_={"cache_key": statement.excluded.cache_key, "payload": statement.excluded.payload},
            ))


def retrieve_analysis(database: Database, analysis_id: UUID) -> ComparisonResult:
    with database.sessions() as session:
        record = session.get(ContradictionAnalysis, analysis_id)
        if record is None:
            raise FileNotFoundError
        sources = dict(session.execute(select(
            ContradictionSource.document_id, ContradictionSource.fingerprint,
        ).where(ContradictionSource.analysis_id == analysis_id)).all())
        result = ComparisonResult.model_validate_json(record.payload)
        if set(sources) != set(result.document_ids):
            raise FileNotFoundError
        documents = get_documents(session, result.document_ids)
        if any(source_identity(doc) != sources[doc.document_id] for doc in documents):
            raise FileNotFoundError
        return result


def read_inputs(database: Database, keys: dict[UUID, str]) -> dict[UUID, str]:
    if not keys:
        return {}
    with database.sessions() as session:
        rows = session.scalars(select(VerifiedInputCache).where(VerifiedInputCache.document_id.in_(keys)))
        return {row.document_id: row.payload for row in rows if row.cache_key == keys[row.document_id]}


def parse_cached(payload: str, engine: Engine) -> tuple[dict, list] | None:
    try:
        data = json.loads(payload)
        claims = [engine.claim_type.model_validate(claim) for claim in data["claims"]]
        reports = [engine.verification_type.model_validate(report) for report in data["reports"]]
        return {claim.claim_id: claim for claim in claims}, reports
    except (ValueError, TypeError, KeyError):
        return None


def verified_inputs(candidates, reports, evidence):
    """Return usable reports and their exact, document-scoped source references."""
    pages = {page.page_number: page for page in evidence}
    claims = []
    verified = []
    sources = []
    for report in reports:
        claim = candidates[report.claim_id]
        if report.status != "SUPPORTED" or report.requires_human_review or not report.evidence:
            continue
        if not all(
            (page := pages.get(quote.page_number)) is not None
            and quote.filename == page.filename
            and quote.text.strip()
            and quote.text in page.extracted_text
            for quote in report.evidence
        ):
            continue
        claims.append(claim)
        verified.append(report)
        for quote in report.evidence:
            page = pages[quote.page_number]
            reference = EvidenceReference(
                evidence_id=page.evidence_id, document_id=page.document_id,
                filename=page.filename, page_number=page.page_number, text=quote.text,
            )
            sources.append((reference, claim))
    return claims, verified, sources


def finding_results(findings, sources) -> tuple[list[VerifiedContradiction], int]:
    by_source = defaultdict(list)
    for reference, claim in sources:
        key = (str(reference.document_id), reference.filename, reference.page_number, reference.text)
        value = " ".join(part.strip() for part in (claim.value, claim.unit) if part and part.strip())
        by_source[(*key, value, claim.temporal_context)].append((reference, claim))

    def side(source, value):
        key = (str(source.document_id), source.filename, source.page_number, source.snippet)
        matches = by_source.get((*key, value, source.temporal_context), ())
        if len(matches) == 1:
            reference, claim = matches[0]
            return ContradictionSide(
                claim_id=claim.claim_id, text=claim.claim_text, value=value, evidence=reference,
            )

    results = {}
    excluded = 0
    for finding in findings:
        try:
            if (
                finding.requires_human_review is not False
                or finding.contradiction_type in REVIEW_TYPES
                or " ".join((finding.source_1.temporal_context or "").casefold().split())
                != " ".join((finding.source_2.temporal_context or "").casefold().split())
            ):
                excluded += 1
                continue
            first = side(finding.source_1, finding.value_1)
            second = side(finding.source_2, finding.value_2)
            if first is None or second is None or first.evidence.document_id == second.evidence.document_id:
                excluded += 1
                continue
            ordered = sorted((first, second), key=lambda item: str(item.evidence.document_id))
            key = (
                finding.contradiction_type, finding.entity.casefold(), finding.attribute.casefold(),
                *((str(item.evidence.document_id), item.value) for item in ordered),
            )
            result = VerifiedContradiction(
                id=finding.contradiction_id, claim_a=first, claim_b=second,
                contradiction_type=finding.contradiction_type, explanation=finding.explanation,
            )
            results.setdefault(key, result)
        except (AttributeError, TypeError, ValidationError):
            excluded += 1
    return [results[key] for key in sorted(results)], excluded


async def compare(
    database: Database, documents: list[DocumentResponse],
    engine: Engine | None, cache_revision: str, created_at: datetime,
) -> ComparisonResult:
    readable = [doc for doc in documents if not doc.ocr_required and any(p.text.strip() for p in doc.pages)]
    skipped = [doc.document_id for doc in documents if doc not in readable]
    warnings = []
    if skipped:
        warnings.append("Documents with no readable text require OCR and were skipped.")
    findings = []
    pending_cache = []
    if len(readable) < 2:
        warnings.append("At least two readable documents are required for comparison.")
    else:
        if engine is None or engine.detector is None:
            raise EngineUnavailable("The contradiction detector is unavailable.")
        findings, pending_cache = await asyncio.wait_for(
            detect(database, readable, engine, cache_revision, warnings),
            timeout=COMPARISON_TIMEOUT,
        )
    result = ComparisonResult(
        analysis_id=uuid4(), status="completed" if len(readable) >= 2 else "insufficient_readable_documents",
        document_ids=[doc.document_id for doc in documents],
        documents_analyzed=len(readable) if len(readable) >= 2 else 0,
        ocr_required_document_ids=skipped, contradiction_count=len(findings),
        contradictions=findings, warnings=warnings, created_at=created_at, completed_at=utc_now(),
    )
    await run_in_threadpool(
        persist_analysis, database, result,
        {doc.document_id: source_identity(doc) for doc in documents}, pending_cache,
    )
    return result


async def detect(database, documents, engine, cache_revision, warnings):
    keys = {
        doc.document_id: cache_key(doc, engine, f"{INPUT_REVISION}:{cache_revision}")
        for doc in documents
    } if cache_revision and engine.claim_type and engine.verification_type else {}
    cached = await run_in_threadpool(read_inputs, database, keys)
    all_claims, all_reports, all_evidence, sources, pending = [], [], [], [], []
    for document in documents:
        doc_id = document.document_id
        evidence = page_evidence(document, engine)
        if len(evidence) < document.page_count and "Pages without text were skipped." not in warnings:
            warnings.append("Pages without text were skipped.")
        cached_inputs = parse_cached(cached[doc_id], engine) if doc_id in cached else None
        candidates, reports = cached_inputs or await extract_and_verify(evidence, engine)
        claims, verified, provenance = verified_inputs(candidates, reports, evidence)
        if len(claims) < len(candidates) and "Some claims lacked usable independent verification." not in warnings:
            warnings.append("Some claims lacked usable independent verification.")
        if not candidates:
            warnings.append(f"No claim candidates were available for document {doc_id}.")
        all_claims.extend(claims)
        all_reports.extend(verified)
        all_evidence.extend(evidence)
        sources.extend(provenance)
        if doc_id in keys and not cached_inputs and claims and len(claims) == len(candidates):
            pending.append({
                "document_id": doc_id, "cache_key": keys[doc_id], "payload": json.dumps({
                    "claims": [claim.model_dump(mode="json") for claim in claims],
                    "reports": [report.model_dump(mode="json") for report in verified],
                }),
            })
    raw = await run_in_threadpool(engine.detector.detect, all_claims, all_reports, all_evidence)
    results, excluded = finding_results(raw, sources)
    if excluded:
        warnings.append(f"{excluded} detector finding(s) were excluded for review or insufficient provenance.")
    return results, pending
