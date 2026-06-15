from __future__ import annotations

import re
import unicodedata
from typing import Any


SIMILARITY_MODEL_ID = "semantic_similarity_seed_v1"

TOKEN_RE = re.compile(r"[^\W_]{2,}", re.UNICODE)
LATEX_COMMAND_RE = re.compile(r"\\[a-zA-Z]+")

STOPWORDS = {
    "con",
    "del",
    "desde",
    "dos",
    "el",
    "en",
    "entre",
    "es",
    "la",
    "las",
    "los",
    "para",
    "por",
    "que",
    "se",
    "sin",
    "una",
    "uno",
    "y",
}

DEFAULT_WEIGHTS = {
    "statement": 0.25,
    "figure": 0.10,
    "solution": 0.25,
    "concepts": 0.20,
    "type": 0.10,
    "difficulty": 0.10,
}

COURSE_WEIGHTS = {
    "aritmetica": {
        "statement": 0.30,
        "figure": 0.00,
        "solution": 0.25,
        "concepts": 0.25,
        "type": 0.10,
        "difficulty": 0.10,
    },
    "algebra": {
        "statement": 0.25,
        "figure": 0.00,
        "solution": 0.35,
        "concepts": 0.20,
        "type": 0.10,
        "difficulty": 0.10,
    },
    "geometria": {
        "statement": 0.15,
        "figure": 0.30,
        "solution": 0.30,
        "concepts": 0.15,
        "type": 0.05,
        "difficulty": 0.05,
    },
    "trigonometria": {
        "statement": 0.20,
        "figure": 0.10,
        "solution": 0.35,
        "concepts": 0.20,
        "type": 0.10,
        "difficulty": 0.05,
    },
}


def normalize_text_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents.lower()).strip()


def tokenize_text(value: str) -> set[str]:
    text = normalize_text_key(str(value or ""))
    text = LATEX_COMMAND_RE.sub(" ", text)
    text = text.replace("_", " ")
    tokens = set()
    for token in TOKEN_RE.findall(text):
        if token in STOPWORDS:
            continue
        if token.isdigit():
            continue
        tokens.add(token)
    return tokens


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value is None:
        return []
    return [value]


def _strings_from_nested(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            out.extend(_strings_from_nested(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            out.extend(_strings_from_nested(item))
    elif value is not None and str(value).strip():
        out.append(str(value).strip())
    return out


def _representation(profile: dict[str, Any]) -> dict[str, Any]:
    value = profile.get("representation")
    return value if isinstance(value, dict) else {}


def _difficulty_level(profile: dict[str, Any]) -> int:
    difficulty = profile.get("difficulty")
    if not isinstance(difficulty, dict):
        return 0
    try:
        level = int(difficulty.get("estimated_level") or 0)
    except Exception:
        return 0
    return level if 1 <= level <= 5 else 0


def _figure_profile_text(profile: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("figure_type", "figure_tag"):
        if profile.get(key):
            parts.append(str(profile[key]))
    for key in ("visual_text", "primitives", "formalgeo_candidate"):
        parts.extend(_strings_from_nested(profile.get(key)))
    parts.extend(str(item) for item in _as_list(profile.get("warnings")) if str(item).strip())
    return " ".join(parts)


def _solution_profile_text(profile: dict[str, Any]) -> str:
    rep = _representation(profile)
    parts = [
        str(profile.get("method") or ""),
        str(rep.get("embedding_text") or ""),
        str(profile.get("source", {}).get("solution_source") if isinstance(profile.get("source"), dict) else ""),
    ]
    parts.extend(_strings_from_nested(profile.get("concepts_used")))
    parts.extend(_strings_from_nested(profile.get("skills_used")))
    parts.extend(_strings_from_nested(profile.get("properties_used")))
    evidence = profile.get("evidence")
    if isinstance(evidence, dict):
        parts.append(str(evidence.get("solution_text_latex") or ""))
    return " ".join(part for part in parts if part)


def extract_problem_similarity_features(
    problem_profile: dict[str, Any],
    *,
    figure_profiles: list[dict[str, Any]] | None = None,
    solution_profiles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rep = _representation(problem_profile)
    evidence = problem_profile.get("evidence") if isinstance(problem_profile.get("evidence"), dict) else {}
    course = str(problem_profile.get("course") or "")
    topic = str(problem_profile.get("topic") or "")
    subtopic = str(problem_profile.get("subtopic") or "")
    canonical_type = str(rep.get("canonical_problem_type") or "")

    statement_text = " ".join(
        part
        for part in [
            str(rep.get("statement_embedding_text") or ""),
            str(problem_profile.get("statement_summary") or ""),
            str(rep.get("embedding_text") or ""),
        ]
        if part
    )
    figure_text = " ".join(
        [
            str(rep.get("figure_embedding_text") or ""),
            " ".join(str(item) for item in _as_list(evidence.get("figure_tags")) if str(item).strip()),
            " ".join(_figure_profile_text(profile) for profile in list(figure_profiles or [])),
        ]
    )
    solution_text = " ".join(
        [
            str(rep.get("solution_embedding_text") or ""),
            " ".join(str(item) for item in _as_list(problem_profile.get("solution_methods")) if str(item).strip()),
            " ".join(str(item) for item in _as_list(problem_profile.get("solution_concepts")) if str(item).strip()),
            " ".join(_solution_profile_text(profile) for profile in list(solution_profiles or [])),
        ]
    )

    concept_parts: list[str] = [course, topic, subtopic, canonical_type]
    concept_parts.extend(_strings_from_nested(problem_profile.get("concepts")))
    concept_parts.extend(_strings_from_nested(problem_profile.get("skills")))
    concept_parts.extend(_strings_from_nested(problem_profile.get("objects")))
    concept_parts.extend(_strings_from_nested(problem_profile.get("solution_methods")))
    concept_parts.extend(_strings_from_nested(problem_profile.get("solution_concepts")))
    concept_parts.extend(_strings_from_nested(rep.get("search_keywords")))

    return {
        "problem_id": str(problem_profile.get("problem_id") or "").strip(),
        "course": course,
        "course_key": normalize_text_key(course),
        "topic": topic,
        "topic_key": normalize_text_key(topic),
        "subtopic": subtopic,
        "canonical_type": canonical_type,
        "difficulty_level": _difficulty_level(problem_profile),
        "statement_tokens": tokenize_text(statement_text),
        "figure_tokens": tokenize_text(figure_text),
        "solution_tokens": tokenize_text(solution_text),
        "concept_tokens": tokenize_text(" ".join(concept_parts)),
        "has_figure_signal": bool(tokenize_text(figure_text)),
        "has_solution_signal": bool(tokenize_text(solution_text)),
    }


def _weights_for_course(course_key: str) -> dict[str, float]:
    for key, weights in COURSE_WEIGHTS.items():
        if key in course_key:
            return dict(weights)
    return dict(DEFAULT_WEIGHTS)


def _difficulty_similarity(left: int, right: int) -> float:
    if left <= 0 or right <= 0:
        return 0.0
    return max(0.0, 1.0 - (abs(left - right) / 4.0))


def _type_similarity(left: str, right: str) -> float:
    left_key = normalize_text_key(left)
    right_key = normalize_text_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == "semilla_por_revisar" and right_key == "semilla_por_revisar":
        return 0.0
    return 1.0 if left_key == right_key else 0.0


def _active_weights(components: dict[str, float], weights: dict[str, float], left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    active: dict[str, float] = {}
    for key, weight in weights.items():
        if weight <= 0:
            continue
        if key == "figure" and not (left["has_figure_signal"] or right["has_figure_signal"]):
            continue
        if key == "solution" and not (left["has_solution_signal"] or right["has_solution_signal"]):
            continue
        if key == "type" and components[key] <= 0:
            continue
        active[key] = weight
    total = sum(active.values())
    if total <= 0:
        return {}
    return {key: value / total for key, value in active.items()}


def score_problem_similarity(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    if not left.get("problem_id") or not right.get("problem_id"):
        raise ValueError("Ambos perfiles deben tener problem_id.")
    if str(left["problem_id"]) == str(right["problem_id"]):
        raise ValueError("No se puede comparar un problema consigo mismo.")

    components = {
        "statement": jaccard(left["statement_tokens"], right["statement_tokens"]),
        "figure": jaccard(left["figure_tokens"], right["figure_tokens"]),
        "solution": jaccard(left["solution_tokens"], right["solution_tokens"]),
        "concepts": jaccard(left["concept_tokens"], right["concept_tokens"]),
        "type": _type_similarity(str(left.get("canonical_type") or ""), str(right.get("canonical_type") or "")),
        "difficulty": _difficulty_similarity(int(left.get("difficulty_level") or 0), int(right.get("difficulty_level") or 0)),
    }
    weights = _active_weights(components, _weights_for_course(str(left.get("course_key") or "")), left, right)
    score = sum(components[key] * weights.get(key, 0.0) for key in components)
    if left.get("course_key") and right.get("course_key") and left["course_key"] != right["course_key"]:
        score *= 0.65

    shared_concepts = sorted((left["concept_tokens"] & right["concept_tokens"]))[:12]
    shared_statement = sorted((left["statement_tokens"] & right["statement_tokens"]))[:8]
    reason_parts: list[str] = []
    if left.get("course_key") and left.get("course_key") == right.get("course_key"):
        reason_parts.append(f"mismo curso: {left.get('course')}")
    if left.get("topic_key") and left.get("topic_key") == right.get("topic_key"):
        reason_parts.append(f"mismo tema: {left.get('topic')}")
    if shared_concepts:
        reason_parts.append("conceptos compartidos: " + ", ".join(shared_concepts[:5]))
    if components["solution"] >= 0.25:
        reason_parts.append("solucion/metodo cercano")
    if components["figure"] >= 0.25:
        reason_parts.append("grafico cercano")
    if components["statement"] >= 0.25 and shared_statement:
        reason_parts.append("enunciado cercano")
    if not reason_parts:
        reason_parts.append("similitud semilla baja; requiere revision")

    return {
        "source_problem_id": str(left["problem_id"]),
        "target_problem_id": str(right["problem_id"]),
        "score": round(float(score), 6),
        "components": {
            key: round(float(value), 6)
            for key, value in components.items()
        },
        "weights": {
            key: round(float(value), 6)
            for key, value in weights.items()
        },
        "shared_concepts": shared_concepts,
        "reason": "; ".join(reason_parts),
    }


def rank_similar_problems(
    features: list[dict[str, Any]],
    *,
    top_k: int = 5,
    threshold: float = 0.15,
) -> list[dict[str, Any]]:
    top_k = max(1, int(top_k or 1))
    threshold = max(0.0, float(threshold or 0.0))
    edges: list[dict[str, Any]] = []
    for source in features:
        scored: list[dict[str, Any]] = []
        for target in features:
            if str(source.get("problem_id") or "") == str(target.get("problem_id") or ""):
                continue
            try:
                edge = score_problem_similarity(source, target)
            except ValueError:
                continue
            if edge["score"] >= threshold:
                scored.append(edge)
        scored.sort(key=lambda row: (-float(row["score"]), int(row["target_problem_id"]) if str(row["target_problem_id"]).isdigit() else str(row["target_problem_id"])))
        edges.extend(scored[:top_k])
    return edges
