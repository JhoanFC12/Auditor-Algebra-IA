from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.semantic_similarity_review import (
    SIMILARITY_FEEDBACK_SCHEMA_VERSION,
    build_similarity_feedback_manifest,
    fetch_similarity_feedback_examples,
)
from modulos.semantic_similarity_seed import SIMILARITY_MODEL_ID


def _parse_statuses(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Exporta revisiones humanas de problem_similarity_edges como dataset JSONL "
            "para entrenar/evaluar similitud semantica."
        )
    )
    parser.add_argument("--profile", default="local_mirror", help="Perfil de BD: local_mirror, cloud o active.")
    parser.add_argument("--db-name", default="", help="Nombre de BD. Si se omite usa el perfil.")
    parser.add_argument("--model-id", default=SIMILARITY_MODEL_ID, help="Modelo de similitud a exportar.")
    parser.add_argument(
        "--statuses",
        default="aceptado,rechazado,dudoso",
        help="Estados revisados separados por coma.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limite de ejemplos a exportar.")
    parser.add_argument("--output-jsonl", required=True, help="Ruta del JSONL de salida.")
    parser.add_argument("--manifest", default="", help="Ruta del manifest. Por defecto queda junto al JSONL.")
    args = parser.parse_args()

    from database.connection import DatabaseManager

    output_jsonl = Path(args.output_jsonl).expanduser()
    manifest_path = Path(args.manifest).expanduser() if args.manifest else output_jsonl.with_name("manifest.json")
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    statuses = _parse_statuses(args.statuses)

    db = DatabaseManager.from_profile(args.profile, db_name=args.db_name or None)
    conn = db.get_connection(db.db_name)
    try:
        examples = fetch_similarity_feedback_examples(
            conn,
            model_id=args.model_id or SIMILARITY_MODEL_ID,
            statuses=statuses,
            limit=max(0, int(args.limit or 0)),
        )
    finally:
        conn.close()

    with output_jsonl.open("w", encoding="utf-8") as fh:
        for row in examples:
            row.setdefault("schema_version", SIMILARITY_FEEDBACK_SCHEMA_VERSION)
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    manifest = build_similarity_feedback_manifest(
        examples,
        db_profile=args.profile,
        db_name=db.db_name,
        model_id=args.model_id or SIMILARITY_MODEL_ID,
        statuses=statuses,
        output_jsonl=str(output_jsonl),
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
