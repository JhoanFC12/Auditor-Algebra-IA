from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


TAG_EXAMEN_RE = re.compile(r"\[\[\s*examen\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)


def normalize_origin_code(value: str) -> str:
    raw = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return raw or "origen"


def extract_exam_tag(text: str) -> str:
    label = ""
    for match in TAG_EXAMEN_RE.finditer(text or ""):
        value = str(match.group(1) or "").strip()
        if value:
            label = value
    return label


def parse_exam_metadata(label: str) -> dict[str, Any]:
    text = str(label or "").strip()
    metadata: dict[str, Any] = {}
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    if year_match:
        metadata["anio"] = int(year_match.group(1))
    process_match = re.search(
        r"\b(?:proceso|proc\.?|admision|admisi[oó]n)\s*[:#-]?\s*([0-9IVXLCDMivxlcdm-]+)\b",
        text,
        flags=re.IGNORECASE,
    )
    if process_match:
        metadata["proceso"] = process_match.group(1).strip().upper()
    area_match = re.search(r"\b[aá]rea\s*[:#-]?\s*([A-Z0-9]+)\b", text, flags=re.IGNORECASE)
    if area_match:
        metadata["area"] = area_match.group(1).strip().upper()
    return metadata


def ensure_problem_origin_schema(conn) -> None:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS origenes (
                id SERIAL PRIMARY KEY,
                tipo_origen VARCHAR(50) NOT NULL DEFAULT 'general',
                codigo VARCHAR(160) NOT NULL UNIQUE,
                nombre TEXT NOT NULL,
                institucion TEXT NOT NULL DEFAULT '',
                anio INT,
                proceso VARCHAR(50) NOT NULL DEFAULT '',
                area VARCHAR(50) NOT NULL DEFAULT '',
                modalidad VARCHAR(120) NOT NULL DEFAULT '',
                proyecto TEXT NOT NULL DEFAULT '',
                libro TEXT NOT NULL DEFAULT '',
                instancia TEXT NOT NULL DEFAULT '',
                pdf_path TEXT NOT NULL DEFAULT '',
                session_path TEXT NOT NULL DEFAULT '',
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                estado VARCHAR(40) NOT NULL DEFAULT 'activo',
                notas TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS problema_origen (
                id SERIAL PRIMARY KEY,
                problema_id INT NOT NULL REFERENCES problemas(id) ON DELETE CASCADE,
                origen_id INT NOT NULL REFERENCES origenes(id) ON DELETE CASCADE,
                numero_original INT,
                orden INT,
                pagina INT,
                bloque TEXT NOT NULL DEFAULT '',
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (problema_id, origen_id)
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS ix_origenes_tipo_codigo ON origenes(tipo_origen, codigo);")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_problema_origen_origen ON problema_origen(origen_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_problema_origen_problema ON problema_origen(problema_id);")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def upsert_exam_origin_for_problem(
    conn,
    *,
    problem_id: int,
    exam_label: str,
    numero_original: int | None = None,
) -> int | None:
    label = str(exam_label or "").strip()
    if not label:
        return None
    ensure_problem_origin_schema(conn)
    metadata = parse_exam_metadata(label)
    code = normalize_origin_code(label)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO origenes (
                tipo_origen, codigo, nombre, anio, proceso, area, metadata_json
            )
            VALUES ('examen_admision', %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (codigo) DO UPDATE
            SET nombre = EXCLUDED.nombre,
                anio = COALESCE(EXCLUDED.anio, origenes.anio),
                proceso = COALESCE(NULLIF(EXCLUDED.proceso, ''), origenes.proceso),
                area = COALESCE(NULLIF(EXCLUDED.area, ''), origenes.area),
                metadata_json = origenes.metadata_json || EXCLUDED.metadata_json,
                updated_at = NOW()
            RETURNING id;
            """,
            (
                code,
                label,
                metadata.get("anio"),
                str(metadata.get("proceso") or ""),
                str(metadata.get("area") or ""),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        origin_id = int(cur.fetchone()[0])
        cur.execute(
            """
            INSERT INTO problema_origen (problema_id, origen_id, numero_original, orden)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (problema_id, origen_id) DO UPDATE
            SET numero_original = COALESCE(EXCLUDED.numero_original, problema_origen.numero_original),
                orden = COALESCE(EXCLUDED.orden, problema_origen.orden);
            """,
            (int(problem_id), origin_id, numero_original, numero_original),
        )
        return origin_id
    finally:
        cur.close()
