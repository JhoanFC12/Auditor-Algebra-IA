from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


ANNOTATION_SCHEMA_VERSION = "supervised_relational_annotation_v1"
PRECISION_CONTRACT_VERSION = "precision-annotation-v1"
GEOMETRY_QUALITY_SCHEMA_VERSION = "ingrid_geometry_quality_v1"

REGION_CLASSES = {
    "problem",
    "problem_number",
    "problem_statement",
    "answer_block",
    "formula",
    "table",
    "graph",
    "figure",
    "solution",
}
UNIT_KINDS = {"problem", "solution"}
RELATION_TYPES = {
    "contains",
    "belongs_to",
    "continues_on",
    "continues_from",
    "solves",
    "has_answer_block",
    "precedes",
    "same_entity",
}
HUMAN_REVIEW_STATES = {"pending", "approved", "corrected", "rejected", "abstained"}
_DIGEST_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")
_VOLATILE_KEYS = {"fingerprint", "release_fingerprint", "generated_at", "validated_at", "approved_at"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _unique(items: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _without_volatile(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_volatile(item)
            for key, item in value.items()
            if str(key) not in _VOLATILE_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_without_volatile(item) for item in value]
    return value


def annotation_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _without_volatile(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def normalize_bbox_norm(value: Any) -> tuple[float, float, float, float]:
    values = _sequence(value)
    if len(values) != 4:
        raise ValueError("bbox_norm_xyxy must contain four coordinates")
    try:
        x1, y1, x2, y2 = (float(item) for item in values)
    except (TypeError, ValueError) as exc:
        raise ValueError("bbox_norm_xyxy coordinates must be numeric") from exc
    if not (0.0 <= x1 < x2 <= 1.0 and 0.0 <= y1 < y2 <= 1.0):
        raise ValueError("bbox_norm_xyxy must satisfy 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1")
    return x1, y1, x2, y2


def normalize_bbox_pixels(value: Any) -> tuple[float, float, float, float]:
    values = _sequence(value)
    if len(values) != 4:
        raise ValueError("bbox_xyxy must contain four coordinates")
    try:
        x1, y1, x2, y2 = (float(item) for item in values)
    except (TypeError, ValueError) as exc:
        raise ValueError("bbox_xyxy coordinates must be numeric") from exc
    if not (0.0 <= x1 < x2 and 0.0 <= y1 < y2):
        raise ValueError("bbox_xyxy must contain ordered non-negative coordinates")
    return x1, y1, x2, y2


def normalize_pages(value: Any) -> list[int]:
    pages: set[int] = set()
    for raw in _sequence(value):
        try:
            page = int(raw)
        except (TypeError, ValueError):
            continue
        if page > 0:
            pages.add(page)
    return sorted(pages)


def _review_state(value: Any) -> str:
    if isinstance(value, Mapping):
        return _text(value.get("status")).lower()
    return _text(value).lower()


def validate_region_annotation(
    region: Mapping[str, Any],
    *,
    document_id: str = "",
    page_count: int = 0,
) -> list[str]:
    raw = _mapping(region)
    region_id = _text(raw.get("region_id"))
    prefix = f"region:{region_id or 'unknown'}"
    issues: list[str] = []
    if not region_id:
        issues.append(f"{prefix}:missing_id")
    if not _text(raw.get("annotation_unit_id")):
        issues.append(f"{prefix}:missing_annotation_unit_id")
    region_document = _text(raw.get("document_id"))
    if not region_document:
        issues.append(f"{prefix}:missing_document_id")
    elif document_id and region_document != document_id:
        issues.append(f"{prefix}:document_mismatch")
    try:
        page_number = int(raw.get("page_number") or 0)
    except (TypeError, ValueError):
        page_number = 0
    if page_number < 1 or (page_count > 0 and page_number > page_count):
        issues.append(f"{prefix}:invalid_page_number")
    region_class = _text(raw.get("region_class"))
    if region_class not in REGION_CLASSES:
        issues.append(f"{prefix}:invalid_region_class")
    try:
        normalize_bbox_norm(raw.get("bbox_norm_xyxy"))
    except ValueError:
        issues.append(f"{prefix}:invalid_bbox_norm")
    if raw.get("bbox_xyxy") is not None:
        try:
            normalize_bbox_pixels(raw.get("bbox_xyxy"))
        except ValueError:
            issues.append(f"{prefix}:invalid_bbox_pixels")
    if not isinstance(raw.get("content_members"), Mapping):
        issues.append(f"{prefix}:missing_content_members")
    if not isinstance(raw.get("geometry_quality"), Mapping):
        issues.append(f"{prefix}:missing_geometry_quality")
    try:
        confidence = float(raw.get("confidence"))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError
    except (TypeError, ValueError):
        issues.append(f"{prefix}:invalid_confidence")
    if _text(raw.get("contract_version")) != PRECISION_CONTRACT_VERSION:
        issues.append(f"{prefix}:invalid_contract_version")
    if _text(raw.get("annotation_schema_version")) != ANNOTATION_SCHEMA_VERSION:
        issues.append(f"{prefix}:invalid_annotation_schema_version")
    annotator = _mapping(raw.get("annotator"))
    if not _text(annotator.get("agent_id")) or not _text(annotator.get("capability_id")):
        issues.append(f"{prefix}:missing_annotator")
    if _review_state(raw.get("human_review")) not in HUMAN_REVIEW_STATES:
        issues.append(f"{prefix}:invalid_human_review")
    return _unique(issues)


def validate_annotation_unit(
    unit: Mapping[str, Any],
    *,
    document_id: str = "",
    page_count: int = 0,
) -> list[str]:
    raw = _mapping(unit)
    unit_id = _text(raw.get("annotation_unit_id"))
    prefix = f"unit:{unit_id or 'unknown'}"
    issues: list[str] = []
    if not unit_id:
        issues.append(f"{prefix}:missing_id")
    if _text(raw.get("unit_kind")) not in UNIT_KINDS:
        issues.append(f"{prefix}:invalid_unit_kind")
    unit_document = _text(raw.get("document_id"))
    if not unit_document:
        issues.append(f"{prefix}:missing_document_id")
    elif document_id and unit_document != document_id:
        issues.append(f"{prefix}:document_mismatch")
    if not _text(raw.get("exercise_set_id")):
        issues.append(f"{prefix}:missing_exercise_set_id")
    source_pages = normalize_pages(raw.get("source_pages"))
    if not source_pages or (page_count > 0 and source_pages[-1] > page_count):
        issues.append(f"{prefix}:invalid_source_pages")
    region_ids = [_text(item) for item in _sequence(raw.get("region_ids")) if _text(item)]
    if not region_ids:
        issues.append(f"{prefix}:missing_region_ids")
    if len(region_ids) != len(set(region_ids)):
        issues.append(f"{prefix}:duplicate_region_ids")
    relation_ids = [_text(item) for item in _sequence(raw.get("relation_ids")) if _text(item)]
    if len(relation_ids) != len(set(relation_ids)):
        issues.append(f"{prefix}:duplicate_relation_ids")
    identifier = _text(raw.get("visible_identifier_raw") or raw.get("visible_identifier_normalized"))
    identifier_status = _text(raw.get("visible_identifier_status")).lower()
    if not identifier and identifier_status not in {"not_visible", "abstained"}:
        issues.append(f"{prefix}:missing_visible_identifier_or_abstention")
    if _review_state(raw.get("human_review")) not in HUMAN_REVIEW_STATES:
        issues.append(f"{prefix}:invalid_human_review")
    return _unique(issues)


def validate_annotation_relation(
    relation: Mapping[str, Any],
    *,
    document_id: str = "",
    page_count: int = 0,
) -> list[str]:
    raw = _mapping(relation)
    relation_id = _text(raw.get("relation_id"))
    prefix = f"relation:{relation_id or 'unknown'}"
    issues: list[str] = []
    if not relation_id:
        issues.append(f"{prefix}:missing_id")
    if _text(raw.get("relation_type")) not in RELATION_TYPES:
        issues.append(f"{prefix}:invalid_relation_type")
    source_ids = [_text(item) for item in _sequence(raw.get("source_ids")) if _text(item)]
    target_ids = [_text(item) for item in _sequence(raw.get("target_ids")) if _text(item)]
    if not source_ids:
        issues.append(f"{prefix}:missing_source_ids")
    if not target_ids:
        issues.append(f"{prefix}:missing_target_ids")
    relation_document = _text(raw.get("document_id"))
    if not relation_document:
        issues.append(f"{prefix}:missing_document_id")
    elif document_id and relation_document != document_id:
        issues.append(f"{prefix}:document_mismatch")
    for key in ("source_pages", "target_pages"):
        pages = normalize_pages(raw.get(key))
        if not pages or (page_count > 0 and pages[-1] > page_count):
            issues.append(f"{prefix}:invalid_{key}")
    if not _sequence(raw.get("evidence")):
        issues.append(f"{prefix}:missing_evidence")
    try:
        confidence = float(raw.get("confidence"))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError
    except (TypeError, ValueError):
        issues.append(f"{prefix}:invalid_confidence")
    if _text(raw.get("contract_version")) != PRECISION_CONTRACT_VERSION:
        issues.append(f"{prefix}:invalid_contract_version")
    if _review_state(raw.get("human_review")) not in HUMAN_REVIEW_STATES:
        issues.append(f"{prefix}:invalid_human_review")
    return _unique(issues)


def validate_annotation_package(payload: Mapping[str, Any]) -> list[str]:
    raw = _mapping(payload)
    issues: list[str] = []
    if _text(raw.get("schema_version")) != ANNOTATION_SCHEMA_VERSION:
        issues.append("annotation:invalid_schema_version")
    if not _text(raw.get("annotation_id")):
        issues.append("annotation:missing_annotation_id")
    document = _mapping(raw.get("document"))
    document_id = _text(document.get("document_id"))
    if not document_id:
        issues.append("annotation:missing_document_id")
    source_digest = _text(document.get("source_digest"))
    if not _DIGEST_RE.fullmatch(source_digest):
        issues.append("annotation:invalid_source_digest")
    try:
        page_count = int(document.get("page_count") or 0)
    except (TypeError, ValueError):
        page_count = 0
    if page_count < 1:
        issues.append("annotation:invalid_page_count")
    if _text(raw.get("contract_version")) != PRECISION_CONTRACT_VERSION:
        issues.append("annotation:invalid_contract_version")
    if _text(raw.get("annotation_schema_version")) != ANNOTATION_SCHEMA_VERSION:
        issues.append("annotation:invalid_annotation_schema_version")
    annotator = _mapping(raw.get("annotator"))
    if not _text(annotator.get("agent_id")) or not _text(annotator.get("capability_id")):
        issues.append("annotation:missing_annotator")
    if _review_state(raw.get("review")) not in HUMAN_REVIEW_STATES:
        issues.append("annotation:invalid_review")

    regions = [_mapping(item) for item in _sequence(raw.get("regions"))]
    units = [_mapping(item) for item in _sequence(raw.get("units"))]
    relations = [_mapping(item) for item in _sequence(raw.get("relations"))]
    if not regions:
        issues.append("annotation:missing_regions")
    if not units:
        issues.append("annotation:missing_units")

    region_ids = [_text(item.get("region_id")) for item in regions]
    unit_ids = [_text(item.get("annotation_unit_id")) for item in units]
    relation_ids = [_text(item.get("relation_id")) for item in relations]
    for kind, identifiers in (("region", region_ids), ("unit", unit_ids), ("relation", relation_ids)):
        nonempty = [identifier for identifier in identifiers if identifier]
        if len(nonempty) != len(set(nonempty)):
            issues.append(f"annotation:duplicate_{kind}_ids")

    region_by_id = {_text(item.get("region_id")): item for item in regions if _text(item.get("region_id"))}
    unit_by_id = {_text(item.get("annotation_unit_id")): item for item in units if _text(item.get("annotation_unit_id"))}
    relation_by_id = {_text(item.get("relation_id")): item for item in relations if _text(item.get("relation_id"))}
    all_ids = set(region_by_id) | set(unit_by_id)

    for region in regions:
        issues.extend(validate_region_annotation(region, document_id=document_id, page_count=page_count))
        owner = _text(region.get("annotation_unit_id"))
        if owner and owner not in unit_by_id:
            issues.append(f"region:{_text(region.get('region_id')) or 'unknown'}:unknown_annotation_unit")
    for unit in units:
        issues.extend(validate_annotation_unit(unit, document_id=document_id, page_count=page_count))
        unit_id = _text(unit.get("annotation_unit_id"))
        for region_id in [_text(item) for item in _sequence(unit.get("region_ids")) if _text(item)]:
            region = region_by_id.get(region_id)
            if region is None:
                issues.append(f"unit:{unit_id}:unknown_region:{region_id}")
            elif _text(region.get("annotation_unit_id")) != unit_id:
                issues.append(f"unit:{unit_id}:foreign_region:{region_id}")
        for relation_id in [_text(item) for item in _sequence(unit.get("relation_ids")) if _text(item)]:
            if relation_id not in relation_by_id:
                issues.append(f"unit:{unit_id}:unknown_relation:{relation_id}")
    for relation in relations:
        issues.extend(validate_annotation_relation(relation, document_id=document_id, page_count=page_count))
        relation_id = _text(relation.get("relation_id")) or "unknown"
        source_ids = [_text(item) for item in _sequence(relation.get("source_ids")) if _text(item)]
        target_ids = [_text(item) for item in _sequence(relation.get("target_ids")) if _text(item)]
        for identifier in source_ids + target_ids:
            if identifier not in all_ids:
                issues.append(f"relation:{relation_id}:unknown_reference:{identifier}")
        relation_type = _text(relation.get("relation_type"))
        if relation_type == "has_answer_block":
            for source_id in source_ids:
                if _text(unit_by_id.get(source_id, {}).get("unit_kind")) != "problem":
                    issues.append(f"relation:{relation_id}:invalid_problem_source")
            for target_id in target_ids:
                if _text(region_by_id.get(target_id, {}).get("region_class")) != "answer_block":
                    issues.append(f"relation:{relation_id}:invalid_answer_block_target")
        if relation_type == "solves":
            for source_id in source_ids:
                if _text(unit_by_id.get(source_id, {}).get("unit_kind")) != "solution":
                    issues.append(f"relation:{relation_id}:invalid_solution_source")
            for target_id in target_ids:
                if _text(unit_by_id.get(target_id, {}).get("unit_kind")) != "problem":
                    issues.append(f"relation:{relation_id}:invalid_problem_target")
    return _unique(issues)


__all__ = [
    "ANNOTATION_SCHEMA_VERSION",
    "GEOMETRY_QUALITY_SCHEMA_VERSION",
    "HUMAN_REVIEW_STATES",
    "PRECISION_CONTRACT_VERSION",
    "REGION_CLASSES",
    "RELATION_TYPES",
    "UNIT_KINDS",
    "annotation_fingerprint",
    "normalize_bbox_norm",
    "normalize_bbox_pixels",
    "normalize_pages",
    "validate_annotation_package",
    "validate_annotation_relation",
    "validate_annotation_unit",
    "validate_region_annotation",
]
