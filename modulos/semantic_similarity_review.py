from __future__ import annotations

import json
from typing import Any

from .semantic_profile_db import normalize_similarity_review_status, table_columns, table_exists
from .semantic_similarity_seed import SIMILARITY_MODEL_ID


PROBLEM_COLUMNS = (
    "id",
    "numero_original",
    "enunciado_latex",
    "curso",
    "tema",
    "subtema",
    "respuesta_correcta",
    "respuesta",
    "archivo_origen",
    "nivel_dificultad",
    "consistencia_matematica",
)

SIMILARITY_FEEDBACK_SCHEMA_VERSION = "semantic_similarity_feedback_example_v1"
SIMILARITY_FEEDBACK_EXPORT_SCHEMA_VERSION = "semantic_similarity_feedback_export_v1"


def _decode_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return None
    return value


def _problem_select_expr(alias: str, columns: set[str]) -> tuple[str, list[str]]:
    selected: list[str] = []
    exprs: list[str] = []
    for column in PROBLEM_COLUMNS:
        selected.append(column)
        if column in columns:
            exprs.append(f"{alias}.{column}")
        else:
            exprs.append(f"NULL AS {column}")
    return ", ".join(exprs), selected


def _problem_from_row(headers: list[str], row: tuple[Any, ...]) -> dict[str, Any]:
    payload = dict(zip(headers, row))
    return {
        "id": int(payload.get("id") or 0),
        "numero_original": payload.get("numero_original"),
        "enunciado_latex": str(payload.get("enunciado_latex") or ""),
        "curso": str(payload.get("curso") or ""),
        "tema": str(payload.get("tema") or ""),
        "subtema": str(payload.get("subtema") or ""),
        "respuesta": str(payload.get("respuesta_correcta") or payload.get("respuesta") or ""),
        "archivo_origen": str(payload.get("archivo_origen") or ""),
        "nivel_dificultad": payload.get("nivel_dificultad"),
        "consistencia_matematica": str(payload.get("consistencia_matematica") or ""),
    }


def _prefixed_problem_select_expr(alias: str, prefix: str, columns: set[str]) -> tuple[str, list[str]]:
    selected: list[str] = []
    exprs: list[str] = []
    for column in PROBLEM_COLUMNS:
        selected.append(column)
        if column in columns:
            exprs.append(f"{alias}.{column} AS {prefix}_{column}")
        else:
            exprs.append(f"NULL AS {prefix}_{column}")
    return ", ".join(exprs), selected


def _problem_from_prefixed_row(raw: dict[str, Any], prefix: str, headers: list[str]) -> dict[str, Any]:
    row = tuple(raw.get(f"{prefix}_{column}") for column in headers)
    return _problem_from_row(headers, row)


def _feedback_label(status: str) -> str:
    normalized = normalize_similarity_review_status(status)
    return {
        "aceptado": "positive",
        "rechazado": "negative",
        "dudoso": "uncertain",
    }.get(normalized, "pending")


def _feedback_pair_text(source: dict[str, Any], target: dict[str, Any], reason: str) -> str:
    parts = [
        f"SOURCE[{source.get('curso') or ''}/{source.get('tema') or ''}]: {source.get('enunciado_latex') or ''}",
        f"TARGET[{target.get('curso') or ''}/{target.get('tema') or ''}]: {target.get('enunciado_latex') or ''}",
    ]
    if reason:
        parts.append(f"REASON: {reason}")
    return "\n".join(part.strip() for part in parts if part.strip())


def fetch_problem_summary(conn: Any, problem_id: int) -> dict[str, Any]:
    columns = table_columns(conn, "problemas")
    if "id" not in columns:
        raise RuntimeError("La tabla problemas debe tener columna id.")
    select_expr, headers = _problem_select_expr("p", columns)
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT {select_expr} FROM problemas p WHERE p.id = %s;", (int(problem_id),))
        row = cur.fetchone()
        if not row:
            return {}
        return _problem_from_row(headers, row)
    finally:
        close = getattr(cur, "close", None)
        if callable(close):
            close()


def _edge_rows(
    conn: Any,
    *,
    problem_id: int,
    model_id: str,
    top_k: int,
    reverse: bool,
) -> list[dict[str, Any]]:
    problem_columns = table_columns(conn, "problemas")
    edge_columns = table_columns(conn, "problem_similarity_edges")
    select_expr, problem_headers = _problem_select_expr("p", problem_columns)
    review_note_expr = "e.review_note" if "review_note" in edge_columns else "NULL AS review_note"
    reviewed_at_expr = "e.reviewed_at" if "reviewed_at" in edge_columns else "NULL AS reviewed_at"
    if reverse:
        where = "e.similar_problema_id = %s"
        join = "p.id = e.problema_id"
        source_expr = "e.similar_problema_id"
        target_expr = "e.problema_id"
    else:
        where = "e.problema_id = %s"
        join = "p.id = e.similar_problema_id"
        source_expr = "e.problema_id"
        target_expr = "e.similar_problema_id"
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            SELECT
                {source_expr} AS source_problem_id,
                {target_expr} AS target_problem_id,
                e.problema_id AS edge_problem_id,
                e.similar_problema_id AS edge_similar_problem_id,
                e.score,
                e.score_components,
                e.reason,
                e.model_id,
                e.status,
                e.human_verified,
                {review_note_expr},
                {reviewed_at_expr},
                {select_expr}
            FROM problem_similarity_edges e
            JOIN problemas p ON {join}
            WHERE {where} AND e.model_id = %s
            ORDER BY e.score DESC, p.id ASC
            LIMIT %s;
            """,
            (int(problem_id), str(model_id or SIMILARITY_MODEL_ID), int(top_k)),
        )
        headers = [str(desc[0]) for desc in cur.description]
        rows = []
        for row in cur.fetchall():
            raw = dict(zip(headers, row))
            problem_row = tuple(raw.get(column) for column in problem_headers)
            score_components = _decode_json(raw.get("score_components"))
            rows.append(
                {
                    "source_problem_id": int(raw.get("source_problem_id") or 0),
                    "target_problem_id": int(raw.get("target_problem_id") or 0),
                    "edge_problem_id": int(raw.get("edge_problem_id") or raw.get("source_problem_id") or 0),
                    "edge_similar_problem_id": int(raw.get("edge_similar_problem_id") or raw.get("target_problem_id") or 0),
                    "score": float(raw.get("score") or 0.0),
                    "score_components": score_components if isinstance(score_components, dict) else {},
                    "reason": str(raw.get("reason") or ""),
                    "model_id": str(raw.get("model_id") or ""),
                    "status": str(raw.get("status") or ""),
                    "human_verified": bool(raw.get("human_verified")),
                    "review_note": str(raw.get("review_note") or ""),
                    "reviewed_at": str(raw.get("reviewed_at") or ""),
                    "direction": "reverse" if reverse else "forward",
                    "problem": _problem_from_row(problem_headers, problem_row),
                }
            )
        return rows
    finally:
        close = getattr(cur, "close", None)
        if callable(close):
            close()


def fetch_problem_similarity_review(
    conn: Any,
    *,
    problem_id: int,
    top_k: int = 10,
    model_id: str = SIMILARITY_MODEL_ID,
    include_reverse: bool = False,
) -> dict[str, Any]:
    problem_id = int(problem_id)
    if problem_id <= 0:
        raise ValueError("problem_id debe ser mayor que cero.")
    top_k = max(1, min(int(top_k or 10), 100))
    model_id = str(model_id or SIMILARITY_MODEL_ID).strip() or SIMILARITY_MODEL_ID
    if not table_exists(conn, "problem_similarity_edges"):
        return {
            "schema_version": "problem_similarity_review_v1",
            "problem_id": problem_id,
            "model_id": model_id,
            "problem": fetch_problem_summary(conn, problem_id),
            "similar": [],
            "count": 0,
            "message": "La tabla problem_similarity_edges no existe todavia. Ejecuta el poblador semilla primero.",
        }
    base = fetch_problem_summary(conn, problem_id)
    if not base:
        raise FileNotFoundError(f"Problema no encontrado: {problem_id}")
    rows = _edge_rows(conn, problem_id=problem_id, model_id=model_id, top_k=top_k, reverse=False)
    if include_reverse and len(rows) < top_k:
        extra = _edge_rows(conn, problem_id=problem_id, model_id=model_id, top_k=top_k, reverse=True)
        seen = {(int(row["target_problem_id"]), str(row["direction"])) for row in rows}
        for row in extra:
            key = (int(row["target_problem_id"]), str(row["direction"]))
            if key in seen:
                continue
            rows.append(row)
            seen.add(key)
            if len(rows) >= top_k:
                break
        rows.sort(key=lambda row: (-float(row.get("score") or 0.0), int(row.get("target_problem_id") or 0)))
        rows = rows[:top_k]
    return {
        "schema_version": "problem_similarity_review_v1",
        "problem_id": problem_id,
        "model_id": model_id,
        "problem": base,
        "similar": rows,
        "count": len(rows),
        "message": "" if rows else "No hay similares calculados para este problema con el modelo seleccionado.",
    }


def fetch_similarity_feedback_examples(
    conn: Any,
    *,
    model_id: str = SIMILARITY_MODEL_ID,
    statuses: list[str] | tuple[str, ...] | None = None,
    limit: int = 0,
) -> list[dict[str, Any]]:
    model_id = str(model_id or SIMILARITY_MODEL_ID).strip() or SIMILARITY_MODEL_ID
    selected_statuses = [normalize_similarity_review_status(item) for item in (statuses or ["aceptado", "rechazado", "dudoso"])]
    selected_statuses = [item for item in dict.fromkeys(selected_statuses) if item != "sin_revisar"]
    if not selected_statuses:
        return []
    if not table_exists(conn, "problem_similarity_edges") or not table_exists(conn, "problemas"):
        return []
    edge_columns = table_columns(conn, "problem_similarity_edges")
    required = {"problema_id", "similar_problema_id", "score", "score_components", "reason", "model_id", "status", "human_verified"}
    if not required.issubset(edge_columns):
        return []
    problem_columns = table_columns(conn, "problemas")
    source_expr, source_headers = _prefixed_problem_select_expr("src", "source", problem_columns)
    target_expr, target_headers = _prefixed_problem_select_expr("tgt", "target", problem_columns)
    review_note_expr = "e.review_note" if "review_note" in edge_columns else "NULL AS review_note"
    reviewed_at_expr = "e.reviewed_at" if "reviewed_at" in edge_columns else "NULL AS reviewed_at"
    order_expr = "e.reviewed_at DESC NULLS LAST" if "reviewed_at" in edge_columns else "e.problema_id ASC"
    sql = f"""
        SELECT
            e.problema_id AS edge_problem_id,
            e.similar_problema_id AS edge_similar_problem_id,
            e.score,
            e.score_components,
            e.reason,
            e.model_id,
            e.status,
            e.human_verified,
            {review_note_expr},
            {reviewed_at_expr},
            {source_expr},
            {target_expr}
        FROM problem_similarity_edges e
        JOIN problemas src ON src.id = e.problema_id
        JOIN problemas tgt ON tgt.id = e.similar_problema_id
        WHERE e.model_id = %s
          AND e.human_verified = TRUE
          AND e.status = ANY(%s)
        ORDER BY {order_expr}, e.problema_id ASC, e.similar_problema_id ASC
    """
    params: list[Any] = [model_id, selected_statuses]
    if int(limit or 0) > 0:
        sql += " LIMIT %s"
        params.append(int(limit))
    cur = conn.cursor()
    try:
        cur.execute(sql + ";", tuple(params))
        headers = [str(desc[0]) for desc in cur.description]
        examples = []
        for row in cur.fetchall():
            raw = dict(zip(headers, row))
            source = _problem_from_prefixed_row(raw, "source", source_headers)
            target = _problem_from_prefixed_row(raw, "target", target_headers)
            status = normalize_similarity_review_status(str(raw.get("status") or ""))
            reason = str(raw.get("reason") or "")
            score_components = _decode_json(raw.get("score_components"))
            examples.append(
                {
                    "schema_version": SIMILARITY_FEEDBACK_SCHEMA_VERSION,
                    "label": _feedback_label(status),
                    "status": status,
                    "source_problem_id": int(raw.get("edge_problem_id") or 0),
                    "target_problem_id": int(raw.get("edge_similar_problem_id") or 0),
                    "model_id": str(raw.get("model_id") or model_id),
                    "score": float(raw.get("score") or 0.0),
                    "score_components": score_components if isinstance(score_components, dict) else {},
                    "reason": reason,
                    "review": {
                        "human_verified": bool(raw.get("human_verified")),
                        "review_note": str(raw.get("review_note") or ""),
                        "reviewed_at": str(raw.get("reviewed_at") or ""),
                    },
                    "source_problem": source,
                    "target_problem": target,
                    "pair_text": _feedback_pair_text(source, target, reason),
                }
            )
        return examples
    finally:
        close = getattr(cur, "close", None)
        if callable(close):
            close()


def build_similarity_feedback_manifest(
    examples: list[dict[str, Any]],
    *,
    db_profile: str,
    db_name: str,
    model_id: str,
    statuses: list[str] | tuple[str, ...],
    output_jsonl: str,
) -> dict[str, Any]:
    labels: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for row in examples:
        labels[str(row.get("label") or "")] = labels.get(str(row.get("label") or ""), 0) + 1
        status_counts[str(row.get("status") or "")] = status_counts.get(str(row.get("status") or ""), 0) + 1
    return {
        "schema_version": SIMILARITY_FEEDBACK_EXPORT_SCHEMA_VERSION,
        "db_profile": str(db_profile or ""),
        "db_name": str(db_name or ""),
        "model_id": str(model_id or SIMILARITY_MODEL_ID),
        "statuses": [normalize_similarity_review_status(item) for item in statuses],
        "examples": len(examples),
        "labels": labels,
        "status_counts": status_counts,
        "output_jsonl": str(output_jsonl),
        "notes": [
            "positive = relacion aceptada por docente",
            "negative = relacion rechazada por docente",
            "uncertain = relacion marcada como dudosa",
        ],
    }
