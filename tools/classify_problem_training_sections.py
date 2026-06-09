from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.connection import DatabaseManager
from modulos.modulo12_auditor_entrenamiento.controlador_auditor_entrenamiento import TrainingAuditController


CONFIDENCE_ORDER = {"baja": 1, "media": 2, "alta": 3}
SECTION_TO_COURSE = {
    "Geometria": "Geometria",
    "Geometria analitica": "Geometria Analitica",
    "Algebra": "Algebra",
    "Aritmetica": "Aritmetica",
    "Trigonometria": "Trigonometria",
    "General": "",
}


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _connect(profile: str, db_name: str | None):
    db = DatabaseManager.from_profile(profile, db_name=db_name)
    return db.get_connection(db.db_name)


def _table_columns(conn, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s;
            """,
            (table,),
        )
        return {str(row[0]) for row in cur.fetchall()}


def _value(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _fetch_problem_rows(conn, *, limit: int = 0) -> list[dict[str, Any]]:
    columns = _table_columns(conn, "problemas")
    if not columns:
        raise RuntimeError("No existe la tabla public.problemas.")
    wanted = [
        "id",
        "numero_original",
        "curso",
        "tema",
        "subtema",
        "libro_codigo",
        "codigo_instancia",
        "instancia_tipo",
        "archivo_origen",
        "enunciado_latex",
        "respuesta_correcta",
        "consistencia_matematica",
    ]
    select_cols = [col for col in wanted if col in columns]
    if "id" not in select_cols:
        raise RuntimeError("La tabla problemas no tiene columna id.")
    sql = f"SELECT {', '.join(select_cols)} FROM problemas ORDER BY id"
    params: tuple[Any, ...] = ()
    if int(limit or 0) > 0:
        sql += " LIMIT %s"
        params = (int(limit),)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        headers = [desc[0] for desc in cur.description]
        return [dict(zip(headers, row)) for row in cur.fetchall()]


def _classify_rows(rows: list[dict[str, Any]], *, section_filter: str = "") -> list[dict[str, Any]]:
    section_filter_norm = TrainingAuditController.normalize_training_text(section_filter)
    out: list[dict[str, Any]] = []
    for row in rows:
        result = TrainingAuditController.classify_training_section_from_fields(
            curso=_value(row, "curso"),
            tema=" ".join(x for x in [_value(row, "tema"), _value(row, "subtema")] if x),
            book_code=_value(row, "libro_codigo"),
            instance_type=_value(row, "codigo_instancia", "instancia_tipo"),
            source_label=_value(row, "archivo_origen"),
            text=_value(row, "enunciado_latex"),
        )
        inferred = str(result.get("section") or "General")
        if section_filter_norm and TrainingAuditController.normalize_training_text(inferred) != section_filter_norm:
            continue
        current_course = _value(row, "curso")
        mismatch = bool(
            current_course
            and TrainingAuditController.normalize_training_text(current_course)
            not in {"sin curso", TrainingAuditController.normalize_training_text(inferred)}
        )
        out.append(
            {
                "id": row.get("id"),
                "numero_original": row.get("numero_original", ""),
                "curso_actual": current_course,
                "seccion_inferida": inferred,
                "confianza": result.get("confidence", ""),
                "razon": result.get("reason", ""),
                "posible_conflicto": "SI" if mismatch else "",
                "libro_codigo": _value(row, "libro_codigo"),
                "instancia": _value(row, "codigo_instancia", "instancia_tipo"),
                "archivo_origen": _value(row, "archivo_origen"),
                "respuesta_correcta": _value(row, "respuesta_correcta"),
                "consistencia_matematica": _value(row, "consistencia_matematica"),
                "preview": _value(row, "enunciado_latex")[:260].replace("\n", " "),
            }
        )
    return out


def _write_csv(rows: list[dict[str, Any]], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"problem_training_sections_report_{_timestamp()}.csv"
    fields = [
        "id",
        "numero_original",
        "curso_actual",
        "seccion_inferida",
        "confianza",
        "razon",
        "posible_conflicto",
        "libro_codigo",
        "instancia",
        "archivo_origen",
        "respuesta_correcta",
        "consistencia_matematica",
        "preview",
    ]
    with target.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return target


def _confidence_ok(value: str, minimum: str) -> bool:
    return CONFIDENCE_ORDER.get(str(value or "").lower(), 0) >= CONFIDENCE_ORDER.get(str(minimum or "alta").lower(), 3)


def _mark_rows(
    conn,
    classified: list[dict[str, Any]],
    *,
    section: str,
    min_confidence: str,
    overwrite_course: bool,
) -> int:
    course = SECTION_TO_COURSE.get(section, section)
    if not course:
        raise RuntimeError("No se puede marcar la seccion General como curso.")
    ids: list[int] = []
    for row in classified:
        if row["seccion_inferida"] != section:
            continue
        if not _confidence_ok(str(row.get("confianza") or ""), min_confidence):
            continue
        current = str(row.get("curso_actual") or "").strip()
        if current and current.lower() not in {"sin curso", "sin_curso"} and not overwrite_course:
            continue
        ids.append(int(row["id"]))
    if not ids:
        return 0
    columns = _table_columns(conn, "problemas")
    updates = ["curso = %s"]
    params: list[Any] = [course]
    if "updated_at" in columns:
        updates.append("updated_at = NOW()")
    if "updated_by" in columns:
        updates.append("updated_by = %s")
        params.append("tools/classify_problem_training_sections.py")
    params.append(ids)
    with conn.cursor() as cur:
        cur.execute(f"UPDATE problemas SET {', '.join(updates)} WHERE id = ANY(%s);", tuple(params))
        count = int(cur.rowcount or 0)
    conn.commit()
    return count


def _read_ids(path: Path) -> list[int]:
    raw = path.read_text(encoding="utf-8")
    ids: list[int] = []
    for token in raw.replace(",", "\n").splitlines():
        token = token.strip()
        if not token:
            continue
        ids.append(int(token))
    return sorted(set(ids))


def _delete_ids(conn, ids: list[int], *, out_dir: Path) -> tuple[int, Path]:
    if not ids:
        raise RuntimeError("El archivo de IDs no contiene IDs validos.")
    rows: list[dict[str, Any]]
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM problemas WHERE id = ANY(%s) ORDER BY id;", (ids,))
        headers = [desc[0] for desc in cur.description]
        rows = [dict(zip(headers, row)) for row in cur.fetchall()]
    backup_dir = out_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"delete_problem_ids_training_section_{_timestamp()}.json"
    backup.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM problemas WHERE id = ANY(%s);", (ids,))
        deleted = int(cur.rowcount or 0)
    conn.commit()
    return deleted, backup


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clasifica problemas por seccion de entrenamiento y permite marcar o retirar registros de forma segura."
    )
    parser.add_argument("--profile", default="local_mirror", help="Perfil de BD: local_mirror, cloud o default.")
    parser.add_argument("--db-name", default="", help="Nombre de BD opcional; si se omite usa el perfil.")
    parser.add_argument("--action", choices=["report", "mark", "delete"], default="report")
    parser.add_argument("--section", default="", help="Seccion objetivo, por ejemplo Geometria.")
    parser.add_argument("--min-confidence", choices=["baja", "media", "alta"], default="alta")
    parser.add_argument("--overwrite-course", action="store_true", help="Permite reemplazar curso ya existente.")
    parser.add_argument("--ids-file", default="", help="Archivo con IDs para action=delete.")
    parser.add_argument("--out-dir", default=str(ROOT / ".cache" / "transcriptor_runs" / "reports"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    conn = _connect(args.profile, args.db_name or None)
    try:
        rows = _fetch_problem_rows(conn, limit=int(args.limit or 0))
        classified = _classify_rows(rows, section_filter=args.section if args.action == "report" else "")
        report = _write_csv(classified, out_dir)
        payload: dict[str, Any] = {
            "action": args.action,
            "profile": args.profile,
            "db_name": args.db_name or "(perfil)",
            "rows_seen": len(rows),
            "rows_reported": len(classified),
            "report": str(report),
        }
        if args.action == "mark":
            section = str(args.section or "").strip()
            if not section:
                raise RuntimeError("Para action=mark debes indicar --section, por ejemplo --section Geometria.")
            updated = _mark_rows(
                conn,
                classified,
                section=section,
                min_confidence=args.min_confidence,
                overwrite_course=bool(args.overwrite_course),
            )
            payload["updated"] = updated
        elif args.action == "delete":
            ids_file = Path(args.ids_file)
            if not ids_file.exists():
                raise RuntimeError("Para action=delete debes indicar --ids-file con IDs explicitos.")
            deleted, backup = _delete_ids(conn, _read_ids(ids_file), out_dir=out_dir)
            payload["deleted"] = deleted
            payload["backup"] = str(backup)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
