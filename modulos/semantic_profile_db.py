from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Any

from .semantic_figure_seed import build_figure_semantic_seed, figure_embedding_text
from .semantic_profile_seed import build_problem_semantic_seed
from .semantic_solution_seed import (
    build_solution_semantic_seed,
    solution_entries_from_payload,
    solution_entry_from_table_row,
)
from .semantic_similarity_seed import (
    SIMILARITY_MODEL_ID,
    extract_problem_similarity_features,
    rank_similar_problems,
)


REPORT_SCHEMA_VERSION = "semantic_seed_profile_db_report_v1"
PROFILE_MODEL_ID = "semantic_seed_v1"
PROFILE_SCHEMA_VERSION = "problem_semantic_profile_v1"
FIGURE_PROFILE_MODEL_ID = "figure_semantic_seed_v1"
FIGURE_PROFILE_SCHEMA_VERSION = "geometry_figure_description_v1"
SOLUTION_PROFILE_MODEL_ID = "solution_semantic_seed_v1"
SOLUTION_PROFILE_SCHEMA_VERSION = "solution_semantic_profile_v1"
SEMANTIC_STATUS_SCHEMA_VERSION = "semantic_coverage_status_v1"
PRACTICE_DRAFT_SCHEMA_VERSION = "semantic_practice_draft_v1"
PRACTICE_DRAFT_SAVE_SCHEMA_VERSION = "semantic_practice_draft_saved_v1"
PRACTICE_DRAFT_LIST_SCHEMA_VERSION = "semantic_practice_draft_list_v1"
PRACTICE_DRAFT_CATALOG_SCHEMA_VERSION = "semantic_practice_draft_catalog_v1"
CONCEPT_GRAPH_SCHEMA_VERSION = "semantic_concept_graph_report_v1"
CONCEPT_CATALOG_SCHEMA_VERSION = "semantic_concept_catalog_v1"
CONCEPT_LINKED_PROBLEMS_SCHEMA_VERSION = "semantic_concept_linked_problems_v1"
CONCEPT_LINK_REVIEW_SCHEMA_VERSION = "semantic_concept_link_review_v1"
PROBLEM_CONCEPT_LINKS_SCHEMA_VERSION = "semantic_problem_concept_links_v1"


def _cursor(conn: Any) -> Any:
    return conn.cursor()


def _close_cursor(cur: Any) -> None:
    close = getattr(cur, "close", None)
    if callable(close):
        close()


def table_columns(conn: Any, table: str) -> set[str]:
    cur = _cursor(conn)
    try:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s;
            """,
            (table,),
        )
        return {str(row[0]) for row in cur.fetchall()}
    finally:
        _close_cursor(cur)


def table_exists(conn: Any, table: str) -> bool:
    cur = _cursor(conn)
    try:
        cur.execute("SELECT to_regclass(%s);", (f"public.{table}",))
        row = cur.fetchone()
        return bool(row and row[0])
    finally:
        _close_cursor(cur)


def _scalar_int(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> int:
    cur = _cursor(conn)
    try:
        cur.execute(sql, params)
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0
    finally:
        _close_cursor(cur)


def _count_rows(conn: Any, table: str, where_sql: str = "", params: tuple[Any, ...] = ()) -> int:
    if not table_exists(conn, table):
        return 0
    sql = f"SELECT COUNT(*) FROM {table}"
    if where_sql:
        sql += f" {where_sql}"
    return _scalar_int(conn, sql + ";", params)


def _count_distinct(
    conn: Any,
    table: str,
    column: str,
    where_sql: str = "",
    params: tuple[Any, ...] = (),
) -> int:
    if not table_exists(conn, table):
        return 0
    columns = table_columns(conn, table)
    if column not in columns:
        return 0
    sql = f"SELECT COUNT(DISTINCT {column}) FROM {table}"
    if where_sql:
        sql += f" {where_sql}"
    return _scalar_int(conn, sql + ";", params)


def _coverage(done: int, total: int) -> dict[str, Any]:
    done = max(0, int(done or 0))
    total = max(0, int(total or 0))
    missing = max(total - min(done, total), 0)
    ratio = (done / total) if total else 0.0
    return {
        "done": done,
        "total": total,
        "missing": missing,
        "ratio": round(ratio, 4),
        "percent": round(ratio * 100, 2),
    }


def fetch_semantic_coverage_status(conn: Any, *, model_id: str = SIMILARITY_MODEL_ID) -> dict[str, Any]:
    """Read-only status for the semantic problem graph.

    This intentionally reports coverage and gaps only. It does not generate
    profiles, embeddings, edges, or training data.
    """

    model_id = str(model_id or SIMILARITY_MODEL_ID).strip() or SIMILARITY_MODEL_ID
    tables = {
        name: table_exists(conn, name)
        for name in [
            "problemas",
            "problem_semantic_profiles",
            "problem_figure_profiles",
            "solution_semantic_profiles",
            "conceptos_matematicos",
            "problema_concepto",
            "problem_embeddings",
            "problem_similarity_edges",
        ]
    }
    total_problems = _count_rows(conn, "problemas")
    problem_profile_rows = _count_rows(
        conn,
        "problem_semantic_profiles",
        "WHERE schema_version = %s",
        (PROFILE_SCHEMA_VERSION,),
    )
    problem_profile_problems = _count_distinct(
        conn,
        "problem_semantic_profiles",
        "problema_id",
        "WHERE schema_version = %s",
        (PROFILE_SCHEMA_VERSION,),
    )
    figure_profile_rows = _count_rows(
        conn,
        "problem_figure_profiles",
        "WHERE schema_version = %s",
        (FIGURE_PROFILE_SCHEMA_VERSION,),
    )
    figure_profile_problems = _count_distinct(
        conn,
        "problem_figure_profiles",
        "problema_id",
        "WHERE schema_version = %s",
        (FIGURE_PROFILE_SCHEMA_VERSION,),
    )
    solution_profile_rows = _count_rows(
        conn,
        "solution_semantic_profiles",
        "WHERE schema_version = %s",
        (SOLUTION_PROFILE_SCHEMA_VERSION,),
    )
    solution_profile_problems = _count_distinct(
        conn,
        "solution_semantic_profiles",
        "problema_id",
        "WHERE schema_version = %s",
        (SOLUTION_PROFILE_SCHEMA_VERSION,),
    )
    embedding_rows = _count_rows(conn, "problem_embeddings")
    embedding_problems = _count_distinct(conn, "problem_embeddings", "problema_id")
    concept_rows = _count_rows(conn, "conceptos_matematicos")
    concept_links = _count_rows(conn, "problema_concepto")
    concept_link_problems = _count_distinct(conn, "problema_concepto", "problema_id")
    similarity_edges = _count_rows(
        conn,
        "problem_similarity_edges",
        "WHERE model_id = %s",
        (model_id,),
    )
    similarity_source_problems = _count_distinct(
        conn,
        "problem_similarity_edges",
        "problema_id",
        "WHERE model_id = %s",
        (model_id,),
    )
    coverage = {
        "problem_profiles": _coverage(problem_profile_problems, total_problems),
        "figure_profile_problems": _coverage(figure_profile_problems, total_problems),
        "solution_profile_problems": _coverage(solution_profile_problems, total_problems),
        "concept_link_problems": _coverage(concept_link_problems, total_problems),
        "embedding_problems": _coverage(embedding_problems, total_problems),
        "similarity_source_problems": _coverage(similarity_source_problems, total_problems),
    }
    if total_problems <= 0:
        next_step = "Subir problemas revisados a la base local."
        readiness = "empty"
    elif problem_profile_problems < total_problems:
        next_step = "Generar perfiles semanticos de problemas faltantes."
        readiness = "profiles_pending"
    elif concept_link_problems <= 0:
        next_step = "Poblar grafo problema-concepto desde perfiles semanticos."
        readiness = "concept_graph_pending"
    elif similarity_edges <= 0:
        next_step = "Calcular relaciones de similitud entre problemas."
        readiness = "edges_pending"
    else:
        next_step = "Revisar similitud y enriquecer perfiles con figura/solucion."
        readiness = "review_ready"
    return {
        "schema_version": SEMANTIC_STATUS_SCHEMA_VERSION,
        "model_id": model_id,
        "tables": tables,
        "counts": {
            "problems": total_problems,
            "problem_profile_rows": problem_profile_rows,
            "problem_profile_problems": problem_profile_problems,
            "figure_profile_rows": figure_profile_rows,
            "figure_profile_problems": figure_profile_problems,
            "solution_profile_rows": solution_profile_rows,
            "solution_profile_problems": solution_profile_problems,
            "concept_rows": concept_rows,
            "concept_links": concept_links,
            "concept_link_problems": concept_link_problems,
            "embedding_rows": embedding_rows,
            "embedding_problems": embedding_problems,
            "similarity_edges": similarity_edges,
            "similarity_source_problems": similarity_source_problems,
        },
        "coverage": coverage,
        "readiness": readiness,
        "next_step": next_step,
    }


def ensure_problem_semantic_profile_schema(conn: Any) -> None:
    cur = _cursor(conn)
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS problem_semantic_profiles (
                id SERIAL PRIMARY KEY,
                problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
                schema_version VARCHAR(80) NOT NULL DEFAULT 'problem_semantic_profile_v1',
                profile_json JSONB NOT NULL,
                embedding_text TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL DEFAULT '',
                status VARCHAR(40) NOT NULL DEFAULT 'sin_revisar',
                human_verified BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (problema_id, schema_version)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_problem_semantic_profiles_problem
            ON problem_semantic_profiles(problema_id);
            """
        )
    finally:
        _close_cursor(cur)


def ensure_problem_figure_profile_schema(conn: Any) -> None:
    cur = _cursor(conn)
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS problema_assets (
                id SERIAL PRIMARY KEY,
                problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
                asset_type VARCHAR(40) NOT NULL,
                asset_tag VARCHAR(80) NOT NULL DEFAULT '',
                file_path TEXT NOT NULL DEFAULT '',
                content_hash VARCHAR(128) NOT NULL DEFAULT '',
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (problema_id, asset_type, asset_tag, content_hash)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS problem_figure_profiles (
                id SERIAL PRIMARY KEY,
                problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
                asset_id INT REFERENCES problema_assets(id) ON DELETE SET NULL,
                figure_tag VARCHAR(80) NOT NULL DEFAULT '',
                schema_version VARCHAR(80) NOT NULL DEFAULT 'geometry_figure_description_v1',
                profile_json JSONB NOT NULL,
                embedding_text TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL DEFAULT '',
                status VARCHAR(40) NOT NULL DEFAULT 'sin_revisar',
                human_verified BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (problema_id, figure_tag, schema_version)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_problema_assets_problem
            ON problema_assets(problema_id);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_problem_figure_profiles_problem
            ON problem_figure_profiles(problema_id);
            """
        )
    finally:
        _close_cursor(cur)


def ensure_solution_semantic_profile_schema(conn: Any) -> None:
    cur = _cursor(conn)
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS solution_semantic_profiles (
                id SERIAL PRIMARY KEY,
                problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
                solution_path_id VARCHAR(100) NOT NULL,
                schema_version VARCHAR(80) NOT NULL DEFAULT 'solution_semantic_profile_v1',
                solution_latex TEXT NOT NULL DEFAULT '',
                profile_json JSONB NOT NULL,
                embedding_text TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL DEFAULT '',
                status VARCHAR(40) NOT NULL DEFAULT 'sin_revisar',
                human_verified BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (problema_id, solution_path_id, schema_version)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_solution_semantic_profiles_problem
            ON solution_semantic_profiles(problema_id);
            """
        )
    finally:
        _close_cursor(cur)


def ensure_concept_graph_schema(conn: Any) -> None:
    cur = _cursor(conn)
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS conceptos_matematicos (
                id SERIAL PRIMARY KEY,
                codigo VARCHAR(160) NOT NULL UNIQUE,
                nombre TEXT NOT NULL,
                curso VARCHAR(120) NOT NULL DEFAULT '',
                tema VARCHAR(160) NOT NULL DEFAULT '',
                tipo VARCHAR(60) NOT NULL DEFAULT 'concepto',
                descripcion TEXT NOT NULL DEFAULT '',
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                estado VARCHAR(40) NOT NULL DEFAULT 'pendiente',
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS problema_concepto (
                id SERIAL PRIMARY KEY,
                problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
                concepto_id INT NOT NULL REFERENCES conceptos_matematicos(id) ON DELETE CASCADE,
                source VARCHAR(60) NOT NULL DEFAULT 'semantic_profile',
                role VARCHAR(60) NOT NULL DEFAULT 'concept',
                confidence NUMERIC(5,4) NOT NULL DEFAULT 0,
                reviewed BOOLEAN NOT NULL DEFAULT FALSE,
                status VARCHAR(40) NOT NULL DEFAULT 'sin_revisar',
                review_note TEXT NOT NULL DEFAULT '',
                reviewed_at TIMESTAMP WITHOUT TIME ZONE,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (problema_id, concepto_id, role)
            );
            """
        )
        cur.execute(
            """
            ALTER TABLE problema_concepto
            ADD COLUMN IF NOT EXISTS status VARCHAR(40) NOT NULL DEFAULT 'sin_revisar';
            """
        )
        cur.execute(
            """
            ALTER TABLE problema_concepto
            ADD COLUMN IF NOT EXISTS review_note TEXT NOT NULL DEFAULT '';
            """
        )
        cur.execute(
            """
            ALTER TABLE problema_concepto
            ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITHOUT TIME ZONE;
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_conceptos_curso_tema
            ON conceptos_matematicos(curso, tema);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_problema_concepto_concepto
            ON problema_concepto(concepto_id);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_problema_concepto_problema
            ON problema_concepto(problema_id);
            """
        )
    finally:
        _close_cursor(cur)


def ensure_problem_similarity_edges_schema(conn: Any) -> None:
    cur = _cursor(conn)
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS problem_similarity_edges (
                id SERIAL PRIMARY KEY,
                problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
                similar_problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
                score NUMERIC(7,6) NOT NULL,
                score_components JSONB NOT NULL DEFAULT '{}'::jsonb,
                reason TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL DEFAULT '',
                status VARCHAR(40) NOT NULL DEFAULT 'sin_revisar',
                human_verified BOOLEAN NOT NULL DEFAULT FALSE,
                review_note TEXT NOT NULL DEFAULT '',
                reviewed_at TIMESTAMP WITHOUT TIME ZONE,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CHECK (problema_id <> similar_problema_id),
                UNIQUE (problema_id, similar_problema_id, model_id)
            );
            """
        )
        cur.execute(
            """
            ALTER TABLE problem_similarity_edges
            ADD COLUMN IF NOT EXISTS review_note TEXT NOT NULL DEFAULT '';
            """
        )
        cur.execute(
            """
            ALTER TABLE problem_similarity_edges
            ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP WITHOUT TIME ZONE;
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_problem_similarity_edges_problem
            ON problem_similarity_edges(problema_id, score DESC);
            """
        )
    finally:
        _close_cursor(cur)


def ensure_semantic_practice_draft_schema(conn: Any) -> None:
    cur = _cursor(conn)
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS semantic_practice_drafts (
                id SERIAL PRIMARY KEY,
                seed_problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
                schema_version VARCHAR(80) NOT NULL DEFAULT 'semantic_practice_draft_v1',
                title TEXT NOT NULL DEFAULT '',
                objective TEXT NOT NULL DEFAULT '',
                draft_json JSONB NOT NULL,
                practice_latex TEXT NOT NULL DEFAULT '',
                model_id TEXT NOT NULL DEFAULT '',
                status VARCHAR(40) NOT NULL DEFAULT 'borrador',
                human_verified BOOLEAN NOT NULL DEFAULT FALSE,
                review_note TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (seed_problema_id, schema_version, model_id)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS ix_semantic_practice_drafts_seed
            ON semantic_practice_drafts(seed_problema_id, updated_at DESC);
            """
        )
    finally:
        _close_cursor(cur)


def _value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _parse_image_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw_items = list(value)
    elif isinstance(value, dict):
        raw_items = list(value.values())
    else:
        raw = str(value or "").strip()
        raw_items: list[Any] = []
        if raw:
            try:
                decoded = json.loads(raw)
            except Exception:
                decoded = None
            if isinstance(decoded, list):
                raw_items = decoded
            elif isinstance(decoded, dict):
                raw_items = list(decoded.values())
            else:
                raw_items = raw.replace(";", ",").split(",")
    tags: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            item = item.get("tag") or item.get("name") or item.get("file_name") or item.get("path") or ""
        name = str(item or "").strip()
        if not name:
            continue
        name = name.replace("\\", "/").rsplit("/", 1)[-1]
        if "." in name:
            name = name.rsplit(".", 1)[0]
        if name and name not in tags:
            tags.append(name)
    return tags


def _decode_json_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None
    return value


def fetch_problem_rows(
    conn: Any,
    *,
    limit: int = 0,
    problem_ids: list[int] | None = None,
    missing_only: bool = True,
) -> list[dict[str, Any]]:
    columns = table_columns(conn, "problemas")
    if "id" not in columns or "enunciado_latex" not in columns:
        raise RuntimeError("La tabla problemas debe tener columnas id y enunciado_latex.")
    wanted = [
        "id",
        "numero_original",
        "enunciado_latex",
        "curso",
        "tema",
        "subtema",
        "respuesta_correcta",
        "respuesta",
        "imagenes",
        "archivo_origen",
        "libro_codigo",
        "codigo_instancia",
        "instancia_tipo",
        "consistencia_matematica",
        "soluciones",
    ]
    select_cols = [col for col in wanted if col in columns]
    sql = f"SELECT {', '.join(select_cols)} FROM problemas p"
    params: list[Any] = []
    conditions: list[str] = []
    if problem_ids:
        conditions.append("p.id = ANY(%s)")
        params.append([int(item) for item in problem_ids])
    if missing_only and table_exists(conn, "problem_semantic_profiles"):
        conditions.append(
            """
            NOT EXISTS (
                SELECT 1
                FROM problem_semantic_profiles sp
                WHERE sp.problema_id = p.id
                  AND sp.schema_version = %s
            )
            """
        )
        params.append(PROFILE_SCHEMA_VERSION)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY p.id"
    if int(limit or 0) > 0:
        sql += " LIMIT %s"
        params.append(int(limit))
    cur = _cursor(conn)
    try:
        cur.execute(sql, tuple(params))
        headers = [str(desc[0]) for desc in cur.description]
        return [dict(zip(headers, row)) for row in cur.fetchall()]
    finally:
        _close_cursor(cur)


def fetch_problem_semantic_profile_records(
    conn: Any,
    *,
    limit: int = 0,
    problem_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    if not table_exists(conn, "problem_semantic_profiles"):
        return []
    columns = table_columns(conn, "problem_semantic_profiles")
    required = {"problema_id", "schema_version", "profile_json"}
    if not required.issubset(columns):
        return []
    wanted = ["problema_id", "profile_json", "embedding_text", "model_id", "status", "human_verified"]
    select_cols = [col for col in wanted if col in columns]
    sql = f"SELECT {', '.join(select_cols)} FROM problem_semantic_profiles WHERE schema_version = %s"
    params: list[Any] = [PROFILE_SCHEMA_VERSION]
    if problem_ids:
        sql += " AND problema_id = ANY(%s)"
        params.append([int(item) for item in problem_ids])
    sql += " ORDER BY problema_id ASC"
    if int(limit or 0) > 0:
        sql += " LIMIT %s"
        params.append(int(limit))
    cur = _cursor(conn)
    try:
        cur.execute(sql, tuple(params))
        headers = [str(desc[0]) for desc in cur.description]
        return [dict(zip(headers, row)) for row in cur.fetchall()]
    finally:
        _close_cursor(cur)


def fetch_figure_profile_records_by_problem(conn: Any, problem_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not problem_ids or not table_exists(conn, "problem_figure_profiles"):
        return {}
    columns = table_columns(conn, "problem_figure_profiles")
    required = {"problema_id", "schema_version", "profile_json"}
    if not required.issubset(columns):
        return {}
    wanted = ["problema_id", "figure_tag", "profile_json", "embedding_text"]
    select_cols = [col for col in wanted if col in columns]
    sql = (
        f"SELECT {', '.join(select_cols)} FROM problem_figure_profiles "
        "WHERE schema_version = %s AND problema_id = ANY(%s) ORDER BY problema_id ASC, figure_tag ASC"
    )
    cur = _cursor(conn)
    try:
        cur.execute(sql, (FIGURE_PROFILE_SCHEMA_VERSION, [int(item) for item in problem_ids]))
        headers = [str(desc[0]) for desc in cur.description]
        out: dict[int, list[dict[str, Any]]] = {}
        for row in cur.fetchall():
            payload = dict(zip(headers, row))
            problem_id = int(payload.get("problema_id") or 0)
            profile = _decode_json_value(payload.get("profile_json"))
            if not isinstance(profile, dict):
                continue
            if payload.get("embedding_text"):
                profile.setdefault("representation", {})
                if isinstance(profile["representation"], dict):
                    profile["representation"].setdefault("embedding_text", str(payload["embedding_text"]))
            out.setdefault(problem_id, []).append(profile)
        return out
    finally:
        _close_cursor(cur)


def fetch_solution_profile_records_by_problem(conn: Any, problem_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not problem_ids or not table_exists(conn, "solution_semantic_profiles"):
        return {}
    columns = table_columns(conn, "solution_semantic_profiles")
    required = {"problema_id", "schema_version", "profile_json"}
    if not required.issubset(columns):
        return {}
    wanted = ["problema_id", "solution_path_id", "profile_json", "embedding_text"]
    select_cols = [col for col in wanted if col in columns]
    sql = (
        f"SELECT {', '.join(select_cols)} FROM solution_semantic_profiles "
        "WHERE schema_version = %s AND problema_id = ANY(%s) ORDER BY problema_id ASC, solution_path_id ASC"
    )
    cur = _cursor(conn)
    try:
        cur.execute(sql, (SOLUTION_PROFILE_SCHEMA_VERSION, [int(item) for item in problem_ids]))
        headers = [str(desc[0]) for desc in cur.description]
        out: dict[int, list[dict[str, Any]]] = {}
        for row in cur.fetchall():
            payload = dict(zip(headers, row))
            problem_id = int(payload.get("problema_id") or 0)
            profile = _decode_json_value(payload.get("profile_json"))
            if not isinstance(profile, dict):
                continue
            if payload.get("embedding_text"):
                profile.setdefault("representation", {})
                if isinstance(profile["representation"], dict):
                    profile["representation"].setdefault("embedding_text", str(payload["embedding_text"]))
            out.setdefault(problem_id, []).append(profile)
        return out
    finally:
        _close_cursor(cur)


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _clean_concept_name(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:180].strip()


def _concept_slug(value: str) -> str:
    raw = _strip_accents(value).lower()
    slug = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return slug or "sin_nombre"


def _concept_code(*, course: str, topic: str, concept_type: str, name: str) -> str:
    base = "__".join(
        [
            _concept_slug(course or "global"),
            _concept_slug(topic or "general"),
            _concept_slug(concept_type or "concepto"),
            _concept_slug(name),
        ]
    )
    if len(base) <= 150:
        return base
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return f"{base[:137].rstrip('_')}__{digest}"


def _as_unique_strings(value: Any) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            item = item.get("name") or item.get("nombre") or item.get("method") or item.get("value") or ""
        text = _clean_concept_name(item)
        if text and text not in out:
            out.append(text)
    return out


def _concept_candidate(
    *,
    problem_id: int,
    name: str,
    course: str,
    topic: str,
    concept_type: str,
    role: str,
    source: str,
    confidence: float,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    clean_name = _clean_concept_name(name)
    if not clean_name or clean_name.upper() in {"SIN_TEMA", "SIN_CURSO"}:
        return None
    clean_course = _clean_concept_name(course)
    clean_topic = _clean_concept_name(topic)
    clean_type = _clean_concept_name(concept_type or "concepto").lower() or "concepto"
    clean_role = _clean_concept_name(role or "concept").lower() or "concept"
    return {
        "problem_id": int(problem_id),
        "codigo": _concept_code(course=clean_course, topic=clean_topic, concept_type=clean_type, name=clean_name),
        "nombre": clean_name,
        "curso": clean_course,
        "tema": clean_topic,
        "tipo": clean_type,
        "role": clean_role,
        "source": _clean_concept_name(source or "semantic_profile"),
        "confidence": max(0.0, min(float(confidence or 0.0), 1.0)),
        "metadata": dict(metadata or {}),
    }


def concept_candidates_from_profiles(
    *,
    problem_id: int,
    problem_profile: dict[str, Any],
    figure_profiles: list[dict[str, Any]] | None = None,
    solution_profiles: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    course = _clean_concept_name(problem_profile.get("course") or "")
    topic = _clean_concept_name(problem_profile.get("topic") or "")
    candidates: list[dict[str, Any]] = []

    def add_many(values: Any, *, concept_type: str, role: str, source: str, confidence: float) -> None:
        for value in _as_unique_strings(values):
            candidate = _concept_candidate(
                problem_id=problem_id,
                name=value,
                course=course,
                topic=topic,
                concept_type=concept_type,
                role=role,
                source=source,
                confidence=confidence,
            )
            if candidate is not None:
                candidates.append(candidate)

    add_many(problem_profile.get("concepts"), concept_type="concepto", role="concept", source="problem_semantic_profile", confidence=0.55)
    add_many(problem_profile.get("skills"), concept_type="tecnica", role="skill", source="problem_semantic_profile", confidence=0.45)
    add_many(problem_profile.get("solution_concepts"), concept_type="propiedad", role="solution_concept", source="problem_semantic_profile", confidence=0.65)
    add_many(problem_profile.get("solution_methods"), concept_type="tecnica", role="solution_method", source="problem_semantic_profile", confidence=0.6)

    for profile in list(solution_profiles or []):
        add_many(profile.get("concepts_used"), concept_type="concepto", role="solution_concept", source="solution_semantic_profile", confidence=0.7)
        add_many(profile.get("skills_used"), concept_type="tecnica", role="skill", source="solution_semantic_profile", confidence=0.6)
        method = _clean_concept_name(profile.get("method") or "")
        if method:
            add_many([method], concept_type="tecnica", role="solution_method", source="solution_semantic_profile", confidence=0.65)
        for prop in list(profile.get("properties_used") or []):
            name = prop.get("name") if isinstance(prop, dict) else prop
            candidate = _concept_candidate(
                problem_id=problem_id,
                name=str(name or ""),
                course=course,
                topic=topic,
                concept_type="propiedad",
                role="property",
                source="solution_semantic_profile",
                confidence=0.75,
                metadata={"solution_path_id": profile.get("solution_path_id") or ""},
            )
            if candidate is not None:
                candidates.append(candidate)

    for profile in list(figure_profiles or []):
        figure_type = _clean_concept_name(profile.get("figure_type") or "")
        if figure_type and figure_type != "otro":
            candidate = _concept_candidate(
                problem_id=problem_id,
                name=figure_type,
                course=course,
                topic=topic,
                concept_type="concepto",
                role="figure_type",
                source="problem_figure_profile",
                confidence=0.35,
                metadata={"figure_tag": profile.get("figure_tag") or ""},
            )
            if candidate is not None:
                candidates.append(candidate)

    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        key = (str(candidate["codigo"]), str(candidate["role"]))
        previous = deduped.get(key)
        if previous is None or float(candidate["confidence"]) > float(previous["confidence"]):
            deduped[key] = candidate
    return list(deduped.values())


def fetch_similarity_features(
    conn: Any,
    *,
    limit: int = 0,
    problem_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    records = fetch_problem_semantic_profile_records(conn, limit=limit, problem_ids=problem_ids)
    ids = [int(row["problema_id"]) for row in records if int(row.get("problema_id") or 0) > 0]
    figure_profiles = fetch_figure_profile_records_by_problem(conn, ids)
    solution_profiles = fetch_solution_profile_records_by_problem(conn, ids)
    features: list[dict[str, Any]] = []
    for row in records:
        problem_id = int(row.get("problema_id") or 0)
        profile = _decode_json_value(row.get("profile_json"))
        if not isinstance(profile, dict):
            continue
        profile.setdefault("problem_id", str(problem_id))
        features.append(
            extract_problem_similarity_features(
                profile,
                figure_profiles=figure_profiles.get(problem_id, []),
                solution_profiles=solution_profiles.get(problem_id, []),
            )
        )
    return features


def fetch_normalized_solution_rows(conn: Any, *, problem_ids: list[int] | None = None) -> dict[int, list[dict[str, Any]]]:
    if not table_exists(conn, "soluciones"):
        return {}
    columns = table_columns(conn, "soluciones")
    required = {"problema_id", "orden", "metodo_nombre", "solucion_latex"}
    if not required.issubset(columns):
        return {}
    wanted = ["id", "problema_id", "orden", "metodo_nombre", "solucion_latex", "autor_ia"]
    select_cols = [col for col in wanted if col in columns]
    sql = f"SELECT {', '.join(select_cols)} FROM soluciones"
    params: list[Any] = []
    if problem_ids:
        sql += " WHERE problema_id = ANY(%s)"
        params.append([int(item) for item in problem_ids])
    sql += " ORDER BY problema_id ASC, orden ASC, id ASC"
    cur = _cursor(conn)
    try:
        cur.execute(sql, tuple(params))
        headers = [str(desc[0]) for desc in cur.description]
        out: dict[int, list[dict[str, Any]]] = {}
        for row in cur.fetchall():
            payload = dict(zip(headers, row))
            problem_id = int(payload.get("problema_id") or 0)
            if problem_id > 0:
                out.setdefault(problem_id, []).append(payload)
        return out
    finally:
        _close_cursor(cur)


def build_seed_profile_from_problem_row(row: dict[str, Any]) -> dict[str, Any]:
    problem_id = int(row.get("id") or 0)
    if problem_id <= 0:
        raise ValueError("Fila de problema sin id valido.")
    final_latex = str(row.get("enunciado_latex") or "").strip()
    if not final_latex:
        raise ValueError(f"Problema {problem_id} sin enunciado_latex.")
    profile = build_problem_semantic_seed(
        problem_id=problem_id,
        final_latex=final_latex,
        raw_ocr="",
        course_hint=_value(row, "curso"),
        topic_hint=_value(row, "tema"),
        subtopic_hint=_value(row, "subtema"),
        answer_hint=_value(row, "respuesta_correcta", "respuesta"),
        image_tags_hint=_parse_image_tags(row.get("imagenes")),
    )
    evidence = dict(profile.get("evidence") or {})
    evidence["db_columns"] = sorted(str(key) for key in row.keys())
    evidence["db_context"] = {
        "numero_original": row.get("numero_original"),
        "archivo_origen": _value(row, "archivo_origen"),
        "libro_codigo": _value(row, "libro_codigo"),
        "codigo_instancia": _value(row, "codigo_instancia", "instancia_tipo"),
        "consistencia_matematica": _value(row, "consistencia_matematica"),
    }
    profile["evidence"] = evidence
    return profile


def build_figure_seed_profiles_from_problem_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    problem_id = int(row.get("id") or 0)
    if problem_id <= 0:
        raise ValueError("Fila de problema sin id valido.")
    image_tags = _parse_image_tags(row.get("imagenes"))
    if not image_tags:
        semantic = build_seed_profile_from_problem_row(row)
        image_tags = list(dict(semantic.get("evidence") or {}).get("figure_tags") or [])
    profiles: list[dict[str, Any]] = []
    for tag in image_tags:
        profiles.append(
            build_figure_semantic_seed(
                problem_id=problem_id,
                figure_tag=tag,
                course=_value(row, "curso"),
                topic=_value(row, "tema"),
                asset_path=tag,
            )
        )
    return profiles


def build_solution_seed_profiles_from_problem_row(
    row: dict[str, Any],
    *,
    normalized_solution_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    problem_id = int(row.get("id") or 0)
    if problem_id <= 0:
        raise ValueError("Fila de problema sin id valido.")
    entries = []
    for solution_row in list(normalized_solution_rows or []):
        entry = solution_entry_from_table_row(solution_row)
        if entry is not None:
            entries.append(entry)
    if not entries:
        entries = solution_entries_from_payload(row.get("soluciones"), source="problemas.soluciones")
    if not entries:
        return []
    figure_tags = _parse_image_tags(row.get("imagenes"))
    if not figure_tags:
        figure_tags = list(dict(build_seed_profile_from_problem_row(row).get("evidence") or {}).get("figure_tags") or [])
    problem_source = " ".join(
        item
        for item in [
            _value(row, "libro_codigo"),
            _value(row, "codigo_instancia", "instancia_tipo"),
            _value(row, "archivo_origen"),
        ]
        if item
    ).strip()
    return [
        build_solution_semantic_seed(
            problem_id=problem_id,
            entry=entry,
            problem_source=problem_source,
            figure_tags=figure_tags,
        )
        for entry in entries
    ]


def upsert_problem_semantic_profile(conn: Any, profile: dict[str, Any], *, refresh: bool = False) -> str:
    problem_id = int(profile["problem_id"])
    embedding_text = str(dict(profile.get("representation") or {}).get("embedding_text") or "")
    payload = json.dumps(profile, ensure_ascii=False)
    cur = _cursor(conn)
    try:
        if refresh:
            cur.execute(
                """
                INSERT INTO problem_semantic_profiles (
                    problema_id, schema_version, profile_json, embedding_text, model_id, status, human_verified
                )
                VALUES (%s, %s, %s::jsonb, %s, %s, %s, FALSE)
                ON CONFLICT (problema_id, schema_version) DO UPDATE
                SET profile_json = EXCLUDED.profile_json,
                    embedding_text = EXCLUDED.embedding_text,
                    model_id = EXCLUDED.model_id,
                    status = EXCLUDED.status,
                    updated_at = CURRENT_TIMESTAMP
                WHERE problem_semantic_profiles.human_verified = FALSE;
                """,
                (
                    problem_id,
                    PROFILE_SCHEMA_VERSION,
                    payload,
                    embedding_text,
                    PROFILE_MODEL_ID,
                    "sin_revisar",
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO problem_semantic_profiles (
                    problema_id, schema_version, profile_json, embedding_text, model_id, status, human_verified
                )
                VALUES (%s, %s, %s::jsonb, %s, %s, %s, FALSE)
                ON CONFLICT (problema_id, schema_version) DO NOTHING;
                """,
                (
                    problem_id,
                    PROFILE_SCHEMA_VERSION,
                    payload,
                    embedding_text,
                    PROFILE_MODEL_ID,
                    "sin_revisar",
                ),
            )
        return "upserted" if int(getattr(cur, "rowcount", 0) or 0) > 0 else "skipped_existing"
    finally:
        _close_cursor(cur)


def upsert_problem_figure_profile(conn: Any, profile: dict[str, Any], *, refresh: bool = False) -> str:
    problem_id = int(profile["source_record_id"])
    figure_tag = str(profile.get("figure_tag") or "").strip()
    embedding_text = figure_embedding_text(profile)
    payload = json.dumps(profile, ensure_ascii=False)
    cur = _cursor(conn)
    try:
        if refresh:
            cur.execute(
                """
                INSERT INTO problem_figure_profiles (
                    problema_id, asset_id, figure_tag, schema_version, profile_json,
                    embedding_text, model_id, status, human_verified
                )
                VALUES (%s, NULL, %s, %s, %s::jsonb, %s, %s, %s, FALSE)
                ON CONFLICT (problema_id, figure_tag, schema_version) DO UPDATE
                SET profile_json = EXCLUDED.profile_json,
                    embedding_text = EXCLUDED.embedding_text,
                    model_id = EXCLUDED.model_id,
                    status = EXCLUDED.status,
                    updated_at = CURRENT_TIMESTAMP
                WHERE problem_figure_profiles.human_verified = FALSE;
                """,
                (
                    problem_id,
                    figure_tag,
                    FIGURE_PROFILE_SCHEMA_VERSION,
                    payload,
                    embedding_text,
                    FIGURE_PROFILE_MODEL_ID,
                    "sin_revisar",
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO problem_figure_profiles (
                    problema_id, asset_id, figure_tag, schema_version, profile_json,
                    embedding_text, model_id, status, human_verified
                )
                VALUES (%s, NULL, %s, %s, %s::jsonb, %s, %s, %s, FALSE)
                ON CONFLICT (problema_id, figure_tag, schema_version) DO NOTHING;
                """,
                (
                    problem_id,
                    figure_tag,
                    FIGURE_PROFILE_SCHEMA_VERSION,
                    payload,
                    embedding_text,
                    FIGURE_PROFILE_MODEL_ID,
                    "sin_revisar",
                ),
            )
        return "upserted" if int(getattr(cur, "rowcount", 0) or 0) > 0 else "skipped_existing"
    finally:
        _close_cursor(cur)


def upsert_solution_semantic_profile(conn: Any, profile: dict[str, Any], *, refresh: bool = False) -> str:
    problem_id = int(profile["problem_id"])
    solution_path_id = str(profile.get("solution_path_id") or "sol_01").strip()
    solution_latex = str(dict(profile.get("evidence") or {}).get("solution_text_latex") or "")
    embedding_text = str(dict(profile.get("representation") or {}).get("embedding_text") or "")
    payload = json.dumps(profile, ensure_ascii=False)
    cur = _cursor(conn)
    try:
        if refresh:
            cur.execute(
                """
                INSERT INTO solution_semantic_profiles (
                    problema_id, solution_path_id, schema_version, solution_latex,
                    profile_json, embedding_text, model_id, status, human_verified
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, FALSE)
                ON CONFLICT (problema_id, solution_path_id, schema_version) DO UPDATE
                SET solution_latex = EXCLUDED.solution_latex,
                    profile_json = EXCLUDED.profile_json,
                    embedding_text = EXCLUDED.embedding_text,
                    model_id = EXCLUDED.model_id,
                    status = EXCLUDED.status,
                    updated_at = CURRENT_TIMESTAMP
                WHERE solution_semantic_profiles.human_verified = FALSE;
                """,
                (
                    problem_id,
                    solution_path_id,
                    SOLUTION_PROFILE_SCHEMA_VERSION,
                    solution_latex,
                    payload,
                    embedding_text,
                    SOLUTION_PROFILE_MODEL_ID,
                    "sin_revisar",
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO solution_semantic_profiles (
                    problema_id, solution_path_id, schema_version, solution_latex,
                    profile_json, embedding_text, model_id, status, human_verified
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, FALSE)
                ON CONFLICT (problema_id, solution_path_id, schema_version) DO NOTHING;
                """,
                (
                    problem_id,
                    solution_path_id,
                    SOLUTION_PROFILE_SCHEMA_VERSION,
                    solution_latex,
                    payload,
                    embedding_text,
                    SOLUTION_PROFILE_MODEL_ID,
                    "sin_revisar",
                ),
            )
        return "upserted" if int(getattr(cur, "rowcount", 0) or 0) > 0 else "skipped_existing"
    finally:
        _close_cursor(cur)


def upsert_semantic_concept(conn: Any, candidate: dict[str, Any]) -> int:
    metadata = {
        "generated_by": "semantic_concept_graph_seed",
        "sources": [candidate.get("source")],
    }
    payload = json.dumps(metadata, ensure_ascii=False)
    cur = _cursor(conn)
    try:
        cur.execute(
            """
            INSERT INTO conceptos_matematicos (
                codigo, nombre, curso, tema, tipo, descripcion, metadata_json, estado
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (codigo) DO UPDATE
            SET nombre = EXCLUDED.nombre,
                curso = EXCLUDED.curso,
                tema = EXCLUDED.tema,
                tipo = EXCLUDED.tipo,
                updated_at = CURRENT_TIMESTAMP
            RETURNING id;
            """,
            (
                candidate["codigo"],
                candidate["nombre"],
                candidate["curso"],
                candidate["tema"],
                candidate["tipo"],
                "",
                payload,
                "pendiente",
            ),
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        _close_cursor(cur)


def upsert_problem_concept_link(conn: Any, candidate: dict[str, Any], *, concepto_id: int) -> str:
    if int(concepto_id or 0) <= 0:
        raise ValueError("concepto_id invalido para crear problema_concepto.")
    metadata = dict(candidate.get("metadata") or {})
    metadata.update(
        {
            "concept_code": candidate["codigo"],
            "concept_name": candidate["nombre"],
            "seed_status": "pendiente_revision",
        }
    )
    cur = _cursor(conn)
    try:
        cur.execute(
            """
            INSERT INTO problema_concepto (
                problema_id, concepto_id, source, role, confidence, reviewed, metadata_json
            )
            VALUES (%s, %s, %s, %s, %s, FALSE, %s::jsonb)
            ON CONFLICT (problema_id, concepto_id, role) DO UPDATE
            SET source = EXCLUDED.source,
                confidence = GREATEST(problema_concepto.confidence, EXCLUDED.confidence),
                metadata_json = EXCLUDED.metadata_json;
            """,
            (
                int(candidate["problem_id"]),
                int(concepto_id),
                candidate["source"],
                candidate["role"],
                float(candidate["confidence"]),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        return "linked" if int(getattr(cur, "rowcount", 0) or 0) > 0 else "skipped_existing"
    finally:
        _close_cursor(cur)


def upsert_problem_similarity_edge(conn: Any, edge: dict[str, Any], *, refresh: bool = False) -> str:
    problem_id = int(edge["source_problem_id"])
    similar_problem_id = int(edge["target_problem_id"])
    score = float(edge.get("score") or 0.0)
    components = {
        "components": edge.get("components") or {},
        "weights": edge.get("weights") or {},
        "shared_concepts": edge.get("shared_concepts") or [],
    }
    payload = json.dumps(components, ensure_ascii=False)
    reason = str(edge.get("reason") or "")
    cur = _cursor(conn)
    try:
        if refresh:
            cur.execute(
                """
                INSERT INTO problem_similarity_edges (
                    problema_id, similar_problema_id, score, score_components,
                    reason, model_id, status, human_verified
                )
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, FALSE)
                ON CONFLICT (problema_id, similar_problema_id, model_id) DO UPDATE
                SET score = EXCLUDED.score,
                    score_components = EXCLUDED.score_components,
                    reason = EXCLUDED.reason,
                    status = EXCLUDED.status,
                    created_at = CURRENT_TIMESTAMP
                WHERE problem_similarity_edges.human_verified = FALSE;
                """,
                (
                    problem_id,
                    similar_problem_id,
                    score,
                    payload,
                    reason,
                    SIMILARITY_MODEL_ID,
                    "sin_revisar",
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO problem_similarity_edges (
                    problema_id, similar_problema_id, score, score_components,
                    reason, model_id, status, human_verified
                )
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, FALSE)
                ON CONFLICT (problema_id, similar_problema_id, model_id) DO NOTHING;
                """,
                (
                    problem_id,
                    similar_problem_id,
                    score,
                    payload,
                    reason,
                    SIMILARITY_MODEL_ID,
                    "sin_revisar",
                ),
            )
        return "upserted" if int(getattr(cur, "rowcount", 0) or 0) > 0 else "skipped_existing"
    finally:
        _close_cursor(cur)


def normalize_similarity_review_status(value: str) -> str:
    status = str(value or "").strip().lower().replace(" ", "_")
    aliases = {
        "accept": "aceptado",
        "accepted": "aceptado",
        "si": "aceptado",
        "similar": "aceptado",
        "reject": "rechazado",
        "rejected": "rechazado",
        "no": "rechazado",
        "not_similar": "rechazado",
        "doubt": "dudoso",
        "doubtful": "dudoso",
        "duda": "dudoso",
        "pending": "sin_revisar",
        "reset": "sin_revisar",
    }
    status = aliases.get(status, status)
    if status not in {"aceptado", "rechazado", "dudoso", "sin_revisar"}:
        raise ValueError("Estado de similitud no valido. Usa aceptado, rechazado, dudoso o sin_revisar.")
    return status


def update_problem_similarity_edge_review(
    conn: Any,
    *,
    problem_id: int,
    similar_problem_id: int,
    model_id: str = SIMILARITY_MODEL_ID,
    status: str,
    review_note: str = "",
) -> dict[str, Any]:
    problem_id = int(problem_id)
    similar_problem_id = int(similar_problem_id)
    if problem_id <= 0 or similar_problem_id <= 0:
        raise ValueError("problem_id y similar_problem_id deben ser mayores que cero.")
    if problem_id == similar_problem_id:
        raise ValueError("Un problema no puede revisarse como similar de si mismo.")
    model_id = str(model_id or SIMILARITY_MODEL_ID).strip() or SIMILARITY_MODEL_ID
    status = normalize_similarity_review_status(status)
    human_verified = status != "sin_revisar"
    ensure_problem_similarity_edges_schema(conn)
    cur = _cursor(conn)
    try:
        cur.execute(
            """
            UPDATE problem_similarity_edges
            SET status = %s,
                human_verified = %s,
                review_note = %s,
                reviewed_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END
            WHERE problema_id = %s
              AND similar_problema_id = %s
              AND model_id = %s;
            """,
            (
                status,
                bool(human_verified),
                str(review_note or "").strip(),
                bool(human_verified),
                problem_id,
                similar_problem_id,
                model_id,
            ),
        )
        if int(getattr(cur, "rowcount", 0) or 0) <= 0:
            raise FileNotFoundError(
                f"Relacion de similitud no encontrada: {problem_id} -> {similar_problem_id} ({model_id})."
            )
    finally:
        _close_cursor(cur)
    return {
        "schema_version": "problem_similarity_edge_review_v1",
        "problem_id": problem_id,
        "similar_problem_id": similar_problem_id,
        "model_id": model_id,
        "status": status,
        "human_verified": human_verified,
        "review_note": str(review_note or "").strip(),
    }


def normalize_practice_draft_status(value: str) -> str:
    status = str(value or "").strip().lower().replace(" ", "_")
    aliases = {
        "draft": "borrador",
        "pending": "borrador",
        "reviewed": "revisado",
        "approved": "revisado",
        "ok": "revisado",
        "discard": "descartado",
        "discarded": "descartado",
    }
    status = aliases.get(status, status)
    if status not in {"borrador", "revisado", "descartado"}:
        raise ValueError("Estado de borrador no valido. Usa borrador, revisado o descartado.")
    return status


def save_semantic_practice_draft(
    conn: Any,
    draft: dict[str, Any],
    *,
    problem_id: int | None = None,
    status: str = "borrador",
    review_note: str = "",
) -> dict[str, Any]:
    if not isinstance(draft, dict):
        raise ValueError("El borrador de practica debe ser un objeto JSON.")
    seed_problem_id = int(problem_id or draft.get("seed_problem_id") or 0)
    if seed_problem_id <= 0:
        raise ValueError("El borrador debe tener seed_problem_id valido.")
    draft_seed = int(draft.get("seed_problem_id") or seed_problem_id)
    if draft_seed != seed_problem_id:
        raise ValueError("El seed_problem_id del borrador no coincide con la ruta solicitada.")
    schema_version = str(draft.get("schema_version") or PRACTICE_DRAFT_SCHEMA_VERSION).strip()
    if schema_version != PRACTICE_DRAFT_SCHEMA_VERSION:
        raise ValueError(f"Schema de borrador no soportado: {schema_version}.")
    model_id = str(draft.get("model_id") or SIMILARITY_MODEL_ID).strip() or SIMILARITY_MODEL_ID
    title = str(draft.get("title") or "").strip()
    objective = str(draft.get("objective") or "").strip()
    practice_latex = str(draft.get("practice_latex") or "").strip()
    status = normalize_practice_draft_status(status)
    human_verified = status == "revisado"
    payload = json.dumps(draft, ensure_ascii=False)
    ensure_semantic_practice_draft_schema(conn)
    cur = _cursor(conn)
    try:
        cur.execute(
            """
            INSERT INTO semantic_practice_drafts (
                seed_problema_id, schema_version, title, objective, draft_json,
                practice_latex, model_id, status, human_verified, review_note
            )
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
            ON CONFLICT (seed_problema_id, schema_version, model_id) DO UPDATE
            SET title = EXCLUDED.title,
                objective = EXCLUDED.objective,
                draft_json = EXCLUDED.draft_json,
                practice_latex = EXCLUDED.practice_latex,
                status = EXCLUDED.status,
                human_verified = EXCLUDED.human_verified,
                review_note = EXCLUDED.review_note,
                updated_at = CURRENT_TIMESTAMP;
            """,
            (
                seed_problem_id,
                schema_version,
                title,
                objective,
                payload,
                practice_latex,
                model_id,
                status,
                bool(human_verified),
                str(review_note or "").strip(),
            ),
        )
    finally:
        _close_cursor(cur)
    return {
        "schema_version": PRACTICE_DRAFT_SAVE_SCHEMA_VERSION,
        "seed_problem_id": seed_problem_id,
        "draft_schema_version": schema_version,
        "model_id": model_id,
        "status": status,
        "human_verified": human_verified,
        "practice_latex_chars": len(practice_latex),
        "recommendation_count": len(list(draft.get("recommendations") or [])),
        "review_note": str(review_note or "").strip(),
        "policy": {
            "saved_to": "semantic_practice_drafts",
            "does_not_modify_problemas": True,
        },
    }


def _normalize_practice_draft_status_filter(value: str) -> str:
    status_filter = str(value or "").strip().lower().replace(" ", "_")
    if status_filter in {"", "all", "todos", "todo"}:
        return ""
    return normalize_practice_draft_status(status_filter)


def _practice_draft_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    raw = {
        "id": row[0],
        "seed_problem_id": row[1],
        "schema_version": row[2],
        "title": row[3],
        "objective": row[4],
        "draft": _decode_json_value(row[5]) or {},
        "practice_latex": str(row[6] or ""),
        "model_id": str(row[7] or ""),
        "status": str(row[8] or ""),
        "human_verified": bool(row[9]),
        "review_note": str(row[10] or ""),
        "created_at": str(row[11] or ""),
        "updated_at": str(row[12] or ""),
    }
    raw["recommendation_count"] = len(list((raw["draft"] or {}).get("recommendations") or []))
    raw["practice_latex_chars"] = len(raw["practice_latex"])
    return raw


def fetch_semantic_practice_drafts(
    conn: Any,
    *,
    problem_id: int,
    limit: int = 20,
    status: str = "",
) -> dict[str, Any]:
    seed_problem_id = int(problem_id or 0)
    if seed_problem_id <= 0:
        raise ValueError("problem_id debe ser mayor que cero.")
    limit = max(1, min(int(limit or 20), 100))
    status_filter = _normalize_practice_draft_status_filter(status)
    if not table_exists(conn, "semantic_practice_drafts"):
        return {
            "schema_version": PRACTICE_DRAFT_LIST_SCHEMA_VERSION,
            "seed_problem_id": seed_problem_id,
            "status_filter": status_filter or "all",
            "count": 0,
            "drafts": [],
            "policy": {"source": "semantic_practice_drafts", "read_only": True},
        }
    cur = _cursor(conn)
    try:
        where_sql = "WHERE seed_problema_id = %s"
        params: tuple[Any, ...]
        if status_filter:
            where_sql += " AND status = %s"
            params = (seed_problem_id, status_filter, limit)
        else:
            params = (seed_problem_id, limit)
        cur.execute(
            f"""
            SELECT id, seed_problema_id, schema_version, title, objective,
                   draft_json, practice_latex, model_id, status, human_verified,
                   review_note, created_at, updated_at
            FROM semantic_practice_drafts
            {where_sql}
            ORDER BY CASE status
                     WHEN 'revisado' THEN 0
                     WHEN 'borrador' THEN 1
                     ELSE 2
                     END,
                     updated_at DESC, id DESC
            LIMIT %s;
            """,
            params,
        )
        rows = cur.fetchall()
    finally:
        _close_cursor(cur)
    drafts = [_practice_draft_from_row(row) for row in rows]
    return {
        "schema_version": PRACTICE_DRAFT_LIST_SCHEMA_VERSION,
        "seed_problem_id": seed_problem_id,
        "status_filter": status_filter or "all",
        "count": len(drafts),
        "drafts": drafts,
        "policy": {"source": "semantic_practice_drafts", "read_only": True},
    }


def fetch_semantic_practice_draft_catalog(
    conn: Any,
    *,
    limit: int = 50,
    status: str = "revisado",
) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), 200))
    status_filter = _normalize_practice_draft_status_filter(status or "revisado")
    if not table_exists(conn, "semantic_practice_drafts"):
        return {
            "schema_version": PRACTICE_DRAFT_CATALOG_SCHEMA_VERSION,
            "status_filter": status_filter or "all",
            "count": 0,
            "drafts": [],
            "policy": {"source": "semantic_practice_drafts", "read_only": True, "student_safe_only": status_filter == "revisado"},
        }
    cur = _cursor(conn)
    try:
        where_sql = ""
        params: tuple[Any, ...]
        if status_filter:
            where_sql = "WHERE status = %s"
            params = (status_filter, limit)
        else:
            params = (limit,)
        cur.execute(
            f"""
            SELECT id, seed_problema_id, schema_version, title, objective,
                   draft_json, practice_latex, model_id, status, human_verified,
                   review_note, created_at, updated_at
            FROM semantic_practice_drafts
            {where_sql}
            ORDER BY CASE status
                     WHEN 'revisado' THEN 0
                     WHEN 'borrador' THEN 1
                     ELSE 2
                     END,
                     updated_at DESC, id DESC
            LIMIT %s;
            """,
            params,
        )
        rows = cur.fetchall()
    finally:
        _close_cursor(cur)
    drafts = [_practice_draft_from_row(row) for row in rows]
    return {
        "schema_version": PRACTICE_DRAFT_CATALOG_SCHEMA_VERSION,
        "status_filter": status_filter or "all",
        "count": len(drafts),
        "drafts": drafts,
        "policy": {
            "source": "semantic_practice_drafts",
            "read_only": True,
            "student_safe_only": status_filter == "revisado",
        },
    }


def populate_problem_concept_graph(
    conn: Any,
    *,
    apply: bool = False,
    limit: int = 0,
    problem_ids: list[int] | None = None,
) -> dict[str, Any]:
    if apply:
        ensure_concept_graph_schema(conn)
    records = fetch_problem_semantic_profile_records(conn, limit=limit, problem_ids=problem_ids)
    ids = [int(row["problema_id"]) for row in records if int(row.get("problema_id") or 0) > 0]
    figure_profiles = fetch_figure_profile_records_by_problem(conn, ids)
    solution_profiles = fetch_solution_profile_records_by_problem(conn, ids)
    report: dict[str, Any] = {
        "schema_version": CONCEPT_GRAPH_SCHEMA_VERSION,
        "dry_run": not bool(apply),
        "generated": 0,
        "concept_candidates": 0,
        "links": 0,
        "errors": 0,
        "rows": [],
        "policy": {
            "source": "problem_semantic_profiles",
            "creates_pending_concepts_only": True,
            "requires_teacher_review": True,
        },
    }
    try:
        for row in records:
            problem_id = int(row.get("problema_id") or 0)
            profile = _decode_json_value(row.get("profile_json"))
            if problem_id <= 0 or not isinstance(profile, dict):
                continue
            try:
                candidates = concept_candidates_from_profiles(
                    problem_id=problem_id,
                    problem_profile=profile,
                    figure_profiles=figure_profiles.get(problem_id, []),
                    solution_profiles=solution_profiles.get(problem_id, []),
                )
                row_report = {
                    "problem_id": problem_id,
                    "candidate_count": len(candidates),
                    "concepts": [
                        {
                            "codigo": item["codigo"],
                            "nombre": item["nombre"],
                            "tipo": item["tipo"],
                            "role": item["role"],
                            "confidence": item["confidence"],
                        }
                        for item in candidates[:20]
                    ],
                    "status": "would_link" if not apply else "linked",
                }
                if apply:
                    linked = 0
                    for candidate in candidates:
                        concepto_id = upsert_semantic_concept(conn, candidate)
                        status = upsert_problem_concept_link(conn, candidate, concepto_id=concepto_id)
                        if status == "linked":
                            linked += 1
                    row_report["linked"] = linked
                    report["links"] += linked
                else:
                    report["links"] += len(candidates)
                report["generated"] += 1
                report["concept_candidates"] += len(candidates)
                report["rows"].append(row_report)
            except Exception as exc:
                report["errors"] += 1
                report["rows"].append(
                    {
                        "problem_id": problem_id,
                        "status": "error",
                        "error": str(exc),
                    }
                )
        if apply:
            conn.commit()
    except Exception:
        if apply:
            rollback = getattr(conn, "rollback", None)
            if callable(rollback):
                rollback()
        raise
    return report


def fetch_semantic_concept_catalog(
    conn: Any,
    *,
    limit: int = 100,
    query: str = "",
    course: str = "",
    status: str = "",
) -> dict[str, Any]:
    limit = max(1, min(int(limit or 100), 300))
    query_filter = str(query or "").strip()
    course_filter = str(course or "").strip()
    status_filter = str(status or "").strip()
    if not table_exists(conn, "conceptos_matematicos"):
        return {
            "schema_version": CONCEPT_CATALOG_SCHEMA_VERSION,
            "filters": {"query": query_filter, "course": course_filter, "status": status_filter},
            "count": 0,
            "concepts": [],
            "policy": {"source": "conceptos_matematicos", "read_only": True},
        }
    has_links = table_exists(conn, "problema_concepto")
    where: list[str] = []
    params: list[Any] = []
    if query_filter:
        where.append("(c.nombre ILIKE %s OR c.codigo ILIKE %s OR c.tema ILIKE %s)")
        like = f"%{query_filter}%"
        params.extend([like, like, like])
    if course_filter:
        where.append("c.curso ILIKE %s")
        params.append(f"%{course_filter}%")
    if status_filter:
        where.append("c.estado = %s")
        params.append(status_filter)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    cur = _cursor(conn)
    try:
        if has_links:
            cur.execute(
                f"""
                SELECT c.id, c.codigo, c.nombre, c.curso, c.tema, c.tipo, c.estado,
                       c.descripcion, COUNT(pc.problema_id) AS problem_count,
                       COUNT(*) FILTER (WHERE pc.reviewed = TRUE) AS reviewed_links
                FROM conceptos_matematicos c
                LEFT JOIN problema_concepto pc ON pc.concepto_id = c.id
                {where_sql}
                GROUP BY c.id, c.codigo, c.nombre, c.curso, c.tema, c.tipo, c.estado, c.descripcion
                ORDER BY COUNT(pc.problema_id) DESC, c.curso ASC, c.tema ASC, c.nombre ASC
                LIMIT %s;
                """,
                tuple([*params, limit]),
            )
        else:
            cur.execute(
                f"""
                SELECT c.id, c.codigo, c.nombre, c.curso, c.tema, c.tipo, c.estado,
                       c.descripcion, 0 AS problem_count, 0 AS reviewed_links
                FROM conceptos_matematicos c
                {where_sql}
                ORDER BY c.curso ASC, c.tema ASC, c.nombre ASC
                LIMIT %s;
                """,
                tuple([*params, limit]),
            )
        rows = cur.fetchall()
    finally:
        _close_cursor(cur)
    concepts = [
        {
            "id": row[0],
            "codigo": str(row[1] or ""),
            "nombre": str(row[2] or ""),
            "curso": str(row[3] or ""),
            "tema": str(row[4] or ""),
            "tipo": str(row[5] or ""),
            "estado": str(row[6] or ""),
            "descripcion": str(row[7] or ""),
            "problem_count": int(row[8] or 0),
            "reviewed_links": int(row[9] or 0),
        }
        for row in rows
    ]
    return {
        "schema_version": CONCEPT_CATALOG_SCHEMA_VERSION,
        "filters": {"query": query_filter, "course": course_filter, "status": status_filter},
        "count": len(concepts),
        "concepts": concepts,
        "policy": {
            "source": "conceptos_matematicos",
            "link_source": "problema_concepto" if has_links else "",
            "read_only": True,
        },
    }


def fetch_semantic_concept_linked_problems(
    conn: Any,
    *,
    concept_id: int,
    limit: int = 50,
    role: str = "",
) -> dict[str, Any]:
    concept_id = int(concept_id or 0)
    if concept_id <= 0:
        raise ValueError("concept_id invalido.")
    limit = max(1, min(int(limit or 50), 200))
    role_filter = str(role or "").strip()
    if not table_exists(conn, "conceptos_matematicos"):
        return {
            "schema_version": CONCEPT_LINKED_PROBLEMS_SCHEMA_VERSION,
            "concept_id": concept_id,
            "concept": None,
            "role_filter": role_filter or "all",
            "count": 0,
            "problems": [],
            "policy": {"source": "problema_concepto", "read_only": True},
        }

    cur = _cursor(conn)
    try:
        cur.execute(
            """
            SELECT id, codigo, nombre, curso, tema, tipo, estado, descripcion
            FROM conceptos_matematicos
            WHERE id = %s;
            """,
            (concept_id,),
        )
        concept_row = cur.fetchone()
    finally:
        _close_cursor(cur)
    if not concept_row:
        raise FileNotFoundError("Concepto no encontrado.")

    concept = {
        "id": concept_row[0],
        "codigo": str(concept_row[1] or ""),
        "nombre": str(concept_row[2] or ""),
        "curso": str(concept_row[3] or ""),
        "tema": str(concept_row[4] or ""),
        "tipo": str(concept_row[5] or ""),
        "estado": str(concept_row[6] or ""),
        "descripcion": str(concept_row[7] or ""),
    }
    if not table_exists(conn, "problema_concepto") or not table_exists(conn, "problemas"):
        return {
            "schema_version": CONCEPT_LINKED_PROBLEMS_SCHEMA_VERSION,
            "concept_id": concept_id,
            "concept": concept,
            "role_filter": role_filter or "all",
            "count": 0,
            "problems": [],
            "policy": {"source": "problema_concepto", "read_only": True},
        }

    link_columns = table_columns(conn, "problema_concepto")
    problem_columns = table_columns(conn, "problemas")
    if "id" not in problem_columns or "enunciado_latex" not in problem_columns:
        raise RuntimeError("La tabla problemas debe tener columnas id y enunciado_latex.")
    wanted = [
        "id",
        "numero_original",
        "enunciado_latex",
        "curso",
        "tema",
        "subtema",
        "respuesta_correcta",
        "respuesta",
        "imagenes",
        "archivo_origen",
        "libro_codigo",
        "codigo_instancia",
        "instancia_tipo",
    ]
    select_cols = [col for col in wanted if col in problem_columns]
    select_sql = ", ".join(f"p.{col}" for col in select_cols)
    status_sql = "pc.status" if "status" in link_columns else "'' AS status"
    review_note_sql = "pc.review_note" if "review_note" in link_columns else "'' AS review_note"
    reviewed_at_sql = "pc.reviewed_at" if "reviewed_at" in link_columns else "NULL AS reviewed_at"
    where_sql = "WHERE pc.concepto_id = %s"
    params: list[Any] = [concept_id]
    if role_filter:
        where_sql += " AND pc.role = %s"
        params.append(role_filter)
    params.append(limit)
    cur = _cursor(conn)
    try:
        cur.execute(
            f"""
            SELECT pc.role, pc.source, pc.confidence, pc.reviewed, pc.metadata_json,
                   {status_sql}, {review_note_sql}, {reviewed_at_sql},
                   {select_sql}
            FROM problema_concepto pc
            JOIN problemas p ON p.id = pc.problema_id
            {where_sql}
            ORDER BY pc.reviewed ASC, pc.confidence DESC, p.id ASC
            LIMIT %s;
            """,
            tuple(params),
        )
        rows = cur.fetchall()
    finally:
        _close_cursor(cur)

    problems: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(zip(select_cols, row[8:]))
        respuesta = _value(payload, "respuesta_correcta", "respuesta")
        link_status = str(row[5] or "").strip() or ("aceptado" if bool(row[3]) else "sin_revisar")
        problems.append(
            {
                "id": payload.get("id"),
                "numero_original": payload.get("numero_original"),
                "enunciado_latex": str(payload.get("enunciado_latex") or ""),
                "curso": str(payload.get("curso") or ""),
                "tema": str(payload.get("tema") or ""),
                "subtema": str(payload.get("subtema") or ""),
                "respuesta": respuesta,
                "imagenes": _decode_json_value(payload.get("imagenes")) or payload.get("imagenes") or "",
                "archivo_origen": str(payload.get("archivo_origen") or ""),
                "libro_codigo": str(payload.get("libro_codigo") or ""),
                "codigo_instancia": str(payload.get("codigo_instancia") or ""),
                "instancia_tipo": str(payload.get("instancia_tipo") or ""),
                "link": {
                    "role": str(row[0] or ""),
                    "source": str(row[1] or ""),
                    "confidence": float(row[2] or 0.0),
                    "reviewed": bool(row[3]),
                    "status": link_status,
                    "review_note": str(row[6] or ""),
                    "reviewed_at": str(row[7] or ""),
                    "metadata": _decode_json_value(row[4]) or {},
                },
            }
        )
    return {
        "schema_version": CONCEPT_LINKED_PROBLEMS_SCHEMA_VERSION,
        "concept_id": concept_id,
        "concept": concept,
        "role_filter": role_filter or "all",
        "count": len(problems),
        "problems": problems,
        "policy": {"source": "problema_concepto", "read_only": True},
    }


def fetch_problem_concept_links(
    conn: Any,
    *,
    problem_id: int,
    limit: int = 50,
    role: str = "",
    status: str = "",
) -> dict[str, Any]:
    problem_id = int(problem_id or 0)
    if problem_id <= 0:
        raise ValueError("problem_id invalido.")
    limit = max(1, min(int(limit or 50), 200))
    role_filter = str(role or "").strip()
    status_filter = str(status or "").strip()
    if not table_exists(conn, "problemas"):
        return {
            "schema_version": PROBLEM_CONCEPT_LINKS_SCHEMA_VERSION,
            "problem_id": problem_id,
            "problem": None,
            "role_filter": role_filter or "all",
            "status_filter": status_filter or "all",
            "count": 0,
            "concepts": [],
            "policy": {"source": "problema_concepto", "read_only": True},
        }
    problem_columns = table_columns(conn, "problemas")
    if "id" not in problem_columns or "enunciado_latex" not in problem_columns:
        raise RuntimeError("La tabla problemas debe tener columnas id y enunciado_latex.")
    wanted_problem = [
        "id",
        "numero_original",
        "enunciado_latex",
        "curso",
        "tema",
        "subtema",
        "respuesta_correcta",
        "respuesta",
        "archivo_origen",
        "libro_codigo",
        "codigo_instancia",
        "instancia_tipo",
    ]
    problem_select_cols = [col for col in wanted_problem if col in problem_columns]
    cur = _cursor(conn)
    try:
        cur.execute(
            f"""
            SELECT {', '.join(f'p.{col}' for col in problem_select_cols)}
            FROM problemas p
            WHERE p.id = %s;
            """,
            (problem_id,),
        )
        problem_row = cur.fetchone()
    finally:
        _close_cursor(cur)
    if not problem_row:
        raise FileNotFoundError("Problema no encontrado.")
    problem_payload = dict(zip(problem_select_cols, problem_row))
    problem = {
        "id": problem_payload.get("id"),
        "numero_original": problem_payload.get("numero_original"),
        "enunciado_latex": str(problem_payload.get("enunciado_latex") or ""),
        "curso": str(problem_payload.get("curso") or ""),
        "tema": str(problem_payload.get("tema") or ""),
        "subtema": str(problem_payload.get("subtema") or ""),
        "respuesta": _value(problem_payload, "respuesta_correcta", "respuesta"),
        "archivo_origen": str(problem_payload.get("archivo_origen") or ""),
        "libro_codigo": str(problem_payload.get("libro_codigo") or ""),
        "codigo_instancia": str(problem_payload.get("codigo_instancia") or ""),
        "instancia_tipo": str(problem_payload.get("instancia_tipo") or ""),
    }
    if not table_exists(conn, "problema_concepto") or not table_exists(conn, "conceptos_matematicos"):
        return {
            "schema_version": PROBLEM_CONCEPT_LINKS_SCHEMA_VERSION,
            "problem_id": problem_id,
            "problem": problem,
            "role_filter": role_filter or "all",
            "status_filter": status_filter or "all",
            "count": 0,
            "concepts": [],
            "policy": {"source": "problema_concepto", "read_only": True},
        }

    link_columns = table_columns(conn, "problema_concepto")
    status_sql = "pc.status" if "status" in link_columns else "'' AS status"
    review_note_sql = "pc.review_note" if "review_note" in link_columns else "'' AS review_note"
    reviewed_at_sql = "pc.reviewed_at" if "reviewed_at" in link_columns else "NULL AS reviewed_at"
    where_sql = "WHERE pc.problema_id = %s"
    params: list[Any] = [problem_id]
    if role_filter:
        where_sql += " AND pc.role = %s"
        params.append(role_filter)
    if status_filter:
        if "status" in link_columns:
            where_sql += " AND pc.status = %s"
            params.append(status_filter)
        elif status_filter != "sin_revisar":
            return {
                "schema_version": PROBLEM_CONCEPT_LINKS_SCHEMA_VERSION,
                "problem_id": problem_id,
                "problem": problem,
                "role_filter": role_filter or "all",
                "status_filter": status_filter,
                "count": 0,
                "concepts": [],
                "policy": {"source": "problema_concepto", "read_only": True},
            }
    params.append(limit)
    cur = _cursor(conn)
    try:
        cur.execute(
            f"""
            SELECT c.id, c.codigo, c.nombre, c.curso, c.tema, c.tipo, c.estado, c.descripcion,
                   pc.role, pc.source, pc.confidence, pc.reviewed, pc.metadata_json,
                   {status_sql}, {review_note_sql}, {reviewed_at_sql}
            FROM problema_concepto pc
            JOIN conceptos_matematicos c ON c.id = pc.concepto_id
            {where_sql}
            ORDER BY pc.reviewed DESC, pc.confidence DESC, c.curso ASC, c.tema ASC, c.nombre ASC
            LIMIT %s;
            """,
            tuple(params),
        )
        rows = cur.fetchall()
    finally:
        _close_cursor(cur)
    concepts: list[dict[str, Any]] = []
    for row in rows:
        link_status = str(row[13] or "").strip() or ("aceptado" if bool(row[11]) else "sin_revisar")
        concepts.append(
            {
                "concept": {
                    "id": row[0],
                    "codigo": str(row[1] or ""),
                    "nombre": str(row[2] or ""),
                    "curso": str(row[3] or ""),
                    "tema": str(row[4] or ""),
                    "tipo": str(row[5] or ""),
                    "estado": str(row[6] or ""),
                    "descripcion": str(row[7] or ""),
                },
                "link": {
                    "role": str(row[8] or ""),
                    "source": str(row[9] or ""),
                    "confidence": float(row[10] or 0.0),
                    "reviewed": bool(row[11]),
                    "status": link_status,
                    "review_note": str(row[14] or ""),
                    "reviewed_at": str(row[15] or ""),
                    "metadata": _decode_json_value(row[12]) or {},
                },
            }
        )
    return {
        "schema_version": PROBLEM_CONCEPT_LINKS_SCHEMA_VERSION,
        "problem_id": problem_id,
        "problem": problem,
        "role_filter": role_filter or "all",
        "status_filter": status_filter or "all",
        "count": len(concepts),
        "concepts": concepts,
        "policy": {"source": "problema_concepto", "read_only": True},
    }


def update_problem_concept_link_review(
    conn: Any,
    *,
    concept_id: int,
    problem_id: int,
    role: str = "concept",
    status: str,
    review_note: str = "",
) -> dict[str, Any]:
    concept_id = int(concept_id or 0)
    problem_id = int(problem_id or 0)
    if concept_id <= 0:
        raise ValueError("concept_id invalido.")
    if problem_id <= 0:
        raise ValueError("problem_id invalido.")
    role = str(role or "concept").strip() or "concept"
    normalized_status = str(status or "").strip().lower()
    allowed = {"aceptado", "rechazado", "dudoso", "sin_revisar"}
    if normalized_status not in allowed:
        raise ValueError("Estado de revision de concepto invalido.")
    note = str(review_note or "").strip()
    reviewed = normalized_status != "sin_revisar"
    ensure_concept_graph_schema(conn)
    cur = _cursor(conn)
    try:
        cur.execute(
            """
            UPDATE problema_concepto
            SET status = %s,
                reviewed = %s,
                review_note = %s,
                reviewed_at = CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END
            WHERE concepto_id = %s AND problema_id = %s AND role = %s
            RETURNING problema_id, concepto_id, role, source, confidence,
                      reviewed, status, review_note, reviewed_at;
            """,
            (
                normalized_status,
                reviewed,
                note,
                reviewed,
                concept_id,
                problem_id,
                role,
            ),
        )
        row = cur.fetchone()
    finally:
        _close_cursor(cur)
    if not row:
        raise FileNotFoundError("Relacion problema-concepto no encontrada.")
    return {
        "schema_version": CONCEPT_LINK_REVIEW_SCHEMA_VERSION,
        "problem_id": int(row[0]),
        "concept_id": int(row[1]),
        "role": str(row[2] or ""),
        "source": str(row[3] or ""),
        "confidence": float(row[4] or 0.0),
        "reviewed": bool(row[5]),
        "status": str(row[6] or ""),
        "review_note": str(row[7] or ""),
        "reviewed_at": str(row[8] or ""),
    }


def populate_problem_semantic_seed_profiles(
    conn: Any,
    *,
    apply: bool = False,
    refresh: bool = False,
    limit: int = 0,
    problem_ids: list[int] | None = None,
) -> dict[str, Any]:
    if apply:
        ensure_problem_semantic_profile_schema(conn)
    rows = fetch_problem_rows(
        conn,
        limit=limit,
        problem_ids=problem_ids,
        missing_only=not refresh,
    )
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": not bool(apply),
        "refresh": bool(refresh),
        "selected": len(rows),
        "generated": 0,
        "upserted": 0,
        "skipped": 0,
        "errors": 0,
        "rows": [],
    }
    try:
        for row in rows:
            row_report: dict[str, Any] = {
                "problem_id": row.get("id"),
                "status": "pending",
                "course": _value(row, "curso"),
                "topic": _value(row, "tema"),
            }
            try:
                profile = build_seed_profile_from_problem_row(row)
                report["generated"] += 1
                if apply:
                    status = upsert_problem_semantic_profile(conn, profile, refresh=refresh)
                    row_report["status"] = status
                    if status == "upserted":
                        report["upserted"] += 1
                    else:
                        report["skipped"] += 1
                else:
                    row_report["status"] = "would_upsert"
            except Exception as exc:
                row_report["status"] = "error"
                row_report["error"] = str(exc)
                report["errors"] += 1
            report["rows"].append(row_report)
        if apply:
            conn.commit()
    except Exception:
        if apply:
            rollback = getattr(conn, "rollback", None)
            if callable(rollback):
                rollback()
        raise
    return report


def populate_problem_similarity_edges(
    conn: Any,
    *,
    apply: bool = False,
    refresh: bool = False,
    limit: int = 0,
    problem_ids: list[int] | None = None,
    top_k: int = 5,
    threshold: float = 0.15,
) -> dict[str, Any]:
    if apply:
        ensure_problem_similarity_edges_schema(conn)
    features = fetch_similarity_features(conn, limit=limit, problem_ids=problem_ids)
    edges = rank_similar_problems(features, top_k=top_k, threshold=threshold)
    report: dict[str, Any] = {
        "schema_version": "semantic_similarity_edges_report_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model_id": SIMILARITY_MODEL_ID,
        "dry_run": not bool(apply),
        "refresh": bool(refresh),
        "selected_profiles": len(features),
        "generated": len(edges),
        "upserted": 0,
        "skipped": 0,
        "errors": 0,
        "top_k": max(1, int(top_k or 1)),
        "threshold": max(0.0, float(threshold or 0.0)),
        "rows": [],
    }
    try:
        for edge in edges:
            row_report = {
                "problem_id": edge.get("source_problem_id"),
                "similar_problem_id": edge.get("target_problem_id"),
                "score": edge.get("score"),
                "reason": edge.get("reason"),
                "status": "would_upsert",
            }
            try:
                if apply:
                    status = upsert_problem_similarity_edge(conn, edge, refresh=refresh)
                    row_report["status"] = status
                    if status == "upserted":
                        report["upserted"] += 1
                    else:
                        report["skipped"] += 1
            except Exception as exc:
                row_report["status"] = "error"
                row_report["error"] = str(exc)
                report["errors"] += 1
            report["rows"].append(row_report)
        if apply:
            conn.commit()
    except Exception:
        if apply:
            rollback = getattr(conn, "rollback", None)
            if callable(rollback):
                rollback()
        raise
    return report


def populate_problem_figure_seed_profiles(
    conn: Any,
    *,
    apply: bool = False,
    refresh: bool = False,
    limit: int = 0,
    problem_ids: list[int] | None = None,
) -> dict[str, Any]:
    if apply:
        ensure_problem_figure_profile_schema(conn)
    rows = fetch_problem_rows(
        conn,
        limit=limit,
        problem_ids=problem_ids,
        missing_only=False,
    )
    report: dict[str, Any] = {
        "schema_version": "figure_seed_profile_db_report_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": not bool(apply),
        "refresh": bool(refresh),
        "selected": len(rows),
        "generated": 0,
        "upserted": 0,
        "skipped": 0,
        "errors": 0,
        "rows": [],
    }
    try:
        for row in rows:
            problem_id = row.get("id")
            try:
                profiles = build_figure_seed_profiles_from_problem_row(row)
                if not profiles:
                    report["skipped"] += 1
                    report["rows"].append({"problem_id": problem_id, "status": "skipped_no_figure"})
                    continue
                for profile in profiles:
                    report["generated"] += 1
                    row_report = {
                        "problem_id": problem_id,
                        "figure_tag": profile.get("figure_tag"),
                        "status": "would_upsert",
                    }
                    if apply:
                        status = upsert_problem_figure_profile(conn, profile, refresh=refresh)
                        row_report["status"] = status
                        if status == "upserted":
                            report["upserted"] += 1
                        else:
                            report["skipped"] += 1
                    report["rows"].append(row_report)
            except Exception as exc:
                report["errors"] += 1
                report["rows"].append({"problem_id": problem_id, "status": "error", "error": str(exc)})
        if apply:
            conn.commit()
    except Exception:
        if apply:
            rollback = getattr(conn, "rollback", None)
            if callable(rollback):
                rollback()
        raise
    return report


def populate_solution_semantic_seed_profiles(
    conn: Any,
    *,
    apply: bool = False,
    refresh: bool = False,
    limit: int = 0,
    problem_ids: list[int] | None = None,
) -> dict[str, Any]:
    if apply:
        ensure_solution_semantic_profile_schema(conn)
    rows = fetch_problem_rows(
        conn,
        limit=limit,
        problem_ids=problem_ids,
        missing_only=False,
    )
    solution_rows_by_problem = fetch_normalized_solution_rows(
        conn,
        problem_ids=[int(row["id"]) for row in rows if int(row.get("id") or 0) > 0],
    )
    report: dict[str, Any] = {
        "schema_version": "solution_seed_profile_db_report_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": not bool(apply),
        "refresh": bool(refresh),
        "selected": len(rows),
        "generated": 0,
        "upserted": 0,
        "skipped": 0,
        "errors": 0,
        "rows": [],
    }
    try:
        for row in rows:
            problem_id = int(row.get("id") or 0)
            try:
                profiles = build_solution_seed_profiles_from_problem_row(
                    row,
                    normalized_solution_rows=solution_rows_by_problem.get(problem_id, []),
                )
                if not profiles:
                    report["skipped"] += 1
                    report["rows"].append({"problem_id": problem_id, "status": "skipped_no_solution"})
                    continue
                for profile in profiles:
                    report["generated"] += 1
                    row_report = {
                        "problem_id": problem_id,
                        "solution_path_id": profile.get("solution_path_id"),
                        "status": "would_upsert",
                    }
                    if apply:
                        status = upsert_solution_semantic_profile(conn, profile, refresh=refresh)
                        row_report["status"] = status
                        if status == "upserted":
                            report["upserted"] += 1
                        else:
                            report["skipped"] += 1
                    report["rows"].append(row_report)
            except Exception as exc:
                report["errors"] += 1
                report["rows"].append({"problem_id": problem_id, "status": "error", "error": str(exc)})
        if apply:
            conn.commit()
    except Exception:
        if apply:
            rollback = getattr(conn, "rollback", None)
            if callable(rollback):
                rollback()
        raise
    return report
