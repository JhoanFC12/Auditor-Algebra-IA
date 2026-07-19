from __future__ import annotations

import copy
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .annotation_contracts import (
    ANNOTATION_SCHEMA_VERSION,
    GEOMETRY_QUALITY_SCHEMA_VERSION,
    normalize_bbox_norm,
    normalize_pages,
    validate_annotation_package,
)


PRECISION_VALIDATION_SCHEMA_VERSION = "precision_annotation_validation_v1"
QUALITY_STATES = {"pass", "fail", "uncertain", "not_applicable"}
QUALITY_CHECKS = (
    "content_complete",
    "foreign_content_excluded",
    "unit_boundary_valid",
    "alternatives_complete",
    "visible_identifier_captured",
    "continuation_supported",
    "geometry_precise",
)
ALWAYS_REQUIRED_PASS = {
    "content_complete",
    "foreign_content_excluded",
    "unit_boundary_valid",
    "geometry_precise",
}
NEGATIVE_CONTINUITY_TERMS = {
    "header",
    "footer",
    "encabezado",
    "pie de pagina",
    "page number",
    "numero de pagina",
    "blank strip",
    "franja en blanco",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _labels(region: Mapping[str, Any]) -> list[str]:
    members = _mapping(region.get("content_members"))
    labels = members.get("alternative_labels")
    if labels is None:
        labels = region.get("alternative_labels_observed")
    return [_text(item).upper() for item in _sequence(labels) if _text(item)]


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _geometry_warnings(region: Mapping[str, Any]) -> list[str]:
    region_id = _text(region.get("region_id")) or "unknown"
    try:
        x1, y1, x2, y2 = normalize_bbox_norm(region.get("bbox_norm_xyxy"))
    except ValueError:
        return []
    warnings: list[str] = []
    if y1 < 0.12:
        warnings.append(f"region:{region_id}:warning:top_page_band")
    if y2 > 0.88:
        warnings.append(f"region:{region_id}:warning:bottom_page_band")
    if (x2 - x1) * (y2 - y1) > 0.70:
        warnings.append(f"region:{region_id}:warning:large_page_area")
    if (y2 - y1) < 0.06 and (x2 - x1) > 0.60 and (y1 < 0.18 or y2 > 0.82):
        warnings.append(f"region:{region_id}:warning:thin_edge_band")
    return warnings


def _quality_issues(
    region: Mapping[str, Any],
    *,
    unit: Mapping[str, Any],
) -> tuple[list[str], list[str], dict[str, str]]:
    region_id = _text(region.get("region_id")) or "unknown"
    quality = _mapping(region.get("geometry_quality"))
    checks = _mapping(quality.get("checks"))
    issues: list[str] = []
    warnings = _geometry_warnings(region)
    normalized_checks: dict[str, str] = {}
    if _text(quality.get("schema_version")) != GEOMETRY_QUALITY_SCHEMA_VERSION:
        issues.append(f"region:{region_id}:quality:invalid_schema_version")
    region_class = _text(region.get("region_class"))
    source_pages = normalize_pages(unit.get("source_pages"))
    is_multipage = len(source_pages) > 1
    answer_status = _text(unit.get("answer_block_status"))
    for check_name in QUALITY_CHECKS:
        state = _text(checks.get(check_name)).lower()
        normalized_checks[check_name] = state
        if state not in QUALITY_STATES:
            issues.append(f"region:{region_id}:quality:{check_name}:missing")
            continue
        must_pass = check_name in ALWAYS_REQUIRED_PASS
        if check_name == "alternatives_complete":
            must_pass = region_class in {"problem", "answer_block"} and answer_status != "not_applicable"
        if check_name == "continuation_supported":
            must_pass = is_multipage
        if check_name == "visible_identifier_captured":
            identifier_status = _text(unit.get("visible_identifier_status")).lower()
            must_pass = identifier_status not in {"not_visible", "abstained"}
        if state in {"fail", "uncertain"}:
            issues.append(f"region:{region_id}:quality:{check_name}:{state}")
        elif must_pass and state != "pass":
            issues.append(f"region:{region_id}:quality:{check_name}:{state}")
    for exception in _sequence(quality.get("inclusion_exceptions")):
        row = _mapping(exception)
        if not bool(row.get("approved")):
            kind = _text(row.get("excluded_kind")) or "other"
            issues.append(f"region:{region_id}:unapproved_inclusion_exception:{kind}")
    return _dedupe(issues), _dedupe(warnings), normalized_checks


def _continuity_issues(
    relations: list[dict[str, Any]],
    units: list[dict[str, Any]],
) -> list[str]:
    issues: list[str] = []
    continuity = [row for row in relations if _text(row.get("relation_type")) in {"continues_on", "continues_from"}]
    signatures = {
        (
            _text(row.get("relation_type")),
            tuple(_text(item) for item in _sequence(row.get("source_ids")) if _text(item)),
            tuple(_text(item) for item in _sequence(row.get("target_ids")) if _text(item)),
        ): row
        for row in continuity
    }
    for relation in continuity:
        relation_id = _text(relation.get("relation_id")) or "unknown"
        relation_type = _text(relation.get("relation_type"))
        source_ids = tuple(_text(item) for item in _sequence(relation.get("source_ids")) if _text(item))
        target_ids = tuple(_text(item) for item in _sequence(relation.get("target_ids")) if _text(item))
        counterpart = "continues_from" if relation_type == "continues_on" else "continues_on"
        if (counterpart, target_ids, source_ids) not in signatures:
            issues.append(f"relation:{relation_id}:missing_reciprocal_{counterpart}")
        evidence = [_text(item) for item in _sequence(relation.get("evidence")) if _text(item)]
        if len(evidence) < 2:
            issues.append(f"relation:{relation_id}:insufficient_boundary_evidence")
        evidence_text = " ".join(evidence).lower()
        if any(term in evidence_text for term in NEGATIVE_CONTINUITY_TERMS):
            issues.append(f"relation:{relation_id}:negative_continuity_evidence")
    for unit in units:
        if len(normalize_pages(unit.get("source_pages"))) <= 1:
            continue
        unit_id = _text(unit.get("annotation_unit_id")) or "unknown"
        region_ids = {_text(item) for item in _sequence(unit.get("region_ids")) if _text(item)}
        has_relation = any(
            region_ids.intersection(_text(item) for item in _sequence(row.get("source_ids")))
            and region_ids.intersection(_text(item) for item in _sequence(row.get("target_ids")))
            for row in continuity
        )
        if not has_relation:
            issues.append(f"unit:{unit_id}:missing_continuity_relations")
    return _dedupe(issues)


def evaluate_precision_annotation(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = copy.deepcopy(_mapping(payload))
    applicable = _text(raw.get("schema_version")) == ANNOTATION_SCHEMA_VERSION
    contract_issues = validate_annotation_package(raw)
    issues = list(contract_issues)
    warnings: list[str] = []
    units = [_mapping(item) for item in _sequence(raw.get("units"))]
    regions = [_mapping(item) for item in _sequence(raw.get("regions"))]
    relations = [_mapping(item) for item in _sequence(raw.get("relations"))]
    units_by_id = {_text(item.get("annotation_unit_id")): item for item in units}
    regions_by_id = {_text(item.get("region_id")): item for item in regions}
    quality_checks_by_region: dict[str, dict[str, str]] = {}

    for region in regions:
        region_id = _text(region.get("region_id")) or "unknown"
        owner = units_by_id.get(_text(region.get("annotation_unit_id")), {})
        region_issues, region_warnings, normalized_checks = _quality_issues(region, unit=owner)
        issues.extend(region_issues)
        warnings.extend(region_warnings)
        quality_checks_by_region[region_id] = normalized_checks

    answer_block_count = 0
    covered_alternative_count = 0
    for unit in units:
        if _text(unit.get("unit_kind")) != "problem":
            continue
        unit_id = _text(unit.get("annotation_unit_id")) or "unknown"
        status = _text(unit.get("answer_block_status"))
        problem_regions = [
            regions_by_id[region_id]
            for region_id in [_text(item) for item in _sequence(unit.get("region_ids")) if _text(item)]
            if region_id in regions_by_id and _text(regions_by_id[region_id].get("region_class")) == "problem"
        ]
        expected_labels = _dedupe([label for region in problem_regions for label in _labels(region)])
        answer_region_ids: list[str] = []
        for relation in relations:
            if _text(relation.get("relation_type")) != "has_answer_block":
                continue
            if unit_id not in {_text(item) for item in _sequence(relation.get("source_ids"))}:
                continue
            answer_region_ids.extend(_text(item) for item in _sequence(relation.get("target_ids")) if _text(item))
        answer_regions = [
            regions_by_id[region_id]
            for region_id in answer_region_ids
            if region_id in regions_by_id and _text(regions_by_id[region_id].get("region_class")) == "answer_block"
        ]
        answer_block_count += len(answer_regions)
        observed_labels = [label for region in answer_regions for label in _labels(region)]
        covered_alternative_count += len(set(observed_labels))
        duplicates = sorted(label for label, count in Counter(observed_labels).items() if count > 1)
        missing = sorted(set(expected_labels) - set(observed_labels))
        foreign = sorted(set(observed_labels) - set(expected_labels)) if expected_labels else []
        if status == "not_applicable":
            if answer_regions or expected_labels:
                issues.append(f"unit:{unit_id}:answer_block_not_applicable_conflict")
            continue
        if status != "complete":
            issues.append(f"unit:{unit_id}:answer_block_status:{status or 'missing'}")
        if not answer_regions:
            issues.append(f"unit:{unit_id}:missing_answer_blocks")
        for label in missing:
            issues.append(f"unit:{unit_id}:alternative_coverage_missing:{label}")
        for label in duplicates:
            issues.append(f"unit:{unit_id}:alternative_coverage_duplicate:{label}")
        for label in foreign:
            issues.append(f"unit:{unit_id}:foreign_alternative:{label}")
        try:
            expected_count = int(unit.get("expected_alternative_count"))
        except (TypeError, ValueError):
            expected_count = -1
        if expected_count >= 0 and expected_count != len(set(observed_labels)):
            issues.append(f"unit:{unit_id}:alternative_count_mismatch:{expected_count}:{len(set(observed_labels))}")

    issues.extend(_continuity_issues(relations, units))
    issues = _dedupe(issues)
    warnings = _dedupe(warnings)

    unit_results: list[dict[str, Any]] = []
    for unit in units:
        unit_id = _text(unit.get("annotation_unit_id")) or "unknown"
        owned_region_ids = {_text(item) for item in _sequence(unit.get("region_ids")) if _text(item)}
        unit_issue_prefixes = [f"unit:{unit_id}:", *(f"region:{region_id}:" for region_id in owned_region_ids)]
        unit_issues = [issue for issue in issues if any(issue.startswith(prefix) for prefix in unit_issue_prefixes)]
        unit_warnings = [warning for warning in warnings if any(warning.startswith(prefix) for prefix in unit_issue_prefixes)]
        unit_results.append(
            {
                "annotation_unit_id": unit_id,
                "unit_kind": _text(unit.get("unit_kind")),
                "source_pages": normalize_pages(unit.get("source_pages")),
                "h_ps2_ready": not unit_issues,
                "issues": unit_issues,
                "warnings": unit_warnings,
                "quality_checks": {
                    region_id: quality_checks_by_region.get(region_id, {}) for region_id in sorted(owned_region_ids)
                },
            }
        )

    return {
        "schema_version": PRECISION_VALIDATION_SCHEMA_VERSION,
        "applicable": applicable,
        "valid": not issues,
        "h_ps2_ready": applicable and not issues,
        "issues": issues,
        "warnings": warnings,
        "unit_results": unit_results,
        "summary": {
            "unit_count": len(units),
            "region_count": len(regions),
            "relation_count": len(relations),
            "answer_block_count": answer_block_count,
            "covered_alternative_count": covered_alternative_count,
            "blocking_issue_count": len(issues),
            "warning_count": len(warnings),
        },
    }


def evaluate_embedded_precision_annotation(entity: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(entity)
    embedded = raw.get("precision_annotation")
    if not isinstance(embedded, Mapping):
        return {
            "schema_version": PRECISION_VALIDATION_SCHEMA_VERSION,
            "applicable": False,
            "valid": False,
            "h_ps2_ready": False,
            "issues": ["precision_annotation:missing"],
            "warnings": [],
            "unit_results": [],
            "summary": {
                "unit_count": 0,
                "region_count": 0,
                "relation_count": 0,
                "answer_block_count": 0,
                "covered_alternative_count": 0,
                "blocking_issue_count": 1,
                "warning_count": 0,
            },
        }
    return evaluate_precision_annotation(embedded)


__all__ = [
    "ALWAYS_REQUIRED_PASS",
    "PRECISION_VALIDATION_SCHEMA_VERSION",
    "QUALITY_CHECKS",
    "QUALITY_STATES",
    "evaluate_embedded_precision_annotation",
    "evaluate_precision_annotation",
]
