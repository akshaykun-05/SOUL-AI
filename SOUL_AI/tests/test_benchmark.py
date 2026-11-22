from __future__ import annotations

import json
from pathlib import Path

from soul_ai.evaluation.benchmark import score_cases, validate_dataset
from benchmark.create_dataset import build


DATA = Path(__file__).resolve().parents[1] / "benchmark" / "data"


def test_dataset_has_required_case_counts_and_separate_labels():
    inputs = json.loads((DATA / "inputs.json").read_text(encoding="utf-8"))
    labels = json.loads((DATA / "labels.json").read_text(encoding="utf-8"))

    validate_dataset(inputs, labels)

    assert len(inputs["cases"]) == 40
    assert all("category" not in item and "expected_outcome" not in item for item in inputs["cases"])
    assert all("expected_outcome" in item for item in labels["cases"])


def test_dataset_generator_is_deterministic():
    first = build()
    second = build()

    assert first == second


def test_dataset_rejects_mismatched_or_duplicate_case_ids():
    inputs = {"dataset_version": "1", "cases": [{"case_id": "A"}]}
    labels = {"dataset_version": "1", "cases": [{"case_id": "B", "category": "unsupported_absent"}]}

    try:
        validate_dataset(inputs, labels)
    except ValueError as exc:
        assert "identical case IDs" in str(exc)
    else:
        raise AssertionError("dataset validation should fail for mismatched case IDs")


def test_metrics_are_computed_from_case_predictions_not_fixed_scores():
    cases = [
        {"category": "unsupported_absent", "predicted_outcome": "insufficient_evidence", "predicted_conflict": False},
        {"category": "unsupported_absent", "predicted_outcome": "answered", "predicted_conflict": False},
        {"category": "numerical_contradiction", "predicted_outcome": "conflict", "predicted_conflict": True,
         "expected_conflict_count": 1, "predicted_conflict_count": 1, "matched_conflict_count": 1},
        {"category": "consistent_fact", "predicted_outcome": "conflict", "predicted_conflict": True,
         "expected_conflict_count": 0, "predicted_conflict_count": 1, "matched_conflict_count": 0},
        {
            "category": "consistent_fact",
            "predicted_outcome": "answered",
            "predicted_conflict": False,
            "expected_conflict_count": 0,
            "predicted_conflict_count": 0,
            "matched_conflict_count": 0,
            "claim_verification": [{"correct": True}, {"correct": False}],
            "predicted_citations": [
                {"correct": True, "filename": "a.pdf", "page_number": 1, "text": "fact one"},
                {"correct": False, "filename": "wrong.pdf", "page_number": 9, "text": "wrong"},
            ],
            "expected_facts": [
                {"filename": "a.pdf", "page_number": 1, "snippet": "fact one"},
                {"filename": "b.pdf", "page_number": 2, "snippet": "fact two"},
            ],
        },
    ]

    metrics = score_cases(cases)

    assert metrics["unsupported_answer_rejection_rate"]["rate"] == 0.5
    assert metrics["contradiction_detection_precision"]["rate"] == 0.5
    assert metrics["contradiction_detection_recall"]["rate"] == 1.0
    assert metrics["false_positive_contradiction_rate"]["rate"] == 0.25
    assert metrics["claim_verification_accuracy"]["rate"] == 0.5
    assert metrics["citation_accuracy"]["rate"] == 0.5


def test_empty_citation_or_claim_metrics_are_null_not_perfect():
    metrics = score_cases([])

    assert metrics["claim_verification_accuracy"]["rate"] is None
    assert metrics["citation_accuracy"]["rate"] is None
    assert metrics["contradiction_detection_precision"]["rate"] is None
