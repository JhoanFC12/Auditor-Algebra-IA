from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.semantic_profile_seed import build_problem_semantic_seed, write_seed_profile


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception as exc:
            rows.append({"_error": f"line {line_no}: invalid_json:{exc}"})
            continue
        if not isinstance(payload, dict):
            rows.append({"_error": f"line {line_no}: invalid_payload:not_object"})
            continue
        rows.append(payload)
    return rows


def _first_text(row: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _safe_filename(problem_id: str) -> str:
    out = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(problem_id or "").strip())
    return out.strip("_") or "problem"


def build_profiles_from_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    profiles: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if row.get("_error"):
            skipped.append({"index": index, "error": row["_error"]})
            continue
        problem_id = _first_text(row, "problem_id", "problema_id", "id", "record_id") or str(index)
        final_latex = _first_text(row, "latex_rendered_item", "final_latex", "item_latex", "enunciado_latex")
        if not final_latex:
            skipped.append({"index": index, "problem_id": problem_id, "error": "missing:final_latex"})
            continue
        raw_ocr = _first_text(row, "raw_ocr", "ocr_crudo")
        profiles.append(build_problem_semantic_seed(problem_id=problem_id, final_latex=final_latex, raw_ocr=raw_ocr))
    return profiles, skipped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Construye perfiles semanticos semilla desde problemas finales JSONL sin escribir en BD."
    )
    parser.add_argument("--input", required=True, help="JSONL con problem_id/final_latex/raw_ocr.")
    parser.add_argument("--output-jsonl", required=True, help="Ruta del JSONL de perfiles semilla.")
    parser.add_argument("--output-dir", default="", help="Opcional: carpeta con un JSON por problema.")
    parser.add_argument("--manifest", default="", help="Opcional: ruta del manifest JSON.")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser()
    output_jsonl = Path(args.output_jsonl).expanduser()
    rows = _iter_jsonl(input_path)
    profiles, skipped = build_profiles_from_rows(rows)

    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    output_jsonl.write_text(
        "\n".join(json.dumps(profile, ensure_ascii=False, sort_keys=True) for profile in profiles) + ("\n" if profiles else ""),
        encoding="utf-8",
    )
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else None
    if output_dir is not None:
        for profile in profiles:
            write_seed_profile(output_dir / f"{_safe_filename(str(profile['problem_id']))}.profile.json", profile)

    manifest = {
        "schema_version": "semantic_seed_profile_export_manifest_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "input": str(input_path),
        "output_jsonl": str(output_jsonl),
        "output_dir": str(output_dir or ""),
        "profiles_total": len(profiles),
        "skipped_total": len(skipped),
        "skipped": skipped,
    }
    manifest_path = Path(args.manifest).expanduser() if args.manifest else output_jsonl.with_name("manifest.json")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if not skipped else 2


if __name__ == "__main__":
    raise SystemExit(main())
