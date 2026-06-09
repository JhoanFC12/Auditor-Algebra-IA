from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.modulo0_transcriptor.scan_pipeline.renderer import render_item
from modulos.modulo0_transcriptor.scan_pipeline.schema import OPTION_LABELS, ScanItem
from modulos.modulo0_transcriptor.scan_pipeline.validator import validate_rendered_item


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_items(raw: Any, *, curso_default: str = "", tema_default: str = "") -> List[ScanItem]:
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        rows = raw["items"]
    elif isinstance(raw, dict) and raw.get("schema") == "ScanItemJSON-v1":
        rows = [raw]
    elif isinstance(raw, list):
        rows = raw
    else:
        rows = []
    out: List[ScanItem] = []
    for idx, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            continue
        out.append(
            ScanItem.from_dict(
                item,
                default_n=idx,
                curso=curso_default or str(item.get("curso", "") or ""),
                tema=tema_default or str(item.get("tema", "") or ""),
            )
        )
    return out


def _edit_distance(a: str, b: str) -> int:
    x = a or ""
    y = b or ""
    if x == y:
        return 0
    if not x:
        return len(y)
    if not y:
        return len(x)
    prev = list(range(len(y) + 1))
    for i, cx in enumerate(x, start=1):
        curr = [i]
        for j, cy in enumerate(y, start=1):
            cost = 0 if cx == cy else 1
            curr.append(min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = curr
    return prev[-1]


def _pair_items(gold_items: List[ScanItem], pred_items: List[ScanItem]) -> List[Tuple[ScanItem, ScanItem | None]]:
    pred_by_n: Dict[int, ScanItem] = {int(item.n): item for item in pred_items}
    pairs: List[Tuple[ScanItem, ScanItem | None]] = []
    for g in gold_items:
        pairs.append((g, pred_by_n.get(int(g.n))))
    return pairs


def evaluate(gold_dir: Path, pred_dir: Path) -> Dict[str, Any]:
    files = sorted([p for p in gold_dir.glob("*.json") if p.is_file()])
    total_items = 0
    format_pass_items = 0
    statement_dist_sum = 0
    option_dist_sum = 0
    option_count = 0
    missing_pred_files = []

    for gold_file in files:
        pred_file = pred_dir / gold_file.name
        gold_items = _normalize_items(_load_json(gold_file))
        if not pred_file.exists():
            missing_pred_files.append(gold_file.name)
            total_items += len(gold_items)
            continue
        pred_items = _normalize_items(_load_json(pred_file))
        for gold_item, pred_item in _pair_items(gold_items, pred_items):
            total_items += 1
            if pred_item is None:
                statement_dist_sum += len(gold_item.statement)
                for label in OPTION_LABELS:
                    option_dist_sum += len(gold_item.options.get(label, ""))
                    option_count += 1
                continue

            rendered = render_item(pred_item)
            if not validate_rendered_item(rendered, item=pred_item):
                format_pass_items += 1
            statement_dist_sum += _edit_distance(gold_item.statement, pred_item.statement)
            for label in OPTION_LABELS:
                option_dist_sum += _edit_distance(gold_item.options.get(label, ""), pred_item.options.get(label, ""))
                option_count += 1

    format_pass = (format_pass_items / total_items) if total_items else 0.0
    avg_statement_dist = (statement_dist_sum / total_items) if total_items else 0.0
    avg_option_dist = (option_dist_sum / option_count) if option_count else 0.0
    return {
        "gold_files": len(files),
        "missing_pred_files": missing_pred_files,
        "items_total": total_items,
        "format_pass": format_pass,
        "avg_statement_edit_distance": avg_statement_dist,
        "avg_option_edit_distance": avg_option_dist,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evalua dataset de escaneo: FormatPass + edit distance.")
    parser.add_argument("--gold-dir", required=True)
    parser.add_argument("--pred-dir", required=True)
    parser.add_argument("--out", default="", help="Salida JSON opcional.")
    args = parser.parse_args()

    gold_dir = Path(args.gold_dir).expanduser().resolve()
    pred_dir = Path(args.pred_dir).expanduser().resolve()
    if not gold_dir.exists():
        print(f"[eval_scan_dataset] ERROR: gold dir not found: {gold_dir}")
        return 1
    if not pred_dir.exists():
        print(f"[eval_scan_dataset] ERROR: pred dir not found: {pred_dir}")
        return 1

    summary = evaluate(gold_dir, pred_dir)
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
