"""Run the honesty benchmark against a configured ZeroGuessingEngine factory."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from soul_ai.evaluation.benchmark import evaluate_run, file_sha256, validate_dataset
from soul_ai.extraction import PdfEvidence


HERE = Path(__file__).resolve().parent
DEFAULT_INPUTS = HERE / "data" / "inputs.json"
DEFAULT_LABELS = HERE / "data" / "labels.json"
DEFAULT_OUTPUT = HERE / "results" / "latest.json"


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _load_factory(spec: str):
    if ":" not in spec:
        raise ValueError("factory must use module.path:callable format")
    module_name, attribute = spec.split(":", 1)
    factory = getattr(importlib.import_module(module_name), attribute)
    if not callable(factory):
        raise TypeError("engine factory target is not callable")
    return factory


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


async def _run_cases(inputs: list[dict], factory) -> list[tuple[dict, dict | None, Any | None, str | None]]:
    results = []
    for case in inputs:
        try:
            evidence = [PdfEvidence.model_validate(item) for item in case["evidence"]]
            # Gold labels and category metadata are intentionally not passed here.
            engine = await _maybe_await(factory(evidence))
            response = await engine.answer(
                case["question"],
                document_versions=case.get("document_versions") or None,
            )
            results.append((case, response.model_dump(mode="json"), response, None))
        except Exception as exc:
            # Avoid leaking request headers or credentials included in provider exceptions.
            results.append((case, None, None, type(exc).__name__))
    return results


def _not_run_report(inputs: dict, labels: dict, input_path: Path, labels_path: Path, reason: str) -> dict:
    null_metric = {"rate": None, "reason": reason}
    category_counts = {
        category: sum(item["category"] == category for item in labels["cases"])
        for category in {item["category"] for item in labels["cases"]}
    }
    return {
        "report_version": "1.0",
        "run_status": "not_run",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine_factory": None,
        "dataset": {
            "version": inputs["dataset_version"],
            "case_count": len(inputs["cases"]),
            "category_counts": category_counts,
            "inputs_sha256": file_sha256(str(input_path)),
            "labels_sha256": file_sha256(str(labels_path)),
        },
        "execution": {"attempted": 0, "completed": 0, "failed": 0},
        "metrics": {
            "unsupported_answer_rejection_rate": null_metric,
            "contradiction_detection_precision": null_metric,
            "contradiction_detection_recall": null_metric,
            "claim_verification_accuracy": null_metric,
            "citation_accuracy": null_metric,
            "false_positive_contradiction_rate": null_metric,
        },
        "case_results": [],
        "not_run_reason": reason,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-factory", default=os.getenv("SOUL_BENCHMARK_ENGINE_FACTORY"))
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    inputs = _load_json(args.inputs)
    labels = _load_json(args.labels)
    validate_dataset(inputs, labels)
    if args.validate_only:
        counts: dict[str, int] = {}
        for item in labels["cases"]:
            counts[item["category"]] = counts.get(item["category"], 0) + 1
        print(json.dumps({"dataset_version": inputs["dataset_version"], "case_count": len(inputs["cases"]), "category_counts": counts}, indent=2))
        return 0

    if not args.engine_factory:
        reason = (
            "No engine factory configured. Set SOUL_BENCHMARK_ENGINE_FACTORY or pass "
            "--engine-factory module.path:callable; no benchmark cases were run."
        )
        report = _not_run_report(inputs, labels, args.inputs, args.labels, reason)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"run_status": "not_run", "attempted": 0, "output": str(args.output), "reason": reason}, indent=2))
        return 2

    labels_by_id = {item["case_id"]: item for item in labels["cases"]}
    try:
        factory = _load_factory(args.engine_factory)
    except Exception as exc:
        parser.error(f"could not load engine factory: {type(exc).__name__}: {exc}")
    outputs = asyncio.run(_run_cases(inputs["cases"], factory))
    report = evaluate_run(
        input_cases=inputs["cases"],
        labels_by_id=labels_by_id,
        input_sha256=file_sha256(str(args.inputs)),
        labels_sha256=file_sha256(str(args.labels)),
        factory_name=args.engine_factory,
        outputs=outputs,
    )
    report["dataset"]["version"] = inputs["dataset_version"]
    if report["execution"]["completed"] == 0:
        report["run_status"] = "failed"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"run_status": report["run_status"], "execution": report["execution"], "metrics": report["metrics"], "output": str(args.output)}, indent=2))
    return 0 if report["run_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
