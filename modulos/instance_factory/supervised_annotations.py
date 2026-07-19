from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .annotation_contracts import ANNOTATION_SCHEMA_VERSION, annotation_fingerprint
from .annotation_quality import evaluate_precision_annotation


ANNOTATION_RELEASE_SCHEMA_VERSION = "supervised_relational_annotation_release_v1"
PRECISION_PILOT_MANIFEST_SCHEMA_VERSION = "precision_pilot_manifest_v1"
PRECISION_PILOT_REQUIRED_CASES = (
    "one_column",
    "two_columns",
    "single_answer_block",
    "multiple_answer_blocks",
    "graphical_alternatives",
    "open_question",
    "full_page_solution",
    "partial_page_solution",
    "true_multipage_continuation",
    "repeated_header_negative",
    "mixed_problem_solution",
)
RELEASE_STATES = {"draft", "validated", "human_approved", "frozen", "rejected", "superseded"}
RELEASE_TRANSITIONS = {
    "draft": {"validated", "rejected"},
    "validated": {"human_approved", "rejected"},
    "human_approved": {"frozen", "rejected"},
    "frozen": {"superseded"},
    "rejected": set(),
    "superseded": set(),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _review_status(annotation: Mapping[str, Any]) -> str:
    review = annotation.get("review")
    if isinstance(review, Mapping):
        return _text(review.get("status")).lower()
    return _text(review).lower()


def _release_without_fingerprint(release: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(_mapping(release))
    payload.pop("release_fingerprint", None)
    return payload


def _refresh_release_fingerprint(release: dict[str, Any]) -> None:
    release["release_fingerprint"] = annotation_fingerprint(_release_without_fingerprint(release))


def build_annotation_release(
    annotations: Sequence[Mapping[str, Any]],
    *,
    dataset_id: str,
    dataset_version: str,
) -> dict[str, Any]:
    clean_dataset_id = _text(dataset_id)
    clean_dataset_version = _text(dataset_version)
    if not clean_dataset_id or not clean_dataset_version:
        raise ValueError("dataset_id y dataset_version son requeridos")

    approved_rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for raw in sorted((_mapping(item) for item in annotations), key=lambda row: _text(row.get("annotation_id"))):
        annotation_id = _text(raw.get("annotation_id")) or "unknown"
        review_status = _review_status(raw)
        validation = evaluate_precision_annotation(raw)
        if review_status != "approved":
            exclusions.append(
                {
                    "annotation_id": annotation_id,
                    "review_status": review_status or "missing",
                    "reason": "human_review_not_approved",
                    "issues": list(validation.get("issues") or []),
                }
            )
            continue
        if not bool(validation.get("h_ps2_ready")):
            exclusions.append(
                {
                    "annotation_id": annotation_id,
                    "review_status": review_status,
                    "reason": "contract_validation_failed",
                    "issues": list(validation.get("issues") or []),
                }
            )
            continue
        annotation = copy.deepcopy(raw)
        annotation["training_eligible"] = True
        annotation["annotation_fingerprint"] = annotation_fingerprint(annotation)
        approved_rows.append(annotation)

    approved_rows.sort(key=lambda row: _text(row.get("annotation_id")))
    exclusions.sort(key=lambda row: (_text(row.get("annotation_id")), _text(row.get("reason"))))
    documents: dict[tuple[str, str], dict[str, Any]] = {}
    class_counts: Counter[str] = Counter()
    relation_counts: Counter[str] = Counter()
    annotation_manifest: list[dict[str, Any]] = []
    for annotation in approved_rows:
        document = _mapping(annotation.get("document"))
        document_id = _text(document.get("document_id"))
        source_digest = _text(document.get("source_digest"))
        documents[(document_id, source_digest)] = {
            "document_id": document_id,
            "source_digest": source_digest,
            "page_count": int(document.get("page_count") or 0),
        }
        regions = [_mapping(item) for item in _sequence(annotation.get("regions"))]
        relations = [_mapping(item) for item in _sequence(annotation.get("relations"))]
        class_counts.update(_text(row.get("region_class")) for row in regions if _text(row.get("region_class")))
        relation_counts.update(_text(row.get("relation_type")) for row in relations if _text(row.get("relation_type")))
        annotation_manifest.append(
            {
                "annotation_id": _text(annotation.get("annotation_id")),
                "document_id": document_id,
                "annotation_fingerprint": _text(annotation.get("annotation_fingerprint")),
                "region_count": len(regions),
                "unit_count": len(_sequence(annotation.get("units"))),
                "relation_count": len(relations),
            }
        )

    release: dict[str, Any] = {
        "schema_version": ANNOTATION_RELEASE_SCHEMA_VERSION,
        "annotation_schema_version": ANNOTATION_SCHEMA_VERSION,
        "contract_version": "precision-annotation-v1",
        "dataset_id": clean_dataset_id,
        "dataset_version": clean_dataset_version,
        "status": "draft",
        "document_manifest": sorted(documents.values(), key=lambda row: (row["document_id"], row["source_digest"])),
        "split_manifest": None,
        "annotation_manifest": sorted(annotation_manifest, key=lambda row: row["annotation_id"]),
        "annotations": approved_rows,
        "audit_exclusions": exclusions,
        "class_counts": dict(sorted(class_counts.items())),
        "relation_counts": dict(sorted(relation_counts.items())),
        "quality_summary": {
            "input_count": len(approved_rows) + len(exclusions),
            "training_eligible_count": len(approved_rows),
            "excluded_count": len(exclusions),
            "all_training_annotations_hps2_ready": bool(approved_rows),
        },
        "source_digests": sorted({row["source_digest"] for row in documents.values()}),
        "approved_by": None,
        "approved_at": None,
        "history": [],
    }
    _refresh_release_fingerprint(release)
    return release


def validate_annotation_release(release: Mapping[str, Any]) -> list[str]:
    raw = _mapping(release)
    issues: list[str] = []
    if _text(raw.get("schema_version")) != ANNOTATION_RELEASE_SCHEMA_VERSION:
        issues.append("release:invalid_schema_version")
    if _text(raw.get("annotation_schema_version")) != ANNOTATION_SCHEMA_VERSION:
        issues.append("release:invalid_annotation_schema_version")
    if not _text(raw.get("dataset_id")):
        issues.append("release:missing_dataset_id")
    if not _text(raw.get("dataset_version")):
        issues.append("release:missing_dataset_version")
    if _text(raw.get("status")) not in RELEASE_STATES:
        issues.append("release:invalid_status")
    annotations = [_mapping(item) for item in _sequence(raw.get("annotations"))]
    if not annotations:
        issues.append("release:missing_training_annotations")
    annotation_ids = [_text(item.get("annotation_id")) for item in annotations]
    if len(annotation_ids) != len(set(annotation_ids)):
        issues.append("release:duplicate_annotation_ids")
    for annotation in annotations:
        annotation_id = _text(annotation.get("annotation_id")) or "unknown"
        if _review_status(annotation) != "approved":
            issues.append(f"release:annotation_not_approved:{annotation_id}")
        result = evaluate_precision_annotation(annotation)
        if not bool(result.get("h_ps2_ready")):
            issues.append(f"release:annotation_invalid:{annotation_id}")
        if not bool(annotation.get("training_eligible")):
            issues.append(f"release:annotation_not_training_eligible:{annotation_id}")
        expected_fingerprint = annotation_fingerprint(
            {key: value for key, value in annotation.items() if key != "annotation_fingerprint"}
        )
        if _text(annotation.get("annotation_fingerprint")) != expected_fingerprint:
            issues.append(f"release:annotation_fingerprint_mismatch:{annotation_id}")
    manifests = [_mapping(item) for item in _sequence(raw.get("annotation_manifest"))]
    if sorted(_text(item.get("annotation_id")) for item in manifests) != sorted(annotation_ids):
        issues.append("release:annotation_manifest_mismatch")
    expected_release_fingerprint = annotation_fingerprint(_release_without_fingerprint(raw))
    if _text(raw.get("release_fingerprint")) != expected_release_fingerprint:
        issues.append("release:fingerprint_mismatch")
    return list(dict.fromkeys(issues))


def transition_annotation_release(
    release: Mapping[str, Any],
    target_status: str,
    *,
    actor: str,
    occurred_at: str,
) -> dict[str, Any]:
    raw = copy.deepcopy(_mapping(release))
    current = _text(raw.get("status"))
    target = _text(target_status)
    clean_actor = _text(actor)
    clean_occurred_at = _text(occurred_at)
    if target not in RELEASE_TRANSITIONS.get(current, set()):
        raise ValueError(f"Transicion de release no permitida: {current} -> {target}")
    if not clean_actor or not clean_occurred_at:
        raise ValueError("actor y occurred_at son requeridos")
    if target == "validated":
        validation_issues = validate_annotation_release(raw)
        if validation_issues:
            raise ValueError("Release invalido: " + ";".join(validation_issues))
    if target in {"human_approved", "frozen"} and not _sequence(raw.get("annotations")):
        raise ValueError("No se puede aprobar o congelar un release vacio")
    raw["status"] = target
    history = [_mapping(item) for item in _sequence(raw.get("history"))]
    history.append({"from": current, "to": target, "actor": clean_actor, "occurred_at": clean_occurred_at})
    raw["history"] = history
    if target == "validated":
        raw["validated_at"] = clean_occurred_at
    if target == "human_approved":
        raw["approved_by"] = clean_actor
        raw["approved_at"] = clean_occurred_at
    if target == "frozen":
        if not _text(raw.get("approved_by")):
            raise ValueError("El release debe contar con aprobacion humana antes de congelarse")
        raw["frozen_fingerprint"] = annotation_fingerprint(_release_without_fingerprint(raw))
    _refresh_release_fingerprint(raw)
    return raw


def build_precision_pilot_manifest(
    pages: Sequence[Mapping[str, Any]],
    *,
    pilot_id: str,
) -> dict[str, Any]:
    clean_pilot_id = _text(pilot_id)
    if not clean_pilot_id:
        raise ValueError("pilot_id es requerido")
    normalized_pages: list[dict[str, Any]] = []
    for raw in pages:
        row = _mapping(raw)
        document_id = _text(row.get("document_id"))
        source_digest = _text(row.get("source_digest"))
        try:
            page_number = int(row.get("page_number") or 0)
        except (TypeError, ValueError):
            page_number = 0
        if not document_id or not source_digest or page_number < 1:
            raise ValueError("Cada pagina piloto requiere document_id, source_digest y page_number")
        normalized_pages.append(
            {
                "document_id": document_id,
                "source_digest": source_digest,
                "page_number": page_number,
                "case_tags": sorted({_text(item) for item in _sequence(row.get("case_tags")) if _text(item)}),
                "authorized_roles": sorted({_text(item) for item in _sequence(row.get("authorized_roles")) if _text(item)}),
                "synthetic": True,
                "canonical": False,
            }
        )
    normalized_pages.sort(key=lambda row: (row["document_id"], row["page_number"]))
    covered_cases = sorted({tag for row in normalized_pages for tag in row["case_tags"]})
    manifest: dict[str, Any] = {
        "schema_version": PRECISION_PILOT_MANIFEST_SCHEMA_VERSION,
        "pilot_id": clean_pilot_id,
        "page_count": len(normalized_pages),
        "required_cases": list(PRECISION_PILOT_REQUIRED_CASES),
        "covered_cases": covered_cases,
        "pages": normalized_pages,
        "controls": {
            "synthetic_shape_only": True,
            "agents_dispatched": False,
            "canonical_writes": "disabled",
            "training": "not_started",
            "promotion": "not_started",
        },
    }
    manifest["manifest_fingerprint"] = annotation_fingerprint(
        {key: value for key, value in manifest.items() if key != "manifest_fingerprint"}
    )
    return manifest


def validate_precision_pilot_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_page_count: int = 20,
) -> list[str]:
    raw = _mapping(manifest)
    issues: list[str] = []
    if _text(raw.get("schema_version")) != PRECISION_PILOT_MANIFEST_SCHEMA_VERSION:
        issues.append("pilot:invalid_schema_version")
    if not _text(raw.get("pilot_id")):
        issues.append("pilot:missing_pilot_id")
    pages = [_mapping(item) for item in _sequence(raw.get("pages"))]
    if len(pages) != expected_page_count or int(raw.get("page_count") or 0) != expected_page_count:
        issues.append(f"pilot:expected_{expected_page_count}_pages")
    identities = [(_text(row.get("document_id")), int(row.get("page_number") or 0)) for row in pages]
    if len(identities) != len(set(identities)):
        issues.append("pilot:duplicate_pages")
    covered_cases = {tag for row in pages for tag in (_text(item) for item in _sequence(row.get("case_tags"))) if tag}
    for required_case in PRECISION_PILOT_REQUIRED_CASES:
        if required_case not in covered_cases:
            issues.append(f"pilot:missing_case:{required_case}")
    if any(row.get("synthetic") is not True or row.get("canonical") is not False for row in pages):
        issues.append("pilot:non_synthetic_or_canonical_page")
    controls = _mapping(raw.get("controls"))
    expected_controls = {
        "synthetic_shape_only": True,
        "agents_dispatched": False,
        "canonical_writes": "disabled",
        "training": "not_started",
        "promotion": "not_started",
    }
    for key, expected in expected_controls.items():
        if controls.get(key) != expected:
            issues.append(f"pilot:unsafe_control:{key}")
    expected_fingerprint = annotation_fingerprint(
        {key: value for key, value in raw.items() if key != "manifest_fingerprint"}
    )
    if _text(raw.get("manifest_fingerprint")) != expected_fingerprint:
        issues.append("pilot:fingerprint_mismatch")
    return list(dict.fromkeys(issues))


__all__ = [
    "ANNOTATION_RELEASE_SCHEMA_VERSION",
    "PRECISION_PILOT_MANIFEST_SCHEMA_VERSION",
    "PRECISION_PILOT_REQUIRED_CASES",
    "RELEASE_STATES",
    "RELEASE_TRANSITIONS",
    "build_annotation_release",
    "build_precision_pilot_manifest",
    "transition_annotation_release",
    "validate_annotation_release",
    "validate_precision_pilot_manifest",
]
