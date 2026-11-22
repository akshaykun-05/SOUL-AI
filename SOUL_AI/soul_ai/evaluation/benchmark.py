"""Score ZeroGuessingEngine outputs against separately stored gold labels."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timezone
from typing import Sequence

from soul_ai.contradictions import ContradictionDetector
from soul_ai.response_engine import ZeroGuessingResponse
from soul_ai.schemas import AtomicClaim


REQUIRED_CATEGORY_COUNTS = {
    "unsupported_absent": 20,
    "numerical_contradiction": 5,
    "date_contradiction": 5,
    "identity_contradiction": 5,
    "consistent_fact": 5,
}


def validate_dataset(inputs: dict, labels: dict) -> None:
    if inputs.get("dataset_version") != labels.get("dataset_version"):
        raise ValueError("input and label dataset versions do not match")
    input_cases = inputs.get("cases")
    label_cases = labels.get("cases")
    if not isinstance(input_cases, list) or not isinstance(label_cases, list):
        raise ValueError("both dataset files must contain a cases list")
    input_ids = [item.get("case_id") for item in input_cases]
    label_ids = [item.get("case_id") for item in label_cases]
    if len(input_ids) != len(set(input_ids)) or len(label_ids) != len(set(label_ids)):
        raise ValueError("case IDs must be unique")
    if set(input_ids) != set(label_ids):
        raise ValueError("input cases and gold labels must have identical case IDs")
    categories = Counter(item.get("category") for item in label_cases)
    if dict(categories) != REQUIRED_CATEGORY_COUNTS:
        raise ValueError(f"unexpected benchmark category counts: {dict(categories)}")


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalized_text(text: str | None) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in (text or "")).split())


def _fact_claim(fact: dict) -> AtomicClaim:
    return AtomicClaim(
        claim_id="gold",
        claim_text="gold fact",
        entity=fact.get("entity"),
        attribute=fact.get("attribute"),
        value=fact.get("value"),
        unit=fact.get("unit"),
        temporal_context=fact.get("temporal_context"),
    )


def _same_key(claim: AtomicClaim, fact: dict) -> bool:
    return ContradictionDetector._compatible(claim, _fact_claim(fact))


def _same_value(claim: AtomicClaim, fact: dict) -> bool:
    if not _same_key(claim, fact):
        return False
    comparison = ContradictionDetector._compare_values(claim, _fact_claim(fact))
    return comparison is not None and comparison[0] == "equal"


def _expected_verdict(observation: dict, label: dict) -> str | None:
    claim = observation["claim"]
    facts = label.get("expected_facts", [])
    category = label["category"]
    matching_key = [fact for fact in facts if _same_key(claim, fact)]
    if not matching_key:
        target = label.get("unsupported_target")
        if category == "unsupported_absent" and target:
            same_entity = _normalized_text(claim.entity) == _normalized_text(target.get("entity"))
            same_attribute = (
                ContradictionDetector._canonical_attribute(claim.attribute or "")
                == ContradictionDetector._canonical_attribute(target.get("attribute", ""))
            )
            return "UNSUPPORTED" if same_entity and same_attribute else None
        return None
    if observation["scope"] == "document":
        scoped = [fact for fact in matching_key if fact["document_id"] in observation.get("document_ids", [])]
        if not scoped:
            return "UNSUPPORTED"
        return "SUPPORTED" if any(_same_value(claim, fact) for fact in scoped) else "CONTRADICTED"
    if category.endswith("_contradiction"):
        return "CONTRADICTED"
    return "SUPPORTED" if any(_same_value(claim, fact) for fact in matching_key) else "CONTRADICTED"


def _trace_rows(response: ZeroGuessingResponse, label: dict) -> list[dict]:
    rows = []
    for entry in response.evaluation_trace:
        expected = _expected_verdict(
            {"claim": entry.claim, "scope": entry.scope, "document_ids": entry.document_ids},
            label,
        )
        if expected is None:
            continue
        predicted = entry.verification.status.value
        rows.append({
            "scope": entry.scope,
            "document_ids": entry.document_ids,
            "entity": entry.claim.entity,
            "attribute": entry.claim.attribute,
            "value": entry.claim.value,
            "expected_status": expected,
            "predicted_status": predicted,
            "correct": expected == predicted,
        })
    return rows


def _predicted_citations(response: dict) -> list[dict]:
    citations = []
    for claim in response.get("claims", []):
        citations.extend(claim.get("evidence", []))
    for conflict in response.get("conflicts", []):
        for side in ("source_1", "source_2"):
            source = conflict.get(side)
            if source:
                citations.append({
                    "filename": source["filename"],
                    "page_number": source["page_number"],
                    "text": source.get("snippet", ""),
                })
    return citations


def _citation_correct(citation: dict, facts: list[dict]) -> bool:
    return any(
        citation.get("filename") == fact.get("filename")
        and citation.get("page_number") == fact.get("page_number")
        and citation.get("text", "")
        and citation["text"] in fact.get("snippet", "")
        for fact in facts
    )


def _source_matches_fact(source: dict, value: str, entity: str, attribute: str, fact: dict) -> bool:
    predicted = AtomicClaim(
        claim_id="predicted",
        claim_text="predicted finding",
        entity=entity,
        attribute=attribute,
        value=value,
    )
    if not _same_key(predicted, fact):
        return False
    if (
        source.get("document_id") != fact.get("document_id")
        or source.get("filename") != fact.get("filename")
        or source.get("page_number") != fact.get("page_number")
        or source.get("snippet", "") not in fact.get("snippet", "")
    ):
        return False
    return _same_value(predicted, fact)


def _finding_matches_gold(finding: dict, facts: list[dict]) -> bool:
    first = finding.get("source_1", {})
    second = finding.get("source_2", {})
    entity = finding.get("entity", "")
    attribute = finding.get("attribute", "")
    return any(
        _source_matches_fact(first, finding.get("value_1", ""), entity, attribute, fact1)
        and _source_matches_fact(second, finding.get("value_2", ""), entity, attribute, fact2)
        for fact1 in facts
        for fact2 in facts
        if fact1.get("document_id") != fact2.get("document_id")
    ) or any(
        _source_matches_fact(first, finding.get("value_1", ""), entity, attribute, fact2)
        and _source_matches_fact(second, finding.get("value_2", ""), entity, attribute, fact1)
        for fact1 in facts
        for fact2 in facts
        if fact1.get("document_id") != fact2.get("document_id")
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def score_cases(case_results: Sequence[dict]) -> dict:
    unsupported = [row for row in case_results if row["category"] == "unsupported_absent"]
    positives = [row for row in case_results if row["category"].endswith("_contradiction")]
    negatives = [row for row in case_results if row["category"] in {"unsupported_absent", "consistent_fact"}]
    tp = sum(row.get("matched_conflict_count", 0) for row in positives)
    expected_pairs = sum(row.get("expected_conflict_count", 0) for row in positives)
    fp = sum(row.get("predicted_conflict_count", 0) - row.get("matched_conflict_count", 0) for row in case_results)
    fp = max(fp, 0)
    fn = expected_pairs - tp
    false_positive_cases = sum(row["predicted_conflict"] for row in negatives)
    tn = len(negatives) - false_positive_cases

    observations = [item for row in case_results for item in row.get("claim_verification", [])]
    verified_correct = sum(item["correct"] for item in observations)
    citations = [item for row in case_results for item in row.get("predicted_citations", [])]
    citation_correct = sum(item["correct"] for item in citations)
    expected_citations = sum(len(row.get("expected_facts", [])) for row in case_results)
    covered_gold = {
        (fact["filename"], fact["page_number"], fact["snippet"])
        for row in case_results
        for fact in row.get("expected_facts", [])
        if any(
            cite["correct"]
            and cite["filename"] == fact["filename"]
            and cite["page_number"] == fact["page_number"]
            and cite["text"] in fact["snippet"]
            for cite in row.get("predicted_citations", [])
        )
    }
    rejected = sum(row["predicted_outcome"] == "insufficient_evidence" for row in unsupported)
    return {
        "unsupported_answer_rejection_rate": {
            "numerator": rejected, "denominator": len(unsupported), "rate": _ratio(rejected, len(unsupported)),
        },
        "contradiction_detection_precision": {
            "true_positive": tp, "false_positive": fp, "rate": _ratio(tp, tp + fp),
        },
        "contradiction_detection_recall": {
            "true_positive": tp, "false_negative": fn, "rate": _ratio(tp, tp + fn),
        },
        "claim_verification_accuracy": {
            "correct": verified_correct, "total_observations": len(observations),
            "rate": _ratio(verified_correct, len(observations)),
        },
        "citation_accuracy": {
            "correct": citation_correct, "emitted": len(citations),
            "rate": _ratio(citation_correct, len(citations)),
            "gold_citation_coverage": {
                "covered": len(covered_gold), "expected": expected_citations,
                "rate": _ratio(len(covered_gold), expected_citations),
            },
        },
        "false_positive_contradiction_rate": {
            "false_positive": false_positive_cases, "true_negative": tn,
            "rate": _ratio(false_positive_cases, false_positive_cases + tn),
        },
    }


def evaluate_run(
    *,
    input_cases: Sequence[dict],
    labels_by_id: dict[str, dict],
    input_sha256: str,
    labels_sha256: str,
    factory_name: str,
    outputs: Sequence[tuple[dict, dict | None, ZeroGuessingResponse | None, str | None]],
) -> dict:
    case_results = []
    for input_case, response_json, response, error in outputs:
        label = labels_by_id[input_case["case_id"]]
        predicted = response.status.value if response is not None and error is None else "error"
        if error:
            response_json = None
        citations = _predicted_citations(response_json or {})
        facts = label.get("expected_facts", [])
        for citation in citations:
            citation["correct"] = _citation_correct(citation, facts)
        findings = (response_json or {}).get("conflicts", [])
        conflict = bool(response_json and (response_json.get("status") == "conflict" or findings))
        matched_conflicts = min(
            sum(_finding_matches_gold(item, facts) for item in findings),
            1 if label["expected_conflict"] else 0,
        )
        expected = label["expected_outcome"]
        case_results.append({
            "case_id": input_case["case_id"],
            "category": label["category"],
            "expected_outcome": expected,
            "predicted_outcome": predicted,
            "outcome_correct": predicted == expected,
            "expected_conflict": label["expected_conflict"],
            "predicted_conflict": conflict,
            "expected_conflict_count": 1 if label["expected_conflict"] else 0,
            "predicted_conflict_count": len(findings) or (1 if response_json and response_json.get("status") == "conflict" else 0),
            "matched_conflict_count": matched_conflicts,
            "expected_conflict_type": label.get("expected_conflict_type"),
            "expected_facts": facts,
            "claim_verification": _trace_rows(response, label) if response else [],
            "predicted_citations": citations,
            "response": response_json,
            "error": error,
        })
    completed = sum(row["predicted_outcome"] != "error" for row in case_results)
    return {
        "report_version": "1.0",
        "run_status": "completed" if completed == len(case_results) else "partial",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine_factory": factory_name,
        "dataset": {
            "version": "1.0.0", "case_count": len(input_cases),
            "inputs_sha256": input_sha256, "labels_sha256": labels_sha256,
        },
        "execution": {"attempted": len(case_results), "completed": completed, "failed": len(case_results) - completed},
        "metrics": score_cases(case_results),
        "case_results": case_results,
    }
