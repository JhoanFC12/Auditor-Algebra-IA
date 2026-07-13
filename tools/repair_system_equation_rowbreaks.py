from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.connection import DatabaseManager, read_db_profile_config


REPAIRS: dict[int, tuple[str, str]] = {
    10102: (
        r"3x+5y=1\2ax-by=8",
        r"3x+5y=1\\ 2ax-by=8",
    ),
    10119: (
        r"2x+3y=8\mx-y=37\3x+8y=m",
        r"2x+3y=8\\ mx-y=37\\ 3x+8y=m",
    ),
    10126: (
        r"x+y-z=1\x^2-y^2+z^2=1\-x^3+y^3+z^3=-1",
        r"x+y-z=1\\ x^2-y^2+z^2=1\\ -x^3+y^3+z^3=-1",
    ),
    10150: (
        r"3x^2+xy-2y^2=0\ldots(1)\2x^2-3xy+y^2=1\ldots(2)",
        r"3x^2+xy-2y^2=0\ldots(1)\\ 2x^2-3xy+y^2=1\ldots(2)",
    ),
    10152: (
        r"2x-5y=1\mx+10y=4",
        r"2x-5y=1\\ mx+10y=4",
    ),
}


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
    root = Path(".cache") / "db_repairs" / "system_equation_rowbreaks"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{stamp}_{profile}_{safe_db}.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repara saltos de fila perdidos dentro de arrays de sistemas de ecuaciones."
    )
    parser.add_argument("--profile", default="local_mirror", help="Perfil DB: local_mirror, active o cloud.")
    parser.add_argument("--db-name", default="", help="Nombre de BD opcional.")
    parser.add_argument("--apply", action="store_true", help="Aplica cambios. Sin esto solo hace dry-run.")
    args = parser.parse_args()

    cfg = read_db_profile_config(args.profile)
    db_name = str(args.db_name or cfg["db_name"])
    db = DatabaseManager.from_profile(args.profile, db_name=db_name)

    conn = db.get_connection(db_name)
    changed: list[dict[str, object]] = []
    missing: list[int] = []
    try:
        cur = conn.cursor()
        problem_cols = _problem_columns(cur)
        for problem_id, (old, new) in REPAIRS.items():
            cur.execute(
                """
                SELECT
                    id,
                    numero_original,
                    COALESCE(curso,'') AS curso,
                    COALESCE(tema,'') AS tema,
                    COALESCE(libro_codigo,'') AS libro_codigo,
                    COALESCE(codigo_instancia,'') AS codigo_instancia,
                    COALESCE(enunciado_latex,'') AS enunciado_latex
                FROM problemas
                WHERE id = %s;
                """,
                (problem_id,),
            )
            row = cur.fetchone()
            if not row:
                missing.append(problem_id)
                continue
            columns = [desc[0] for desc in cur.description]
            data = dict(zip(columns, row))
            text = str(data.get("enunciado_latex") or "")
            if old not in text:
                continue
            repaired = text.replace(old, new)
            changed.append({**data, "repaired_enunciado_latex": repaired})

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
                    params.append("repair-system-equation-rowbreaks")
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
                    "changed": len(changed),
                    "changed_ids": [int(row["id"]) for row in changed],
                    "missing_ids": missing,
                    "backup": str(backup),
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
