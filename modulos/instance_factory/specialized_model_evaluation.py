from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any


MODEL_CANDIDATE_SCHEMA_VERSION = "specialized_model_candidate_v1"
MODEL_EVALUATION_SCHEMA_VERSION = "specialized_model_evaluation_v1"
ROLLOUT_STATES = (
    "model_candidate",
    "offline_evaluated",
    "critical_error_audited",
    "human_approved_model",
    "shadow_operation",
    "limited_operation",
    "regular_operation",
)
PILOT_THRESHOLDS: dict[str, tuple[str, float]] = {
    "structural_page_accuracy": ("min", 0.95),
    "problem_precision": ("min", 0.95),
    "problem_recall": ("min", 0.95),
    "alternative_coverage": ("min", 0.98),
    "solution_precision": ("min", 0.95),
    "continuity_accuracy": ("min", 0.95),
    "linking_accuracy": ("min", 0.95),
    "critical_foreign_content_rate": ("max", 0.01),
    "manual_intervention_rate": ("max", 0.15),
}
REGULAR_THRESHOLDS: dict[str, tuple[str, float]] = {
    **PILOT_THRESHOLDS,
    "independence_index": ("min", 0.90),
    "manual_intervention_rate": ("max", 0.10),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def next_rollout_state(current_state: str, evaluation_report: Mapping[str, Any]) -> str:
    current = _text(current_state) or "model_candidate"
    report = _mapping(evaluation_report)
    if current not in ROLLOUT_STATES or not bool(report.get("gate_passed")):
        return current
    operation_mode = _text(report.get("operation_mode")) or "pilot"
    if current == "limited_operation" and operation_mode != "regular":
        return current
    index = ROLLOUT_STATES.index(current)
    if index >= len(ROLLOUT_STATES) - 1:
        return current
    return ROLLOUT_STATES[index + 1]


def evaluate_specialized_model_candidate(
    candidate: Mapping[str, Any],
    *,
    operation_mode: str = "pilot",
) -> dict[str, Any]:
    raw = copy.deepcopy(_mapping(candidate))
    mode = _text(operation_mode).lower() or "pilot"
    if mode not in {"pilot", "regular"}:
        raise ValueError(f"operation_mode no soportado: {operation_mode}")
    blockers: list[str] = []
    if _text(raw.get("schema_version")) != MODEL_CANDIDATE_SCHEMA_VERSION:
        blockers.append("candidate:invalid_schema_version")
    for key in ("evaluation_id", "capability_id", "model_id", "model_version"):
        if not _text(raw.get(key)):
            blockers.append(f"candidate:missing_{key}")

    dataset = _mapping(raw.get("dataset"))
    if not _text(dataset.get("dataset_id")):
        blockers.append("dataset:missing_id")
    if _text(dataset.get("status")) != "frozen":
        blockers.append("dataset:not_frozen")
    if _text(dataset.get("split_audit_status")) != "passed":
        blockers.append("dataset:split_audit_not_passed")
    if not [_text(item) for item in _sequence(raw.get("test_document_ids")) if _text(item)]:
        blockers.append("evaluation:missing_unseen_test_documents")
    if not [_text(item) for item in _sequence(raw.get("ood_document_ids")) if _text(item)]:
        blockers.append("evaluation:missing_ood_documents")

    metrics = _mapping(raw.get("metrics"))
    thresholds = REGULAR_THRESHOLDS if mode == "regular" else PILOT_THRESHOLDS
    metric_results: dict[str, dict[str, Any]] = {}
    for metric_name, (direction, threshold) in thresholds.items():
        try:
            value = float(metrics.get(metric_name))
            numeric = True
        except (TypeError, ValueError):
            value = 0.0
            numeric = False
        passed = numeric and (value >= threshold if direction == "min" else value <= threshold)
        metric_results[metric_name] = {
            "value": value if numeric else None,
            "direction": direction,
            "threshold": threshold,
            "passed": passed,
        }
        if not numeric:
            blockers.append(f"metric:{metric_name}:missing")
        elif not passed:
            comparison = "below_minimum" if direction == "min" else "above_maximum"
            blockers.append(f"metric:{metric_name}:{comparison}")

    family_results: dict[str, dict[str, Any]] = {}
    family_metrics = _mapping(raw.get("family_metrics"))
    if not family_metrics:
        blockers.append("family_metrics:missing")
    for family_name in sorted(family_metrics):
        family = _mapping(family_metrics[family_name])
        try:
            evaluated_units = int(family.get("evaluated_units") or 0)
        except (TypeError, ValueError):
            evaluated_units = 0
        try:
            systematic_errors = int(family.get("systematic_critical_errors") or 0)
        except (TypeError, ValueError):
            systematic_errors = 1
        passed = evaluated_units > 0 and systematic_errors == 0
        family_results[str(family_name)] = {
            "evaluated_units": evaluated_units,
            "systematic_critical_errors": systematic_errors,
            "passed": passed,
        }
        if evaluated_units <= 0:
            blockers.append(f"family:{family_name}:no_evaluated_units")
        if systematic_errors > 0:
            blockers.append(f"family:{family_name}:systematic_critical_errors")

    unresolved_critical_errors: list[dict[str, Any]] = []
    for item in _sequence(raw.get("critical_errors")):
        error = _mapping(item)
        if _text(error.get("status")).lower() in {"resolved", "dismissed", "not_an_error"}:
            continue
        unresolved_critical_errors.append(error)
        blockers.append(f"critical_error:{_text(error.get('error_type')) or 'unknown'}")

    abstention = _mapping(raw.get("abstention"))
    if abstention.get("supported") is not True:
        blockers.append("abstention:not_supported")
    if abstention.get("low_confidence_routed") is not True:
        blockers.append("abstention:low_confidence_not_routed")
    human_decision = _mapping(raw.get("human_decision"))
    if _text(human_decision.get("status")) != "approved" or not _text(human_decision.get("reviewer")):
        blockers.append("human_decision:not_approved")
    rollback_target = _mapping(raw.get("rollback_target"))
    if not _text(rollback_target.get("model_id")) or not _text(rollback_target.get("model_version")):
        blockers.append("rollback_target:missing")

    blockers = _dedupe(blockers)
    gate_passed = not blockers
    current_state = _text(raw.get("current_rollout_state")) or "model_candidate"
    report: dict[str, Any] = {
        "schema_version": MODEL_EVALUATION_SCHEMA_VERSION,
        "evaluation_id": _text(raw.get("evaluation_id")),
        "capability_id": _text(raw.get("capability_id")),
        "model_id": _text(raw.get("model_id")),
        "model_version": _text(raw.get("model_version")),
        "dataset_id": _text(dataset.get("dataset_id")),
        "operation_mode": mode,
        "current_rollout_state": current_state,
        "metric_results": metric_results,
        "family_results": family_results,
        "unresolved_critical_errors": unresolved_critical_errors,
        "abstention": abstention,
        "human_decision": human_decision,
        "rollback_target": rollback_target,
        "blockers": blockers,
        "gate_passed": gate_passed,
        "human_execution_required": True,
        "automatic_promotion": False,
    }
    report["recommended_next_state"] = next_rollout_state(current_state, report)
    return report


__all__ = [
    "MODEL_CANDIDATE_SCHEMA_VERSION",
    "MODEL_EVALUATION_SCHEMA_VERSION",
    "PILOT_THRESHOLDS",
    "REGULAR_THRESHOLDS",
    "ROLLOUT_STATES",
    "evaluate_specialized_model_candidate",
    "next_rollout_state",
]
