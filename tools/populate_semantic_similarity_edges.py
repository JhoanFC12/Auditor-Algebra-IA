from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.semantic_profile_db import populate_problem_similarity_edges


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Genera relaciones semilla en problem_similarity_edges desde perfiles semanticos. "
            "Por defecto es dry-run; usa --apply para escribir en BD."
        )
    )
    parser.add_argument("--profile", default="local_mirror", help="Perfil de BD: local_mirror, cloud o active.")
    parser.add_argument("--db-name", default="", help="Nombre de BD. Si se omite usa el perfil.")
    parser.add_argument("--limit", type=int, default=0, help="Limite de perfiles de problema a comparar.")
    parser.add_argument("--problem-id", action="append", type=int, default=[], help="ID especifico. Repetible.")
    parser.add_argument("--top-k", type=int, default=5, help="Maximo de similares por problema.")
    parser.add_argument("--threshold", type=float, default=0.15, help="Score minimo para guardar/reportar.")
    parser.add_argument("--apply", action="store_true", help="Escribe/upsertea en problem_similarity_edges.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Recalcula edges existentes no verificados por humano.",
    )
    args = parser.parse_args()

    from database.connection import DatabaseManager

    db = DatabaseManager.from_profile(args.profile, db_name=args.db_name or None)
    conn = db.get_connection(db.db_name)
    try:
        report = populate_problem_similarity_edges(
            conn,
            apply=bool(args.apply),
            refresh=bool(args.refresh),
            limit=max(0, int(args.limit or 0)),
            problem_ids=list(args.problem_id or []),
            top_k=max(1, int(args.top_k or 1)),
            threshold=max(0.0, float(args.threshold or 0.0)),
        )
    finally:
        conn.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if int(report.get("errors") or 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
