from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_jsonl(path: Path, *, limit: int = 0) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[:limit] if limit and limit > 0 else rows


def _normalize_for_metric(value: str) -> str:
    text = str(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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


def _expected_prefix_ok(target: str, prediction: str) -> bool:
    target = str(target or "").strip()
    prediction = str(prediction or "").strip()
    if target.startswith("[CONT.]"):
        return prediction.startswith("[CONT.]")
    match = re.match(r"^<\s*(\d{1,4})\s*\.\s*>", target)
    if not match:
        return bool(prediction)
    expected = match.group(1).lstrip("0") or "0"
    pred_match = re.match(r"^<\s*(\d{1,4})\s*\.\s*>", prediction)
    if not pred_match:
        return False
    got = pred_match.group(1).lstrip("0") or "0"
    return got == expected


def _option_labels(text: str) -> set[str]:
    return set(re.findall(r"(?<![A-Za-z])([A-E])\)", str(text or "")))


def _format_score(target: str, prediction: str) -> dict[str, Any]:
    target_options = _option_labels(target)
    pred_options = _option_labels(prediction)
    return {
        "prefix_ok": _expected_prefix_ok(target, prediction),
        "options_recall": (len(target_options & pred_options) / len(target_options)) if target_options else 1.0,
        "target_options": sorted(target_options),
        "pred_options": sorted(pred_options),
    }


def _load_prediction_map(path: Path) -> dict[str, str]:
    if not path:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return {str(key): str(value or "") for key, value in payload.items()}
    if isinstance(payload, list):
        out: dict[str, str] = {}
        for item in payload:
            if isinstance(item, dict):
                out[str(item.get("id") or item.get("record_id") or "")] = str(
                    item.get("prediction") or item.get("text") or ""
                )
        return {key: value for key, value in out.items() if key}
    return {}


def _predict_with_local_ocr(dataset_dir: Path, row: dict[str, Any]) -> str:
    from modulos.modulo0_transcriptor.scan_pipeline.extractor import ScanExtractor

    extractor = ScanExtractor(provider="ocr", timeout_s=180, strict_json=False)
    _items, raw = extractor.extract_from_image(
        image_path=dataset_dir / str(row.get("image") or ""),
        curso=str(row.get("curso") or ""),
        tema=str(row.get("tema") or ""),
        start_n=1,
    )
    return str(raw or "")


def evaluate_rows(
    rows: list[dict[str, Any]],
    *,
    dataset_dir: Path,
    candidate_field: str = "raw_candidate",
    predictions: dict[str, str] | None = None,
    provider: str = "field",
) -> dict[str, Any]:
    predictions = predictions or {}
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        target = _normalize_for_metric(str(row.get("text") or ""))
        error = ""
        if provider == "local_ocr":
            try:
                prediction = _predict_with_local_ocr(dataset_dir, row)
            except Exception as exc:
                prediction = ""
                error = str(exc)
        elif predictions:
            prediction = predictions.get(str(row.get("id") or ""), "")
        else:
            prediction = str(row.get(candidate_field) or "")
        prediction = _normalize_for_metric(prediction)
        distance = _edit_distance(prediction, target)
        format_score = _format_score(target, prediction)
        results.append(
            {
                "id": str(row.get("id") or ""),
                "image": str(row.get("image") or ""),
                "char_distance": distance,
                "target_chars": len(target),
                "cer": distance / max(1, len(target)),
                "exact": prediction == target,
                "prefix_ok": bool(format_score["prefix_ok"]),
                "options_recall": float(format_score["options_recall"]),
                "prediction_chars": len(prediction),
                "error": error,
                "target_preview": target[:220],
                "prediction_preview": prediction[:220],
            }
        )
    total = len(results)
    if total == 0:
        return {
            "schema_version": "local_math_ocr_eval_v1",
            "samples": 0,
            "results": [],
        }
    errored = [row for row in results if row["error"]]
    return {
        "schema_version": "local_math_ocr_eval_v1",
        "samples": total,
        "errors": len(errored),
        "exact_match_rate": sum(1 for row in results if row["exact"]) / total,
        "prefix_ok_rate": sum(1 for row in results if row["prefix_ok"]) / total,
        "avg_cer": sum(float(row["cer"]) for row in results) / total,
        "avg_options_recall": sum(float(row["options_recall"]) for row in results) / total,
        "avg_prediction_chars": sum(int(row["prediction_chars"]) for row in results) / total,
        "worst": sorted(results, key=lambda row: float(row["cer"]), reverse=True)[:10],
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evalua candidatos OCR contra dataset local imagen->texto corregido.")
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--split", default="test", choices=["train", "validation", "test"])
    parser.add_argument("--candidate-field", default="raw_candidate")
    parser.add_argument("--predictions", default="", help="JSON dict id->prediction o lista con id/prediction.")
    parser.add_argument("--provider", default="field", choices=["field", "predictions", "local_ocr"])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", default="")
    parser.add_argument("--hide-results", action="store_true")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    rows = _load_jsonl(dataset_dir / f"{args.split}.jsonl", limit=max(0, int(args.limit or 0)))
    predictions = _load_prediction_map(Path(args.predictions).expanduser().resolve()) if args.predictions else {}
    provider = "predictions" if args.predictions and args.provider == "field" else args.provider
    summary = evaluate_rows(
        rows,
        dataset_dir=dataset_dir,
        candidate_field=args.candidate_field,
        predictions=predictions,
        provider=provider,
    )
    output = dict(summary)
    if args.hide_results:
        output.pop("results", None)
    text = json.dumps(output, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
