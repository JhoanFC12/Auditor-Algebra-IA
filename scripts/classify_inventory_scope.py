from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / ".cache" / "book_catalog" / "repository_inventory" / "whatsapp_books_master_complete.csv"
DEFAULT_STATE = (
    ROOT
    / ".cache"
    / "book_catalog"
    / "repository_inventory"
    / "curated_math_books"
    / "review_decisions.json"
)
TARGET_COURSES = {
    "Aritmetica",
    "Algebra",
    "Geometria",
    "Trigonometria",
    "Razonamiento matematico",
    "Geometria analitica",
    "Geometria del espacio",
    "Fisica",
    "Quimica",
}
SOURCE = "bulk_scope_v1"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": "book_inventory_review_v1", "updated_at": None, "decisions": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def classify(catalog_path: Path, state_path: Path, *, apply: bool) -> dict:
    state = load_state(state_path)
    previous = state.setdefault("decisions", {})
    decisions = dict(previous)
    totals = {
        "target_confirmed": 0,
        "outside_excluded": 0,
        "doubtful_pending": 0,
        "manual_preserved": 0,
    }

    with catalog_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))

    for row in rows:
        item_id = row.get("drive_id", "").strip()
        if not item_id:
            continue
        existing = previous.get(item_id)
        if existing and existing.get("classification_source") != SOURCE:
            totals["manual_preserved"] += 1
            continue

        course = (row.get("course") or "Pendiente").strip()
        if course in TARGET_COURSES:
            decisions[item_id] = {
                "review_state": "confirmado",
                "confirmed_course": course,
                "material_type": existing.get("material_type", "libro_problemas") if existing else "libro_problemas",
                "multiple_choice": existing.get("multiple_choice", "por_verificar") if existing else "por_verificar",
                "notes": existing.get("notes", "") if existing else "",
                "reviewed_at": now(),
                "classification_source": SOURCE,
            }
            totals["target_confirmed"] += 1
        elif course and course != "Pendiente":
            decisions[item_id] = {
                "review_state": "excluido",
                "confirmed_course": course,
                "material_type": "otro",
                "multiple_choice": "por_verificar",
                "notes": "Excluido automaticamente: curso fuera del alcance definido.",
                "reviewed_at": now(),
                "classification_source": SOURCE,
            }
            totals["outside_excluded"] += 1
        else:
            if existing and existing.get("classification_source") == SOURCE:
                decisions.pop(item_id, None)
            totals["doubtful_pending"] += 1

    result = {
        "schema_version": "inventory_scope_classification_report_v1",
        "catalog": str(catalog_path),
        "state": str(state_path),
        "apply": apply,
        "catalog_total": len(rows),
        **totals,
        "decisions_after": len(decisions),
    }
    if apply:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        if state_path.exists():
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = state_path.with_name(f"{state_path.stem}.before-scope-{stamp}{state_path.suffix}")
            shutil.copy2(state_path, backup)
            result["backup"] = str(backup)
        state["decisions"] = decisions
        state["updated_at"] = now()
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(state_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Clasifica el inventario por alcance y conserva las dudas")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(classify(args.catalog, args.state, apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
