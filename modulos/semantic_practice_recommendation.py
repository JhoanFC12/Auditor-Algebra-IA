from __future__ import annotations

import re
from typing import Any

from .semantic_similarity_review import fetch_problem_similarity_review
from .semantic_similarity_seed import SIMILARITY_MODEL_ID


PRACTICE_DRAFT_SCHEMA_VERSION = "semantic_practice_draft_v1"
ITEM_PREFIX_RE = re.compile(r"^\s*\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\]\s*")


def _status(value: Any) -> str:
    return str(value or "").strip().lower() or "sin_revisar"


def _recommendation_label(row: dict[str, Any]) -> str:
    status = _status(row.get("status"))
    score = float(row.get("score") or 0.0)
    if status == "aceptado":
        return "refuerzo_validado"
    if status == "dudoso":
        return "segunda_revision"
    if score >= 0.78:
        return "refuerzo_directo"
    if score >= 0.55:
        return "practica_guiada"
    return "extension"


def _priority(row: dict[str, Any]) -> tuple[int, float, int]:
    status = _status(row.get("status"))
    verified = bool(row.get("human_verified"))
    status_rank = {
        "aceptado": 0,
        "sin_revisar": 1,
        "dudoso": 2,
        "rechazado": 9,
    }.get(status, 3)
    if verified and status == "aceptado":
        status_rank = -1
    return (status_rank, -float(row.get("score") or 0.0), int(row.get("target_problem_id") or 0))


def build_semantic_practice_draft(
    similarity_payload: dict[str, Any],
    *,
    target_count: int = 10,
    include_rejected: bool = False,
) -> dict[str, Any]:
    target_count = max(1, min(int(target_count or 10), 50))
    base = dict(similarity_payload.get("problem") or {})
    rows = list(similarity_payload.get("similar") or [])
    if not include_rejected:
        rows = [row for row in rows if _status(row.get("status")) != "rechazado"]
    rows.sort(key=_priority)
    selected = rows[:target_count]
    recommendations = []
    for index, row in enumerate(selected, start=1):
        problem = dict(row.get("problem") or {})
        recommendations.append(
            {
                "order": index,
                "problem_id": int(problem.get("id") or row.get("target_problem_id") or 0),
                "role": _recommendation_label(row),
                "score": float(row.get("score") or 0.0),
                "status": _status(row.get("status")),
                "human_verified": bool(row.get("human_verified")),
                "reason": str(row.get("reason") or ""),
                "problem": problem,
                "similarity": {
                    "source_problem_id": int(row.get("edge_problem_id") or row.get("source_problem_id") or 0),
                    "target_problem_id": int(row.get("edge_similar_problem_id") or row.get("target_problem_id") or 0),
                    "score_components": row.get("score_components") if isinstance(row.get("score_components"), dict) else {},
                },
            }
        )
    latex_items = [format_practice_latex_item(row["problem"], row["order"]) for row in recommendations]
    return {
        "schema_version": PRACTICE_DRAFT_SCHEMA_VERSION,
        "seed_problem_id": int(similarity_payload.get("problem_id") or base.get("id") or 0),
        "model_id": str(similarity_payload.get("model_id") or SIMILARITY_MODEL_ID),
        "title": _practice_title(base),
        "objective": _practice_objective(base),
        "seed_problem": base,
        "recommendations": recommendations,
        "practice_latex_items": latex_items,
        "practice_latex": build_practice_latex_block(_practice_title(base), latex_items),
        "count": len(recommendations),
        "excluded": {
            "rejected": len([row for row in list(similarity_payload.get("similar") or []) if _status(row.get("status")) == "rechazado"]),
            "not_selected": max(0, len(rows) - len(selected)),
        },
        "policy": {
            "read_only": True,
            "requires_teacher_review_before_student_use": True,
            "source": "problem_similarity_edges",
        },
    }


def format_practice_latex_item(problem: dict[str, Any], order: int) -> str:
    body = str(problem.get("enunciado_latex") or "").strip()
    if not body:
        problem_id = str(problem.get("id") or "").strip() or "sin_id"
        body = f"% Problema {problem_id} sin enunciado_latex"
    prefix = f"\\item[\\textbf{{{max(1, int(order or 1))}.}}] "
    if ITEM_PREFIX_RE.search(body):
        return ITEM_PREFIX_RE.sub(lambda _match: prefix, body, count=1).strip()
    return f"{prefix}{body}".strip()


def build_practice_latex_block(title: str, latex_items: list[str]) -> str:
    clean_title = str(title or "Practica de refuerzo por similitud").strip()
    lines = [
        f"% {clean_title}",
        "% Borrador generado desde relaciones semanticas; revisar antes de usar con alumnos.",
        r"\begin{enumerate}",
    ]
    lines.extend(str(item).strip() for item in latex_items if str(item).strip())
    lines.append(r"\end{enumerate}")
    return "\n".join(lines).strip()


def _practice_title(problem: dict[str, Any]) -> str:
    course = str(problem.get("curso") or "").strip()
    topic = str(problem.get("tema") or "").strip()
    if course and topic:
        return f"Practica de refuerzo: {course} / {topic}"
    if topic:
        return f"Practica de refuerzo: {topic}"
    if course:
        return f"Practica de refuerzo: {course}"
    return "Practica de refuerzo por similitud"


def _practice_objective(problem: dict[str, Any]) -> str:
    parts = [str(problem.get("curso") or "").strip(), str(problem.get("tema") or "").strip(), str(problem.get("subtema") or "").strip()]
    label = " / ".join(part for part in parts if part)
    if label:
        return f"Reforzar problemas que comparten conceptos, propiedades o rutas de solucion con {label}."
    return "Reforzar problemas que comparten conceptos, propiedades o rutas de solucion con el problema semilla."


def fetch_semantic_practice_draft(
    conn: Any,
    *,
    problem_id: int,
    top_k: int = 20,
    target_count: int = 10,
    model_id: str = SIMILARITY_MODEL_ID,
    include_reverse: bool = True,
    include_rejected: bool = False,
) -> dict[str, Any]:
    payload = fetch_problem_similarity_review(
        conn,
        problem_id=int(problem_id),
        top_k=max(int(top_k or target_count or 10), int(target_count or 10)),
        model_id=model_id,
        include_reverse=include_reverse,
    )
    return build_semantic_practice_draft(
        payload,
        target_count=target_count,
        include_rejected=include_rejected,
    )
