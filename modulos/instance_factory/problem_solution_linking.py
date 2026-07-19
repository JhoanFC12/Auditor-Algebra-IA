from __future__ import annotations

import copy
import hashlib
import json
import re
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from typing import Any

from .annotation_contracts import ANNOTATION_SCHEMA_VERSION
from .annotation_quality import evaluate_embedded_precision_annotation


CANDIDATE_LINK_SCHEMA_VERSION = "problem_solution_candidate_link_v1"
PROMOTION_BUNDLE_SCHEMA_VERSION = "problem_solution_promotion_bundle_v1"
VISUAL_SOLUTION_SCHEMA_VERSION = "visual_solution_v1"

LINK_STATUSES = {
    "high_confidence",
    "review_required",
    "weak",
    "orphan",
    "conflict",
}
PROMOTABLE_BUNDLE_STATUS = "human_confirmed"
ALLOWED_RELATION_KINDS = {"one_to_one", "alternative_solution", "shared_solution"}
_VOLATILE_FINGERPRINT_KEYS = {"bundle_fingerprint", "created_at", "updated_at", "generated_at", "promoted_at"}
_CANDIDATE_REVIEW_KEYS = {"human_review", "review_status", "selected_problem_unit_id"}
_SCOPE_KEYS = ("book_code", "instance_type", "exercise_set_id")
_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,6})(?:\s*[-_.]?\s*([A-Za-z]))?(?!\d)")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _without_volatile_fields(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_volatile_fields(item)
            for key, item in value.items()
            if str(key) not in _VOLATILE_FINGERPRINT_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_without_volatile_fields(item) for item in value]
    if isinstance(value, set):
        return sorted((_without_volatile_fields(item) for item in value), key=lambda item: repr(item))
    return value


def canonical_payload_fingerprint(value: Any) -> str:
    """Return a deterministic SHA-256 fingerprint for JSON-compatible content."""

    encoded = json.dumps(
        _without_volatile_fields(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def bundle_fingerprint(bundle: Mapping[str, Any]) -> str:
    return canonical_payload_fingerprint(dict(bundle or {}))


def problem_source_fingerprint(record: Mapping[str, Any] | Any) -> str:
    if hasattr(record, "to_dict"):
        record = record.to_dict()
    raw = _mapping(record)
    return canonical_payload_fingerprint(
        {
            "record_id": _text(raw.get("record_id")),
            "crop_id": _text(raw.get("crop_id")),
            "crop_path": _text(raw.get("crop_path")),
            "source": _mapping(raw.get("source")),
            "normalized": _mapping(raw.get("normalized")),
        }
    )


def unit_source_fingerprint(unit: Mapping[str, Any]) -> str:
    # Do not trust a caller-provided digest as the complete unit identity.  A
    # retained digest with changed pages/boxes used to keep reviewed bundles
    # apparently current.  The explicit source digest remains part of the
    # payload, while the derived fingerprint itself is removed to avoid a
    # recursive value.
    payload = copy.deepcopy(_mapping(unit))
    payload.pop("source_fingerprint", None)
    return canonical_payload_fingerprint(payload)


def candidate_evidence_fingerprint(candidate: Mapping[str, Any]) -> str:
    """Fingerprint only the model/rule evidence a human decision refers to."""

    payload = {
        key: copy.deepcopy(value)
        for key, value in _mapping(candidate).items()
        if key not in _CANDIDATE_REVIEW_KEYS
    }
    payload.pop("candidate_evidence_fingerprint", None)
    payload.pop("review_fingerprint", None)
    return canonical_payload_fingerprint(payload)


def candidate_review_fingerprint(candidate: Mapping[str, Any]) -> str:
    raw = _mapping(candidate)
    return canonical_payload_fingerprint(
        {
            "candidate_link_id": _text(raw.get("candidate_link_id")),
            "human_review": copy.deepcopy(_mapping(raw.get("human_review"))),
            "review_status": _text(raw.get("review_status")),
            "selected_problem_unit_id": _text(raw.get("selected_problem_unit_id")),
        }
    )


def normalize_exercise_number(value: Any) -> str:
    """Normalize visible exercise identifiers without inventing one."""

    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, int):
        return str(value) if value >= 0 else ""
    text = _text(value)
    if not text:
        return ""
    match = _NUMBER_RE.search(text)
    if match:
        number = str(int(match.group(1)))
        suffix = _text(match.group(2)).lower()
        return f"{number}{suffix}" if suffix else number
    compact = re.sub(r"[^a-z0-9]+", "", text.lower())
    return compact if compact and len(compact) <= 16 else ""


def _unit_id(unit: Mapping[str, Any], *, fallback_prefix: str) -> str:
    for key in ("unit_id", f"{fallback_prefix}_unit_id", "record_id", "solution_id", "fragment_id", "id"):
        value = _text(unit.get(key))
        if value:
            return value
    return f"{fallback_prefix}_{canonical_payload_fingerprint(unit).split(':', 1)[1][:16]}"


def _number(unit: Mapping[str, Any]) -> str:
    for key in ("number_normalized", "normalized_number", "problem_number", "solution_number", "number", "number_raw"):
        number = normalize_exercise_number(unit.get(key))
        if number:
            return number
    return ""


def _page_span(unit: Mapping[str, Any]) -> tuple[int, int]:
    raw = unit.get("page_span")
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        try:
            left, right = int(raw[0]), int(raw[1])
            return min(left, right), max(left, right)
        except Exception:
            pass
    try:
        page = int(unit.get("page_number") or unit.get("page") or 0)
    except Exception:
        page = 0
    return page, page


def _bbox(unit: Mapping[str, Any]) -> tuple[float, float, float, float]:
    raw = unit.get("bbox_px") or unit.get("bbox") or []
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return 0.0, 0.0, 0.0, 0.0
    try:
        return tuple(float(item) for item in raw[:4])  # type: ignore[return-value]
    except Exception:
        return 0.0, 0.0, 0.0, 0.0


def _reading_key(unit: Mapping[str, Any]) -> tuple[float, float, float, str]:
    span = _page_span(unit)
    try:
        explicit = float(unit.get("reading_order") or unit.get("source_order") or 0)
    except Exception:
        explicit = 0.0
    column_raw = unit.get("column") or unit.get("column_index") or 0
    try:
        column = float(column_raw)
    except Exception:
        column = 0.0
    y1 = _bbox(unit)[1]
    order = explicit if explicit else column * 1_000_000.0 + y1
    return float(span[0]), order, y1, _unit_id(unit, fallback_prefix="unit")


def _same_value(left: Mapping[str, Any], right: Mapping[str, Any], *keys: str) -> bool | None:
    for key in keys:
        left_value = _text(left.get(key))
        right_value = _text(right.get(key))
        if left_value and right_value:
            return left_value == right_value
    return None


def _scope_values(unit: Mapping[str, Any]) -> tuple[str, str, str]:
    scope = _mapping(unit.get("scope"))
    return (
        _text(unit.get("book_code") or unit.get("book_id") or scope.get("book_code") or scope.get("book_id")),
        _text(
            unit.get("instance_type")
            or unit.get("instance_id")
            or scope.get("instance_type")
            or scope.get("instance_id")
        ),
        _text(unit.get("exercise_set_id") or scope.get("exercise_set_id")),
    )


def _missing_scope_fields(unit: Mapping[str, Any]) -> list[str]:
    return [key for key, value in zip(_SCOPE_KEYS, _scope_values(unit)) if not value]


def _scope_compatible(problem: Mapping[str, Any], solution: Mapping[str, Any]) -> bool:
    problem_scope = _scope_values(problem)
    solution_scope = _scope_values(solution)
    return all(problem_scope) and all(solution_scope) and problem_scope == solution_scope


def _scope_signature(unit: Mapping[str, Any]) -> tuple[str, str, str]:
    return _scope_values(unit)


def precision_gate_for_unit(unit: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate an embedded V2 precision package without changing legacy V1 units."""

    raw = _mapping(unit)
    opted_in = (
        _text(raw.get("annotation_schema_version")) == ANNOTATION_SCHEMA_VERSION
        or isinstance(raw.get("precision_annotation"), Mapping)
    )
    result = evaluate_embedded_precision_annotation(raw)
    if not opted_in:
        result = copy.deepcopy(result)
        result["issues"] = []
        result["summary"] = {**_mapping(result.get("summary")), "blocking_issue_count": 0}
    return result


def _same_set(problem: Mapping[str, Any], solution: Mapping[str, Any]) -> bool:
    return _scope_values(problem)[2] == _scope_values(solution)[2] and bool(_scope_values(problem)[2])


def _same_column(problem: Mapping[str, Any], solution: Mapping[str, Any]) -> bool:
    return _same_value(problem, solution, "column", "column_index") is True


def _ref(unit: Mapping[str, Any], *, role: str) -> dict[str, Any]:
    page_span = _page_span(unit)
    book_code, instance_type, exercise_set_id = _scope_values(unit)
    source_fingerprint = (
        _text(unit.get("source_fingerprint"))
        if role == "problem"
        else unit_source_fingerprint(unit)
    )
    source_fingerprint = source_fingerprint or unit_source_fingerprint(unit)
    ref = {
        "unit_id": _unit_id(unit, fallback_prefix=role),
        "record_id": _text(unit.get("record_id")),
        "book_code": book_code,
        "instance_type": instance_type,
        "exercise_set_id": exercise_set_id,
        "number_raw": _text(unit.get("number_raw") or unit.get("number") or unit.get("problem_number") or unit.get("solution_number")),
        "number_normalized": _number(unit),
        "page_span": [int(page_span[0]), int(page_span[1])],
        "source_fingerprint": source_fingerprint,
    }
    return {key: value for key, value in ref.items() if value not in ("", [], None)}


def _candidate_id(pattern: str, solution: Mapping[str, Any], problem: Mapping[str, Any] | None) -> str:
    scope = _scope_signature(solution)
    raw = "|".join(
        (
            pattern,
            _unit_id(solution, fallback_prefix="solution"),
            _unit_id(problem or {}, fallback_prefix="orphan"),
            *scope,
        )
    )
    return f"psl_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _signal(signals: list[dict[str, Any]], name: str, weight: int, evidence: Any) -> int:
    signals.append({"name": name, "weight": int(weight), "evidence": evidence})
    return int(weight)


def _normalized_pattern(pattern: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "_", _text(pattern).lower()).strip("_")
    if clean in {"separate", "separated", "separate_sections", "sections", "secciones_separadas"}:
        return "separate_sections"
    if clean in {"interleaved", "intercalado", "intercalados", "mixed_order"}:
        return "interleaved"
    raise ValueError(f"Patron editorial no soportado: {pattern!r}")


def _score_separate(
    problem: Mapping[str, Any],
    solution: Mapping[str, Any],
    *,
    structure: Mapping[str, Any],
    problem_rank: int,
    solution_rank: int,
    exact_number_count: int,
) -> tuple[int, list[dict[str, Any]], list[str], dict[str, bool]]:
    score = 0
    signals: list[dict[str, Any]] = []
    ambiguity: list[str] = []
    problem_number, solution_number = _number(problem), _number(solution)
    identifiers_compatible = not (problem_number and solution_number and problem_number != solution_number)

    if problem_number and solution_number and problem_number == solution_number:
        score += _signal(signals, "exact_number", 50, f"{problem_number}={solution_number}")
    elif not identifiers_compatible:
        score += _signal(signals, "explicit_number_mismatch", -60, f"{problem_number}!={solution_number}")
        ambiguity.append("explicit_number_mismatch")
    if _same_set(problem, solution):
        score += _signal(signals, "same_exercise_set", 20, _text(problem.get("exercise_set_id")))
    if bool(structure.get("section_pair_confirmed") or solution.get("section_pair_confirmed")):
        score += _signal(signals, "confirmed_section_pair", 10, True)
    if solution_number and exact_number_count == 1:
        score += _signal(signals, "unique_number", 10, solution_number)
    elif solution_number and exact_number_count > 1:
        score += _signal(signals, "duplicate_number", -15, exact_number_count)
        ambiguity.append("duplicate_problem_number")
    rank_delta = abs(int(problem_rank) - int(solution_rank))
    if rank_delta == 0:
        score += _signal(signals, "monotonic_order", 10, "same_rank")
    elif rank_delta == 1:
        score += _signal(signals, "monotonic_order", 5, "adjacent_rank")

    continuation_complete = bool(solution.get("continuation_complete", True))
    if not continuation_complete:
        score += _signal(signals, "incomplete_continuation", -20, False)
        ambiguity.append("incomplete_continuation")
    score = max(0, min(100, score))
    if not identifiers_compatible:
        score = min(score, 39)
    if not continuation_complete:
        score = min(score, 64)
    source_mapping_confirmed = bool(structure.get("source_mapping_confirmed", True))
    if not source_mapping_confirmed:
        ambiguity.append("source_mapping_unconfirmed")
    return score, signals, ambiguity, {
        "scope_compatible": True,
        "identifiers_compatible": identifiers_compatible,
        "continuation_complete": continuation_complete,
        "source_mapping_confirmed": source_mapping_confirmed,
    }


def _score_interleaved(
    problem: Mapping[str, Any],
    solution: Mapping[str, Any],
    *,
    nearest_preceding_id: str,
    exact_number_count: int,
    source_mapping_confirmed: bool,
) -> tuple[int, list[dict[str, Any]], list[str], dict[str, bool]]:
    score = 0
    signals: list[dict[str, Any]] = []
    ambiguity: list[str] = []
    problem_number, solution_number = _number(problem), _number(solution)
    identifiers_compatible = not (problem_number and solution_number and problem_number != solution_number)

    if problem_number and solution_number and problem_number == solution_number:
        score += _signal(signals, "exact_number", 35, f"{problem_number}={solution_number}")
    elif not identifiers_compatible:
        score += _signal(signals, "explicit_number_mismatch", -60, f"{problem_number}!={solution_number}")
        ambiguity.append("explicit_number_mismatch")
    if _same_set(problem, solution):
        score += _signal(signals, "same_exercise_set", 15, _text(problem.get("exercise_set_id")))
    if solution_number and exact_number_count == 1:
        score += _signal(signals, "unique_number", 20, solution_number)
    elif solution_number and exact_number_count > 1:
        score += _signal(signals, "duplicate_number", -15, exact_number_count)
        ambiguity.append("duplicate_problem_number")

    problem_id = _unit_id(problem, fallback_prefix="problem")
    if problem_id == nearest_preceding_id:
        score += _signal(signals, "immediate_preceding_problem", 25, problem_id)
        score += _signal(signals, "no_intervening_problem", 10, True)
    problem_span, solution_span = _page_span(problem), _page_span(solution)
    page_gap = solution_span[0] - problem_span[1]
    if _same_column(problem, solution) or 0 <= page_gap <= 1:
        score += _signal(signals, "same_column_or_adjacent_page", 10, {"page_gap": page_gap})
    if bool(solution.get("solution_heading") or solution.get("has_solution_marker")):
        score += _signal(signals, "solution_heading", 5, True)

    continuation_complete = bool(solution.get("continuation_complete", True))
    if not continuation_complete:
        score += _signal(signals, "incomplete_continuation", -20, False)
        ambiguity.append("incomplete_continuation")
    score = max(0, min(100, score))
    if not identifiers_compatible:
        score = min(score, 39)
    if not continuation_complete:
        score = min(score, 64)
    if not source_mapping_confirmed:
        ambiguity.append("source_mapping_unconfirmed")
    return score, signals, ambiguity, {
        "scope_compatible": True,
        "identifiers_compatible": identifiers_compatible,
        "continuation_complete": continuation_complete,
        "source_mapping_confirmed": bool(source_mapping_confirmed),
    }


def generate_candidate_links(
    problem_units: Sequence[Mapping[str, Any]],
    solution_units: Sequence[Mapping[str, Any]],
    *,
    pattern: str,
    exercise_set_id: str = "",
    source_mapping_confirmed: bool = False,
    structure: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate one deterministic best-candidate decision per solution unit."""

    normalized_pattern = _normalized_pattern(pattern)
    structure = _mapping(structure)
    structure.setdefault("source_mapping_confirmed", bool(source_mapping_confirmed))
    default_set = _text(exercise_set_id)
    solution_selection = _mapping(structure.get("solution_page_selection"))
    selection_configured = bool(
        structure.get("solution_page_selection_configured")
        or solution_selection.get("configured")
    )
    raw_selected_solution_pages = (
        structure.get("solution_selected_pages")
        or solution_selection.get("selected_pages")
        or []
    )
    selected_solution_pages: set[int] = set()
    for raw_page in list(raw_selected_solution_pages or []):
        try:
            page = int(raw_page)
        except (TypeError, ValueError):
            continue
        if page > 0:
            selected_solution_pages.add(page)

    def with_default_set(row: Mapping[str, Any]) -> dict[str, Any]:
        clean = _mapping(row)
        if default_set and not _text(clean.get("exercise_set_id")):
            clean["exercise_set_id"] = default_set
        return clean

    problem_rows = sorted((with_default_set(row) for row in problem_units), key=_reading_key)
    solution_rows = sorted((with_default_set(row) for row in solution_units), key=_reading_key)
    if selection_configured:
        solution_rows = [
            row
            for row in solution_rows
            if any(page in selected_solution_pages for page in range(_page_span(row)[0], _page_span(row)[1] + 1))
        ]
    problem_rank_by_id = {
        _unit_id(problem, fallback_prefix="problem"): index
        for index, problem in enumerate(problem_rows)
    }
    problem_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    incomplete_problem_scope = False
    problems_by_number: dict[str, list[dict[str, Any]]] = {}
    for problem in problem_rows:
        signature = _scope_signature(problem)
        if all(signature):
            problem_groups.setdefault(signature, []).append(problem)
        else:
            incomplete_problem_scope = True
        number = _number(problem)
        if number:
            problems_by_number.setdefault(number, []).append(problem)
    compatible_scope_cache: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    compatible_scope_keys_cache: dict[tuple[str, str, str], list[tuple[float, float, float, str]]] = {}

    def compatible_problems(solution: Mapping[str, Any]) -> list[dict[str, Any]]:
        signature = _scope_signature(solution)
        cached = compatible_scope_cache.get(signature)
        if cached is not None:
            return cached
        rows: list[dict[str, Any]] = list(problem_groups.get(signature) or []) if all(signature) else []
        rows.sort(key=lambda row: problem_rank_by_id[_unit_id(row, fallback_prefix="problem")])
        compatible_scope_cache[signature] = rows
        compatible_scope_keys_cache[signature] = [_reading_key(row) for row in rows]
        return rows
    results: list[dict[str, Any]] = []

    for solution_rank, solution in enumerate(solution_rows):
        missing_solution_scope = _missing_scope_fields(solution)
        scoped = compatible_problems(solution)
        if not scoped:
            ambiguous_scope = bool(missing_solution_scope or incomplete_problem_scope)
            reasons = [f"missing_solution_scope:{key}" for key in missing_solution_scope]
            if incomplete_problem_scope:
                reasons.append("incomplete_problem_scope")
            if not reasons:
                reasons.append("no_problem_in_scope")
            results.append(
                {
                    "schema_version": CANDIDATE_LINK_SCHEMA_VERSION,
                    "candidate_link_id": _candidate_id(normalized_pattern, solution, None),
                    "pattern": normalized_pattern,
                    "relation_kind": _text(solution.get("relation_kind")) or "one_to_one",
                    "problem_ref": None,
                    "solution_ref": _ref(solution, role="solution"),
                    "signals": [],
                    "score": 0,
                    "runner_up_score": 0,
                    "score_margin": 0,
                    "status": "review_required" if ambiguous_scope else "orphan",
                    "gates": {
                        "scope_compatible": False,
                        "scope_complete": not ambiguous_scope,
                        "source_mapping_confirmed": bool(structure.get("source_mapping_confirmed", False)),
                    },
                    "ambiguity_reasons": reasons,
                }
            )
            continue

        solution_number = _number(solution)
        exact_problems = [
            problem
            for problem in problems_by_number.get(solution_number, [])
            if _scope_compatible(problem, solution)
        ] if solution_number else []
        exact_count = len(exact_problems)
        scope_keys = compatible_scope_keys_cache[_scope_signature(solution)]
        preceding_index = bisect_left(scope_keys, _reading_key(solution)) - 1
        nearest_preceding = scoped[preceding_index] if preceding_index >= 0 else None
        nearest_preceding_id = _unit_id(nearest_preceding, fallback_prefix="problem") if nearest_preceding else ""
        candidate_pool = list(exact_problems) if exact_problems else list(scoped)
        if normalized_pattern == "interleaved" and nearest_preceding is not None:
            nearest_id = _unit_id(nearest_preceding, fallback_prefix="problem")
            if all(_unit_id(problem, fallback_prefix="problem") != nearest_id for problem in candidate_pool):
                candidate_pool.append(nearest_preceding)
        scored: list[dict[str, Any]] = []
        for problem in candidate_pool:
            problem_rank = problem_rank_by_id[_unit_id(problem, fallback_prefix="problem")]
            if normalized_pattern == "separate_sections":
                score, signals, ambiguity, gates = _score_separate(
                    problem,
                    solution,
                    structure=structure,
                    problem_rank=problem_rank,
                    solution_rank=solution_rank,
                    exact_number_count=exact_count,
                )
            else:
                score, signals, ambiguity, gates = _score_interleaved(
                    problem,
                    solution,
                    nearest_preceding_id=nearest_preceding_id,
                    exact_number_count=exact_count,
                    source_mapping_confirmed=bool(structure.get("source_mapping_confirmed", True)),
                )
            scored.append(
                {
                    "problem": problem,
                    "score": score,
                    "signals": signals,
                    "ambiguity": ambiguity,
                    "gates": gates,
                }
            )
        scored.sort(key=lambda row: (-int(row["score"]), _unit_id(row["problem"], fallback_prefix="problem")))
        top = scored[0]
        runner_up = int(scored[1]["score"]) if len(scored) > 1 else 0
        score = int(top["score"])
        margin = score - runner_up
        ambiguity = list(dict.fromkeys(str(item) for item in top["ambiguity"]))
        gates = dict(top["gates"])
        gates["scope_complete"] = True
        relation_kind = _text(solution.get("relation_kind")) or "one_to_one"

        if not gates.get("identifiers_compatible", True):
            status = "conflict"
        elif margin < 10 and len(scored) > 1:
            status = "conflict"
            ambiguity.append("top_score_tie")
        elif score >= 85 and margin >= 20 and all(gates.values()):
            status = "high_confidence"
        elif score >= 65:
            status = "review_required"
        elif score >= 40:
            status = "weak"
        else:
            status = "orphan"
        if relation_kind == "shared_solution" and status == "high_confidence":
            status = "review_required"
            ambiguity.append("shared_solution_requires_human_review")

        problem = top["problem"]
        results.append(
            {
                "schema_version": CANDIDATE_LINK_SCHEMA_VERSION,
                "candidate_link_id": _candidate_id(normalized_pattern, solution, problem),
                "pattern": normalized_pattern,
                "relation_kind": relation_kind,
                "problem_ref": _ref(problem, role="problem"),
                "solution_ref": _ref(solution, role="solution"),
                "signals": list(top["signals"]),
                "score": score,
                "runner_up_score": runner_up,
                "score_margin": margin,
                "status": status,
                "gates": gates,
                "ambiguity_reasons": list(dict.fromkeys(ambiguity)),
            }
        )
    return results


def project_problem_units(records: Sequence[Any], context: Mapping[str, Any] | Any = None) -> list[dict[str, Any]]:
    """Project staging records into the stable, pure linker input contract."""

    if hasattr(context, "to_dict"):
        context = context.to_dict()
    context_row = _mapping(context)
    structure_row = _mapping(context_row.get("problem_solution_structure"))
    default_exercise_set = _text(structure_row.get("exercise_set_id"))
    problem_selection_configured = bool(context_row.get("page_selection_configured"))
    selected_problem_pages: set[int] = set()
    for raw_page in list(context_row.get("selected_pages") or []):
        try:
            page = int(raw_page)
        except (TypeError, ValueError):
            continue
        if page > 0:
            selected_problem_pages.add(page)
    units: list[dict[str, Any]] = []
    for raw_record in records:
        if hasattr(raw_record, "to_dict"):
            record = raw_record.to_dict()
        else:
            record = _mapping(raw_record)
        source = _mapping(record.get("source"))
        normalized = _mapping(record.get("normalized"))
        continuation = _mapping(normalized.get("continuacion"))
        if bool(continuation.get("es_continuacion") or continuation.get("is_continuation")):
            continue
        record_id = _text(record.get("record_id") or record.get("crop_id"))
        if not record_id:
            continue
        final_latex = _text(normalized.get("latex_rendered_item"))
        number = _number(
            {
                "number_normalized": source.get("problem_number"),
                "number_raw": final_latex,
            }
        )
        span = _page_span(source)
        if problem_selection_configured and not any(
            page in selected_problem_pages for page in range(span[0], span[1] + 1)
        ):
            continue
        units.append(
            {
                "unit_id": record_id,
                "record_id": record_id,
                "crop_id": _text(record.get("crop_id")) or record_id,
                "crop_path": _text(record.get("crop_path")),
                "book_code": _text(source.get("book_code") or context_row.get("book_code")),
                "instance_type": _text(source.get("instance_type") or context_row.get("instance_type")),
                "exercise_set_id": _text(source.get("exercise_set_id")) or default_exercise_set,
                "number_raw": _text(source.get("problem_number")) or number,
                "number_normalized": number,
                "page_span": [int(span[0]), int(span[1])],
                "page_number": int(span[0]),
                "bbox_px": list(source.get("bbox_px") or []),
                "reading_order": source.get("source_order") or source.get("box_index") or 0,
                "column": source.get("column") or source.get("column_index") or "",
                "source_fingerprint": problem_source_fingerprint(record),
            }
        )
    return sorted(units, key=_reading_key)


def review_candidate_link(
    candidate: Mapping[str, Any],
    *,
    action: str,
    problem_unit_id: str = "",
    reviewer: str,
    comment: str = "",
    reviewed_at: str,
) -> dict[str, Any]:
    """Apply a human decision without mutating the detector/linker evidence."""

    clean_action = re.sub(r"[^a-z0-9]+", "_", _text(action).lower()).strip("_")
    aliases = {
        "confirm": "confirmed",
        "confirmed": "confirmed",
        "confirmar": "confirmed",
        "change": "confirmed",
        "changed": "confirmed",
        "cambiar": "confirmed",
        "reject": "rejected",
        "rejected": "rejected",
        "rechazar": "rejected",
        "orphan": "orphan",
        "huerfano": "orphan",
    }
    decision = aliases.get(clean_action)
    if not decision:
        raise ValueError(f"Accion de revision no soportada: {action!r}")
    reviewer_text = _text(reviewer)
    reviewed_at_text = _text(reviewed_at)
    if not reviewer_text or not reviewed_at_text:
        raise ValueError("reviewer y reviewed_at son requeridos")

    reviewed = copy.deepcopy(_mapping(candidate))
    selected_problem = _text(problem_unit_id)
    current_ref = _mapping(reviewed.get("problem_ref"))
    if decision == "confirmed" and not selected_problem:
        selected_problem = _text(current_ref.get("unit_id") or current_ref.get("record_id"))
    if decision == "confirmed" and not selected_problem:
        raise ValueError("problem_unit_id requerido para confirmar el enlace")
    event_seed = {
        "candidate_link_id": _text(reviewed.get("candidate_link_id")),
        "action": clean_action,
        "problem_unit_id": selected_problem,
        "reviewer": reviewer_text,
        "reviewed_at": reviewed_at_text,
    }
    event = {
        "schema_version": "problem_solution_review_event_v1",
        "review_version": "problem_solution_review_event_v1",
        "review_event_id": f"psr_{canonical_payload_fingerprint(event_seed).split(':', 1)[1][:20]}",
        "action": clean_action,
        "status": decision,
        "problem_unit_id": selected_problem,
        "reviewer": reviewer_text,
        "comment": _text(comment),
        "reviewed_at": reviewed_at_text,
        "candidate_evidence_fingerprint": candidate_evidence_fingerprint(reviewed),
    }
    reviewed["human_review"] = event
    reviewed["review_status"] = decision
    reviewed["selected_problem_unit_id"] = selected_problem if decision == "confirmed" else ""
    return reviewed


def retarget_candidate_problem(
    candidate: Mapping[str, Any],
    problem_unit: Mapping[str, Any],
) -> dict[str, Any]:
    """Point a generated candidate at a human-selected problem with fresh evidence."""

    retargeted = copy.deepcopy(_mapping(candidate))
    target = _mapping(problem_unit)
    target_ref = _ref(target, role="problem")
    target_id = _text(target_ref.get("unit_id") or target_ref.get("record_id"))
    if not target_id:
        raise ValueError("El problema de destino no tiene un identificador estable")
    if not _text(target_ref.get("source_fingerprint")):
        raise ValueError("El problema de destino no tiene evidencia versionada")

    current_ref = copy.deepcopy(_mapping(retargeted.get("problem_ref")))
    if current_ref and current_ref != target_ref:
        if not isinstance(retargeted.get("original_problem_ref"), Mapping):
            retargeted["original_problem_ref"] = copy.deepcopy(current_ref)
        history = [
            copy.deepcopy(_mapping(item))
            for item in list(retargeted.get("problem_ref_history") or [])
            if isinstance(item, Mapping)
        ]
        if not history or history[-1] != current_ref:
            history.append(current_ref)
        retargeted["problem_ref_history"] = history
    retargeted["problem_ref"] = target_ref

    # A prior human decision refers to the old evidence.  The caller must create
    # a new review event after retargeting.
    for key in _CANDIDATE_REVIEW_KEYS:
        retargeted.pop(key, None)
    retargeted["candidate_evidence_fingerprint"] = candidate_evidence_fingerprint(retargeted)
    return retargeted


def build_problem_solution_bundle(
    *,
    problem_unit: Mapping[str, Any],
    solution_units: Sequence[Mapping[str, Any]],
    reviewed_links: Sequence[Mapping[str, Any]],
    bundle_id: str = "",
    revision: int = 1,
    document_relation: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a promotable bundle from explicit human-confirmed link decisions."""

    problem = _mapping(problem_unit)
    problem_id = _unit_id(problem, fallback_prefix="problem")
    problem_precision = precision_gate_for_unit(problem)
    if (
        _text(problem.get("annotation_schema_version")) == ANNOTATION_SCHEMA_VERSION
        or isinstance(problem.get("precision_annotation"), Mapping)
    ) and not bool(problem_precision.get("h_ps2_ready")):
        raise ValueError(f"Precision H-PS2 bloqueada para problema {problem_id}: {';'.join(problem_precision.get('issues') or [])}")
    problem_scope = _scope_signature(problem)
    missing_problem_scope = [key for key, value in zip(_SCOPE_KEYS, problem_scope) if not value]
    if missing_problem_scope:
        raise ValueError(f"Alcance incompleto del problema: {','.join(missing_problem_scope)}")
    solutions_by_id = {
        _unit_id(_mapping(unit), fallback_prefix="solution"): _mapping(unit) for unit in solution_units
    }
    confirmed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for raw_link in reviewed_links:
        link = _mapping(raw_link)
        review = _mapping(link.get("human_review"))
        selected_problem = _text(link.get("selected_problem_unit_id") or review.get("problem_unit_id"))
        if _text(review.get("status")) != "confirmed" or selected_problem != problem_id:
            continue
        solution_ref = _mapping(link.get("solution_ref"))
        solution_id = _text(solution_ref.get("unit_id"))
        unit = solutions_by_id.get(solution_id)
        if unit is not None:
            precision_validation = precision_gate_for_unit(unit)
            precision_opt_in = (
                _text(unit.get("annotation_schema_version")) == ANNOTATION_SCHEMA_VERSION
                or isinstance(unit.get("precision_annotation"), Mapping)
            )
            if precision_opt_in and not bool(precision_validation.get("h_ps2_ready")):
                raise ValueError(
                    f"Precision H-PS2 bloqueada para solucion {solution_id}: "
                    + ";".join(str(item) for item in list(precision_validation.get("issues") or []))
                )
            unit_fragments = list(unit.get("fragments") or [])
            continuation_complete = unit.get("continuation_complete")
            if continuation_complete is False or (len(unit_fragments) > 1 and continuation_complete is not True):
                raise ValueError(f"Solucion incompleta {solution_id}")
            ordered_unit_fragments = sorted(
                (_mapping(fragment) for fragment in unit_fragments),
                key=_reading_key,
            )
            ordered_roles = [_text(fragment.get("fragment_role")).lower() for fragment in ordered_unit_fragments]
            if len(ordered_roles) > 1:
                valid_middle_roles = {"middle", "continuation", "continuacion", "continuation_middle"}
                if any(not role for role in ordered_roles):
                    raise ValueError(f"Roles de continuacion requeridos {solution_id}")
                if (
                    ordered_roles[0] not in {"begin", "start", "inicio"}
                    or ordered_roles[-1] not in {"end", "finish", "fin"}
                    or any(role not in valid_middle_roles for role in ordered_roles[1:-1])
                ):
                    raise ValueError(f"Secuencia de continuacion invalida {solution_id}")
            if _scope_signature(unit) != problem_scope:
                raise ValueError(f"Alcance incompatible para la solucion {solution_id}")
            referenced_solution_source = _text(solution_ref.get("source_fingerprint"))
            if referenced_solution_source and referenced_solution_source != unit_source_fingerprint(unit):
                raise ValueError(f"Evidencia obsoleta de la solucion {solution_id}")
            referenced_problem_source = _text(_mapping(link.get("problem_ref")).get("source_fingerprint"))
            current_problem_source = _text(problem.get("source_fingerprint")) or canonical_payload_fingerprint(problem)
            if referenced_problem_source and referenced_problem_source != current_problem_source:
                raise ValueError(f"Evidencia obsoleta del problema {problem_id}")
            confirmed.append((link, unit))
    if not confirmed:
        raise ValueError("No existen enlaces humanos confirmados para el problema")

    confirmed.sort(key=lambda pair: _reading_key(pair[1]))
    solution_rows: list[dict[str, Any]] = []
    reviewer = ""
    reviewed_at = ""
    for index, (link, unit) in enumerate(confirmed, start=1):
        review = _mapping(link.get("human_review"))
        reviewer = reviewer or _text(review.get("reviewer"))
        reviewed_at = max(reviewed_at, _text(review.get("reviewed_at")))
        unit_id = _unit_id(unit, fallback_prefix="solution")
        fragments_raw = unit.get("fragments")
        fragments = list(fragments_raw) if isinstance(fragments_raw, list) else [unit]
        fragments = sorted((_mapping(fragment) for fragment in fragments), key=_reading_key)
        relation_kind = _text(link.get("relation_kind") or unit.get("relation_kind")) or "one_to_one"
        identity = "|".join((problem_id, unit_id, relation_kind))
        solution_rows.append(
            {
                "solution_id": _text(unit.get("solution_id"))
                or f"sol_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}",
                "solution_unit_id": unit_id,
                "relation_kind": relation_kind,
                "variant_index": int(unit.get("variant_index") or index),
                "continuation_complete": bool(unit.get("continuation_complete", len(fragments) == 1)),
                "fragments": [copy.deepcopy(fragment) for fragment in fragments],
                "candidate_link_id": _text(link.get("candidate_link_id")),
                "human_review_event_id": _text(review.get("review_event_id")),
                "source_fingerprint": unit_source_fingerprint(unit),
                "candidate_evidence_fingerprint": candidate_evidence_fingerprint(link),
                "review_fingerprint": candidate_review_fingerprint(link),
                "scope": {
                    "book_code": problem_scope[0],
                    "instance_type": problem_scope[1],
                    "exercise_set_id": problem_scope[2],
                },
                "provenance": copy.deepcopy(_mapping(unit.get("provenance"))),
                "precision_validation": copy.deepcopy(precision_gate_for_unit(unit)),
            }
        )

    clean_bundle_id = _text(bundle_id)
    if not clean_bundle_id:
        seed = "|".join((problem_id, *(row["solution_unit_id"] for row in solution_rows)))
        clean_bundle_id = f"psb_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:20]}"
    problem_ref = {
        "record_id": _text(problem.get("record_id")) or problem_id,
        "crop_id": _text(problem.get("crop_id")),
        "book_code": problem_scope[0],
        "instance_type": problem_scope[1],
        "exercise_set_id": problem_scope[2],
        "number_normalized": _number(problem),
        "source_fingerprint": _text(problem.get("source_fingerprint"))
        or canonical_payload_fingerprint(problem),
    }
    bundle: dict[str, Any] = {
        "schema_version": PROMOTION_BUNDLE_SCHEMA_VERSION,
        "bundle_id": clean_bundle_id,
        "revision": max(1, int(revision)),
        "status": PROMOTABLE_BUNDLE_STATUS,
        "scope": {
            "book_code": problem_scope[0],
            "instance_type": problem_scope[1],
            "exercise_set_id": problem_scope[2],
        },
        "problem_ref": {key: value for key, value in problem_ref.items() if value != ""},
        "solutions": solution_rows,
        "confirmed_link_ids": [row["candidate_link_id"] for row in solution_rows],
        "document_relation": copy.deepcopy(_mapping(document_relation)),
        "human_review": {
            "status": "confirmed",
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
            "review_version": "problem_solution_review_event_v1",
        },
        "provenance": copy.deepcopy(_mapping(provenance)),
    }
    bundle["bundle_fingerprint"] = bundle_fingerprint(bundle)
    return bundle


def group_solution_fragments(fragments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Group already-detected page fragments without performing visual inference."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for raw in fragments:
        fragment = _mapping(raw)
        group_id = _text(fragment.get("continuation_group_id") or fragment.get("solution_unit_id"))
        if not group_id:
            group_id = _unit_id(fragment, fallback_prefix="solution")
        grouped.setdefault(group_id, []).append(fragment)

    units: list[dict[str, Any]] = []
    for group_id in sorted(grouped):
        rows = sorted(grouped[group_id], key=_reading_key)
        roles = {_text(row.get("fragment_role")).lower() for row in rows}
        complete = len(rows) == 1 and (not roles or "single" in roles)
        complete = complete or ("begin" in roles and "end" in roles)
        spans = [_page_span(row) for row in rows]
        units.append(
            {
                "solution_unit_id": group_id,
                "exercise_set_id": _text(rows[0].get("exercise_set_id")),
                "number_normalized": next((_number(row) for row in rows if _number(row)), ""),
                "page_span": [min(span[0] for span in spans), max(span[1] for span in spans)],
                "continuation_complete": bool(complete),
                "fragments": [copy.deepcopy(row) for row in rows],
            }
        )
    return units


def validate_solution_unit(
    unit: Mapping[str, Any],
    *,
    expected_scope: Mapping[str, Any] | None = None,
) -> list[str]:
    """Validate the reviewed unit contract before it can replace staging state."""

    raw = _mapping(unit)
    unit_id = _unit_id(raw, fallback_prefix="solution") if raw else ""
    prefix = f"solution_unit:{unit_id or 'unknown'}"
    issues: list[str] = []
    explicit_id = _text(raw.get("solution_unit_id") or raw.get("unit_id"))
    if not explicit_id:
        issues.append(f"{prefix}:missing_id")

    scope_values = _scope_values(raw)
    for key, value in zip(_SCOPE_KEYS, scope_values):
        if not value:
            issues.append(f"{prefix}:missing_scope:{key}")
    if expected_scope:
        expected = _scope_values(expected_scope)
        for key, actual, wanted in zip(_SCOPE_KEYS, scope_values, expected):
            if wanted and actual and actual != wanted:
                issues.append(f"{prefix}:scope_mismatch:{key}")

    provenance = _mapping(raw.get("provenance"))
    if not provenance:
        issues.append(f"{prefix}:missing_provenance")
    source_version = _text(
        raw.get("source_version")
        or provenance.get("source_version")
        or provenance.get("detector_version")
        or provenance.get("segmentation_version")
    )
    review_version = _text(
        raw.get("review_version")
        or provenance.get("review_version")
        or provenance.get("box_review_version")
    )
    if not source_version:
        issues.append(f"{prefix}:missing_source_version")
    if not review_version:
        issues.append(f"{prefix}:missing_review_version")

    precision_opt_in = (
        _text(raw.get("annotation_schema_version")) == ANNOTATION_SCHEMA_VERSION
        or isinstance(raw.get("precision_annotation"), Mapping)
    )
    if precision_opt_in:
        precision_validation = precision_gate_for_unit(raw)
        if not bool(precision_validation.get("h_ps2_ready")):
            for issue in list(precision_validation.get("issues") or ["precision_annotation:invalid"]):
                issues.append(f"{prefix}:precision_gate:{issue}")

    fragments = raw.get("fragments")
    if not isinstance(fragments, list) or not fragments:
        issues.append(f"{prefix}:missing_fragments")
        return list(dict.fromkeys(issues))
    continuation_complete = raw.get("continuation_complete")
    if continuation_complete is False or (len(fragments) > 1 and continuation_complete is not True):
        issues.append(f"{prefix}:incomplete_continuation")
    fragment_roles = [_text(_mapping(fragment).get("fragment_role")).lower() for fragment in fragments]
    if len(fragments) > 1:
        if any(not role for role in fragment_roles):
            issues.append(f"{prefix}:missing_continuation_roles")
        else:
            if fragment_roles[0] not in {"begin", "start", "inicio"}:
                issues.append(f"{prefix}:invalid_continuation_start")
            if fragment_roles[-1] not in {"end", "finish", "fin"}:
                issues.append(f"{prefix}:invalid_continuation_end")
            if any(role not in {"middle", "continuation", "continuacion", "continuation_middle"} for role in fragment_roles[1:-1]):
                issues.append(f"{prefix}:invalid_continuation_middle")
    seen_fragments: set[str] = set()
    for index, fragment_raw in enumerate(fragments, start=1):
        fragment = _mapping(fragment_raw)
        fragment_prefix = f"{prefix}:fragment_{index}"
        fragment_id = _text(fragment.get("fragment_id"))
        if not fragment_id:
            issues.append(f"{fragment_prefix}:missing_id")
        elif fragment_id in seen_fragments:
            issues.append(f"{fragment_prefix}:duplicate_id")
        seen_fragments.add(fragment_id)
        try:
            page = int(fragment.get("page_number") or 0)
        except (TypeError, ValueError):
            page = 0
        if page < 1:
            issues.append(f"{fragment_prefix}:invalid_page")
        bbox = fragment.get("bbox_px") or fragment.get("bbox_xyxy")
        try:
            values = [float(value) for value in list(bbox or [])]
            if len(values) != 4 or min(values[0], values[1]) < 0 or values[2] <= values[0] or values[3] <= values[1]:
                raise ValueError
        except (TypeError, ValueError):
            issues.append(f"{fragment_prefix}:invalid_bbox")
        if not _text(fragment.get("crop_path")):
            issues.append(f"{fragment_prefix}:missing_crop_path")
        if not _text(fragment.get("sha256") or fragment.get("crop_sha256")):
            issues.append(f"{fragment_prefix}:missing_sha256")
    return list(dict.fromkeys(issues))


def validate_confirmed_bundle(bundle: Mapping[str, Any]) -> list[str]:
    """Validate a promotion bundle's pure contract; asset bytes are checked by staging."""

    raw = _mapping(bundle)
    issues: list[str] = []
    if _text(raw.get("schema_version")) != PROMOTION_BUNDLE_SCHEMA_VERSION:
        issues.append("solution_bundle:invalid_schema_version")
    if not _text(raw.get("bundle_id")):
        issues.append("solution_bundle:missing_bundle_id")
    try:
        revision = int(raw.get("revision") or 0)
    except Exception:
        revision = 0
    if revision < 1:
        issues.append("solution_bundle:invalid_revision")
    if _text(raw.get("status")) != PROMOTABLE_BUNDLE_STATUS:
        issues.append("solution_bundle:not_human_confirmed")
    human_review = _mapping(raw.get("human_review"))
    if _text(human_review.get("status")) not in {"confirmed", "human_confirmed"}:
        issues.append("solution_bundle:missing_human_confirmation")

    problem_ref = _mapping(raw.get("problem_ref"))
    if not _text(problem_ref.get("record_id")):
        issues.append("solution_bundle:missing_problem_record_id")
    if not _text(problem_ref.get("source_fingerprint")):
        issues.append("solution_bundle:missing_problem_source_fingerprint")

    provenance = _mapping(raw.get("provenance"))
    external_required = _text(provenance.get("solution_status")).lower() == "external_source"
    document_relation = _mapping(raw.get("document_relation"))
    relation_is_external = bool(document_relation.get("external"))
    if external_required and not relation_is_external:
        issues.append("solution_bundle:external_document_required")
    if external_required or relation_is_external:
        if _text(document_relation.get("status")) != "confirmed":
            issues.append("solution_bundle:external_document_unconfirmed")
        document_reference = _text(
            document_relation.get("document_id")
            or document_relation.get("document_reference")
            or document_relation.get("source_pdf_id")
            or document_relation.get("source_pdf_path")
        )
        if not document_reference:
            issues.append("solution_bundle:external_document_reference_missing")

    solutions = raw.get("solutions")
    if not isinstance(solutions, list) or not solutions:
        issues.append("solution_bundle:missing_solutions")
        return list(dict.fromkeys(issues))

    seen_solutions: set[str] = set()
    seen_fragments: set[str] = set()
    for solution_index, solution_raw in enumerate(solutions, start=1):
        solution = _mapping(solution_raw)
        prefix = f"solution_bundle:solution_{solution_index}"
        solution_id = _text(solution.get("solution_id"))
        if not solution_id:
            issues.append(f"{prefix}:missing_solution_id")
        elif solution_id in seen_solutions:
            issues.append(f"{prefix}:duplicate_solution_id")
        seen_solutions.add(solution_id)
        if not _text(solution.get("solution_unit_id")):
            issues.append(f"{prefix}:missing_solution_unit_id")
        if solution.get("continuation_complete") is False:
            issues.append(f"{prefix}:incomplete_continuation")
        relation_kind = _text(solution.get("relation_kind")) or "one_to_one"
        if relation_kind not in ALLOWED_RELATION_KINDS:
            issues.append(f"{prefix}:invalid_relation_kind")
        fragments = solution.get("fragments")
        if not isinstance(fragments, list) or not fragments:
            issues.append(f"{prefix}:missing_fragments")
            continue
        fragment_roles = [_text(_mapping(fragment).get("fragment_role")).lower() for fragment in fragments]
        if len(fragments) > 1:
            if solution.get("continuation_complete") is not True:
                issues.append(f"{prefix}:incomplete_continuation")
            if any(not role for role in fragment_roles):
                issues.append(f"{prefix}:missing_continuation_roles")
            else:
                if fragment_roles[0] not in {"begin", "start", "inicio"}:
                    issues.append(f"{prefix}:invalid_continuation_start")
                if fragment_roles[-1] not in {"end", "finish", "fin"}:
                    issues.append(f"{prefix}:invalid_continuation_end")
                if any(
                    role not in {"middle", "continuation", "continuacion", "continuation_middle"}
                    for role in fragment_roles[1:-1]
                ):
                    issues.append(f"{prefix}:invalid_continuation_middle")
        for fragment_index, fragment_raw in enumerate(fragments, start=1):
            fragment = _mapping(fragment_raw)
            fragment_prefix = f"{prefix}:fragment_{fragment_index}"
            fragment_id = _text(fragment.get("fragment_id"))
            if not fragment_id:
                issues.append(f"{fragment_prefix}:missing_fragment_id")
            elif fragment_id in seen_fragments:
                issues.append(f"{fragment_prefix}:duplicate_fragment_id")
            seen_fragments.add(fragment_id)
            if not _text(fragment.get("crop_path")):
                issues.append(f"{fragment_prefix}:missing_crop_path")
            if not _text(fragment.get("sha256")):
                issues.append(f"{fragment_prefix}:missing_sha256")
    return list(dict.fromkeys(issues))


def visual_solution_payloads(bundle: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues = validate_confirmed_bundle(bundle)
    if issues:
        raise ValueError(";".join(issues))
    raw = _mapping(bundle)
    fingerprint = _text(raw.get("bundle_fingerprint")) or bundle_fingerprint(raw)
    payloads: list[dict[str, Any]] = []
    for solution_raw in list(raw.get("solutions") or []):
        solution = _mapping(solution_raw)
        fragments = sorted(
            (_mapping(item) for item in list(solution.get("fragments") or [])),
            key=lambda item: (int(item.get("order") or 0), _page_span(item), _text(item.get("fragment_id"))),
        )
        payloads.append(
            {
                "schema_version": VISUAL_SOLUTION_SCHEMA_VERSION,
                "solution_id": _text(solution.get("solution_id")),
                "solution_unit_id": _text(solution.get("solution_unit_id")),
                "relation_kind": _text(solution.get("relation_kind")) or "one_to_one",
                "variant_index": int(solution.get("variant_index") or 1),
                "images": [_text(fragment.get("crop_path")) for fragment in fragments],
                "fragments": [copy.deepcopy(fragment) for fragment in fragments],
                "candidate_link_id": _text(solution.get("candidate_link_id")),
                "human_review_event_id": _text(solution.get("human_review_event_id")),
                "bundle_id": _text(raw.get("bundle_id")),
                "bundle_fingerprint": fingerprint,
                "source": "problem_solution_bundle",
            }
        )
    return payloads
