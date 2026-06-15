from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.semantic_profile_db import (
    populate_problem_concept_graph,
    populate_problem_figure_seed_profiles,
    populate_problem_semantic_seed_profiles,
    populate_solution_semantic_seed_profiles,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Genera perfiles semanticos semilla desde public.problemas. "
            "Por defecto no escribe en BD; usa --apply para poblar las tablas semanticas."
        )
    )
    parser.add_argument("--profile", default="local_mirror", help="Perfil de BD: local_mirror, cloud o active.")
    parser.add_argument("--db-name", default="", help="Nombre de BD. Si se omite usa el perfil.")
    parser.add_argument("--limit", type=int, default=0, help="Limite de problemas a procesar.")
    parser.add_argument("--problem-id", action="append", type=int, default=[], help="ID especifico. Repetible.")
    parser.add_argument(
        "--kind",
        choices=("problem", "figure", "solution", "concept", "all"),
        default="problem",
        help="Perfil a generar: problema, grafico, solucion, conceptos o todos.",
    )
    parser.add_argument("--apply", action="store_true", help="Escribe/upsertea en las tablas semanticas.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Recalcula perfiles existentes no verificados por humano. Sin esto solo llena faltantes.",
    )
    args = parser.parse_args()

    from database.connection import DatabaseManager

    db = DatabaseManager.from_profile(args.profile, db_name=args.db_name or None)
    conn = db.get_connection(db.db_name)
    try:
        common = {
            "apply": bool(args.apply),
            "refresh": bool(args.refresh),
            "limit": max(0, int(args.limit or 0)),
            "problem_ids": list(args.problem_id or []),
        }
        if args.kind == "problem":
            report = populate_problem_semantic_seed_profiles(conn, **common)
        elif args.kind == "figure":
            report = populate_problem_figure_seed_profiles(conn, **common)
        elif args.kind == "solution":
            report = populate_solution_semantic_seed_profiles(conn, **common)
        elif args.kind == "concept":
            concept_common = {
                "apply": bool(args.apply),
                "limit": max(0, int(args.limit or 0)),
                "problem_ids": list(args.problem_id or []),
            }
            report = populate_problem_concept_graph(conn, **concept_common)
        else:
            problem_report = populate_problem_semantic_seed_profiles(conn, **common)
            figure_report = populate_problem_figure_seed_profiles(conn, **common)
            solution_report = populate_solution_semantic_seed_profiles(conn, **common)
            concept_report = populate_problem_concept_graph(
                conn,
                apply=bool(args.apply),
                limit=max(0, int(args.limit or 0)),
                problem_ids=list(args.problem_id or []),
            )
            report = {
                "schema_version": "semantic_seed_profiles_combined_report_v1",
                "dry_run": not bool(args.apply),
                "refresh": bool(args.refresh),
                "problem": problem_report,
                "figure": figure_report,
                "solution": solution_report,
                "concept": concept_report,
                "errors": (
                    int(problem_report.get("errors") or 0)
                    + int(figure_report.get("errors") or 0)
                    + int(solution_report.get("errors") or 0)
                    + int(concept_report.get("errors") or 0)
                ),
            }
    finally:
        conn.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if int(report.get("errors") or 0) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
