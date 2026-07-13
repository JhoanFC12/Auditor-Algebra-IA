from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.connection import DatabaseManager, read_db_profile_config


CASES_RE = re.compile(r"\\begin\{cases\}(?P<body>.*?)\\end\{cases\}", re.DOTALL)


def _norm(value: object) -> str:
    text = str(value or "").strip()
    text = "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )
    return text.casefold()


def _is_system_candidate(row: dict[str, object]) -> bool:
    meta = _norm(
        " ".join(
            [
                str(row.get("curso") or ""),
                str(row.get("tema") or ""),
                str(row.get("subtema") or ""),
                str(row.get("libro_codigo") or ""),
                str(row.get("codigo_instancia") or ""),
            ]
        )
    )
    text = _norm(row.get("enunciado_latex") or "")
    return (
        ("sistema" in meta and "ecuacion" in meta)
        or ("sistema" in text and "ecuacion" in text)
        or ("sistemas" in text and "ecuaciones" in text)
    )


def _split_array_rows(body: str) -> list[str]:
    return [part for part in re.split(r"(?<!\\)\\\\", body) if part.strip()]


def _array_spec_for_body(body: str) -> str:
    rows = _split_array_rows(body)
    max_alignment_tabs = 0
    for row in rows:
        max_alignment_tabs = max(max_alignment_tabs, row.count("&"))
    return "l" * max(max_alignment_tabs + 1, 1)


def convert_cases_to_array(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        body = match.group("body").strip()
        spec = _array_spec_for_body(body)
        return rf"\left\{{\begin{{array}}{{{spec}}} {body} \end{{array}}\right."

    return CASES_RE.sub(repl, text)


def _iter_problem_rows(cur) -> Iterable[dict[str, object]]:
    cur.execute(
        """
        SELECT
            id,
            numero_original,
            COALESCE(curso,'') AS curso,
            COALESCE(tema,'') AS tema,
            COALESCE(subtema,'') AS subtema,
            COALESCE(libro_codigo,'') AS libro_codigo,
            COALESCE(codigo_instancia,'') AS codigo_instancia,
            COALESCE(enunciado_latex,'') AS enunciado_latex
        FROM problemas
        ORDER BY id;
        """
    )
    columns = [desc[0] for desc in cur.description]
    for raw in cur.fetchall():
        yield dict(zip(columns, raw))


def _problem_columns(cur) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name='problemas';
        """
    )
    return {str(row[0]) for row in cur.fetchall()}


def _backup_path(profile: str, db_name: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_db = re.sub(r"[^A-Za-z0-9_.-]+", "_", db_name)
    root = Path(".cache") / "db_repairs" / "system_equation_arrays"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{stamp}_{profile}_{safe_db}.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convierte sistemas LaTeX en problemas desde cases a left-brace array."
    )
    parser.add_argument("--profile", default="local_mirror", help="Perfil DB: local_mirror, active o cloud.")
    parser.add_argument("--db-name", default="", help="Nombre de BD opcional.")
    parser.add_argument("--apply", action="store_true", help="Aplica cambios. Sin esto solo hace dry-run.")
    parser.add_argument("--limit", type=int, default=0, help="Limita cambios para pruebas.")
    args = parser.parse_args()

    cfg = read_db_profile_config(args.profile)
    db_name = str(args.db_name or cfg["db_name"])
    db = DatabaseManager.from_profile(args.profile, db_name=db_name)

    conn = db.get_connection(db_name)
    changed: list[dict[str, object]] = []
    try:
        cur = conn.cursor()
        problem_cols = _problem_columns(cur)
        for row in _iter_problem_rows(cur):
            text = str(row.get("enunciado_latex") or "")
            if r"\begin{cases}" not in text:
                continue
            if not _is_system_candidate(row):
                continue
            repaired = convert_cases_to_array(text)
            if repaired == text:
                continue
            changed.append({**row, "repaired_enunciado_latex": repaired})
            if args.limit and len(changed) >= args.limit:
                break

        backup = _backup_path(args.profile, db_name)
        with backup.open("w", encoding="utf-8") as fh:
            for row in changed:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

        if args.apply and changed:
            update_parts = ["enunciado_latex = %s"]
            if "updated_at" in problem_cols:
                update_parts.append("updated_at = NOW()")
            if "updated_by" in problem_cols:
                update_parts.append("updated_by = %s")
            if "revision_version" in problem_cols:
                update_parts.append("revision_version = COALESCE(revision_version, 0) + 1")
            sql = f"UPDATE problemas SET {', '.join(update_parts)} WHERE id = %s;"
            for row in changed:
                params: list[object] = [row["repaired_enunciado_latex"]]
                if "updated_by" in problem_cols:
                    params.append("repair-system-equation-arrays")
                params.append(int(row["id"]))
                cur.execute(sql, tuple(params))
            conn.commit()
        else:
            conn.rollback()

        print(
            json.dumps(
                {
                    "profile": args.profile,
                    "db_name": db_name,
                    "mode": "apply" if args.apply else "dry-run",
                    "candidates_changed": len(changed),
                    "backup": str(backup),
                    "sample_ids": [int(row["id"]) for row in changed[:20]],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
