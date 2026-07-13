from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modulos.instance_factory.continuations import continuation_flags_enabled, has_continuation_marker
from modulos.instance_factory.models import InstancePipelineContext, StagingProblemRecord
from modulos.instance_factory.pipeline import InstancePdfPipelineService
from modulos.instance_factory.staging import InstanceStagingStore


DEFAULT_STAGING_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "staging"
DEFAULT_REPORTS_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "reports"
DEFAULT_CROPS_LIVE_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "datasets" / "problem_crops_live"
DEFAULT_TRAINING_BANK = REPO_ROOT / ".cache" / "transcriptor_runs" / "datasets" / "normalizer_training_bank" / "samples.jsonl"


class _ReadOnlyRecordStore:
    def __init__(self, context: InstancePipelineContext, rows: list[StagingProblemRecord]) -> None:
        self.context = context
        self._rows = list(rows)

    def load_records(self) -> list[StagingProblemRecord]:
        return list(self._rows)


def _load_store(staging_dir: Path) -> InstanceStagingStore:
    context = InstancePipelineContext(
        book_code=staging_dir.name,
        instance_type="continuity_eval",
        staging_root_override=str(staging_dir),
    )
    return InstanceStagingStore(context, root=staging_dir)


def _record_label_text(record: StagingProblemRecord) -> str:
    normalized = record.normalized if isinstance(record.normalized, dict) else {}
    review = record.review if isinstance(record.review, dict) else {}
    values = [
        record.raw_ocr,
        normalized.get("enunciado_latex"),
        normalized.get("latex_rendered_item"),
        review.get("final_latex"),
        review.get("latex_rendered_item"),
        review.get("human_review_text"),
    ]
    return "\n".join(str(value or "").strip() for value in values if str(value or "").strip())


def _parent_continuation_ids(rows: list[StagingProblemRecord]) -> dict[str, set[str]]:
    by_parent: dict[str, set[str]] = {}
    for row in rows:
        parent_id = str(row.record_id or "").strip()
        if not parent_id:
            continue
        normalized = row.normalized if isinstance(row.normalized, dict) else {}
        fused = normalized.get("continuaciones_fusionadas")
        if not isinstance(fused, list):
            continue
        for item in fused:
            if not isinstance(item, dict):
                continue
            for key in ("record_id", "crop_id"):
                child_id = str(item.get(key) or "").strip()
                if child_id:
                    by_parent.setdefault(parent_id, set()).add(child_id)
    return by_parent


def _is_labeled_continuation(
    parent: StagingProblemRecord,
    child: StagingProblemRecord,
    parent_ids: dict[str, set[str]],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    parent_id = str(parent.record_id or "").strip()
    child_ids = {str(child.record_id or "").strip(), str(child.crop_id or "").strip()}
    child_ids.discard("")
    if parent_id and parent_ids.get(parent_id, set()).intersection(child_ids):
        reasons.append("parent.continuaciones_fusionadas")

    child_source = child.source if isinstance(child.source, dict) else {}
    merged_into = str(child_source.get("merged_into_record_id") or "").strip()
    if merged_into and merged_into == parent_id:
        reasons.append("child.source.merged_into_record_id")

    child_normalized = child.normalized if isinstance(child.normalized, dict) else {}
    continuation = child_normalized.get("continuacion")
    if isinstance(continuation, dict) and continuation_flags_enabled(continuation):
        explicit_parent = str(continuation.get("parent_record_id") or "").strip()
        if not explicit_parent or explicit_parent == parent_id:
            reasons.append("child.normalized.continuacion")

    if has_continuation_marker(_record_label_text(child)):
        reasons.append("child.text_cont_marker")

    return bool(reasons), reasons


def _classify_pair(predicted: bool, expected: bool) -> str:
    if predicted and expected:
        return "TP"
    if predicted and not expected:
        return "FP"
    if not predicted and expected:
        return "FN"
    return "TN"


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def evaluate_staging_dir(
    staging_dir: Path,
    *,
    min_confidence: float,
    max_pairs: int | None = None,
) -> dict[str, Any]:
    store = _load_store(staging_dir)
    rows = store.load_records()
    if max_pairs is not None:
        rows = rows[: max(0, int(max_pairs)) + 1]
    service = InstancePdfPipelineService(store.context, staging_store=store)
    parent_ids = _parent_continuation_ids(rows)
    candidate_rows = service.detect_continuation_candidates(
        min_confidence=min_confidence,
        max_candidates=max(1, len(rows)),
    )
    predictions = {
        (str(item.get("parent_record_id") or ""), str(item.get("continuation_record_id") or "")): item
        for item in candidate_rows
        if item.get("recommendation") == "merge"
    }

    pairs: list[dict[str, Any]] = []
    counts = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
    for index in range(max(0, len(rows) - 1)):
        parent = rows[index]
        child = rows[index + 1]
        key = (str(parent.record_id or ""), str(child.record_id or ""))
        predicted = key in predictions
        expected, label_reasons = _is_labeled_continuation(parent, child, parent_ids)
        bucket = _classify_pair(predicted, expected)
        counts[bucket] += 1
        candidate = predictions.get(key) or {}
        pairs.append(
            {
                "index": index,
                "bucket": bucket,
                "expected": expected,
                "predicted": predicted,
                "confidence": candidate.get("confidence"),
                "parent_record_id": parent.record_id,
                "continuation_record_id": child.record_id,
                "parent_crop": Path(str(parent.crop_path or "")).name,
                "continuation_crop": Path(str(child.crop_path or "")).name,
                "parent_page": (parent.source or {}).get("page_number", (parent.source or {}).get("source_page_number")),
                "continuation_page": (child.source or {}).get("page_number", (child.source or {}).get("source_page_number")),
                "label_reasons": label_reasons,
                "prediction_reasons": list(candidate.get("reasons") or []),
                "prediction_warnings": list(candidate.get("warnings") or []),
            }
        )

    tp, fp, tn, fn = counts["TP"], counts["FP"], counts["TN"], counts["FN"]
    total = max(1, tp + fp + tn + fn)
    metrics = {
        "accuracy": _ratio(tp + tn, total),
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "f1": round((2 * tp / max(1, 2 * tp + fp + fn)), 4),
        "false_positive_rate": _ratio(fp, fp + tn),
        "false_negative_rate": _ratio(fn, fn + tp),
    }
    return {
        "schema_version": "continuity_session_eval_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "staging_dir": str(staging_dir),
        "records_total": len(rows),
        "pairs_total": max(0, len(rows) - 1),
        "min_confidence": min_confidence,
        "counts": counts,
        "metrics": metrics,
        "merge_predictions_total": len(predictions),
        "labeled_continuations_total": tp + fn,
        "pairs": pairs,
        "errors": [item for item in pairs if item["bucket"] in {"FP", "FN"}],
    }


def _safe_json_load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _load_training_texts(path: Path = DEFAULT_TRAINING_BANK) -> dict[str, dict[str, str]]:
    texts: dict[str, dict[str, str]] = {}
    if not path.exists():
        return texts
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            record_id = str(payload.get("record_id") or payload.get("crop_id") or payload.get("source_record_id") or "").strip()
            if not record_id:
                continue
            texts[record_id] = {
                "raw_ocr": str(payload.get("raw_ocr") or payload.get("ocr_raw") or "").strip(),
                "final_latex": str(payload.get("final_latex") or payload.get("latex_rendered_item") or "").strip(),
            }
    return texts


def _crop_path_from_live_payload(payload: dict[str, Any], crops_root: Path) -> Path:
    raw = str(payload.get("crop_path") or "").strip()
    if raw:
        return Path(raw)
    rel = str(payload.get("crop_image_rel") or "").strip()
    if rel:
        return crops_root / rel.replace("/", "\\")
    crop_id = str(payload.get("crop_id") or payload.get("record_id") or "").strip()
    return crops_root / "images" / f"{crop_id}.png"


def _live_record_from_payload(
    payload: dict[str, Any],
    *,
    crops_root: Path,
    training_texts: dict[str, dict[str, str]],
) -> StagingProblemRecord:
    crop_id = str(payload.get("crop_id") or payload.get("record_id") or "").strip()
    texts = training_texts.get(crop_id, {})
    return StagingProblemRecord(
        record_id=crop_id,
        crop_id=crop_id,
        crop_path=str(_crop_path_from_live_payload(payload, crops_root)),
        raw_ocr=str(texts.get("raw_ocr") or payload.get("corrected_text") or payload.get("ocr_text") or "").strip(),
        normalized={"latex_rendered_item": str(texts.get("final_latex") or "").strip()} if texts.get("final_latex") else {},
        source={
            "page_number": payload.get("source_page_number") or payload.get("page_number"),
            "source_page_number": payload.get("source_page_number") or payload.get("page_number"),
            "source_order": payload.get("source_order") or payload.get("problem_index") or payload.get("box_index"),
            "bbox_px": list(payload.get("bbox_px") or [0, 0, 0, 0])[:4],
            "page_image": str(payload.get("source_page_image") or ""),
            "book_code": str(payload.get("book_code") or ""),
            "instance_type": str(payload.get("instance_type") or payload.get("source_instance") or ""),
            "project_name": str(payload.get("project_name") or ""),
        },
    )


def _load_live_groups(
    *,
    contains: str,
    crops_root: Path = DEFAULT_CROPS_LIVE_ROOT,
) -> dict[str, list[StagingProblemRecord]]:
    training_texts = _load_training_texts()
    records_dir = crops_root / "records"
    contains_l = str(contains or "").strip().lower()
    groups: dict[str, list[StagingProblemRecord]] = {}
    for path in records_dir.glob("*.json"):
        payload = _safe_json_load(path)
        if not payload:
            continue
        haystack = " ".join(
            str(payload.get(key) or "")
            for key in ("book_code", "instance_type", "source_instance", "project_name", "source_pdf_path")
        ).lower()
        if contains_l and contains_l not in haystack:
            continue
        record = _live_record_from_payload(payload, crops_root=crops_root, training_texts=training_texts)
        if not Path(record.crop_path).exists():
            continue
        source = record.source if isinstance(record.source, dict) else {}
        key = f"{source.get('book_code')}__{source.get('instance_type')}"
        groups.setdefault(key, []).append(record)
    for key, rows in list(groups.items()):
        groups[key] = sorted(
            rows,
            key=lambda row: (
                int((row.source or {}).get("page_number") or 0),
                int((row.source or {}).get("source_order") or 0),
                str(row.record_id or ""),
            ),
        )
    return {key: rows for key, rows in groups.items() if rows}


def evaluate_live_group(
    group_name: str,
    rows: list[StagingProblemRecord],
    *,
    min_confidence: float,
    max_pairs: int | None = None,
) -> dict[str, Any]:
    if max_pairs is not None:
        rows = rows[: max(0, int(max_pairs)) + 1]
    left, _, right = group_name.partition("__")
    context = InstancePipelineContext(book_code=left or group_name, instance_type=right or "live")
    store = _ReadOnlyRecordStore(context, rows)
    service = InstancePdfPipelineService(context, staging_store=store)  # type: ignore[arg-type]
    parent_ids = _parent_continuation_ids(rows)
    candidate_rows = service.detect_continuation_candidates(
        min_confidence=min_confidence,
        max_candidates=max(1, len(rows)),
    )
    predictions = {
        (str(item.get("parent_record_id") or ""), str(item.get("continuation_record_id") or "")): item
        for item in candidate_rows
        if item.get("recommendation") == "merge"
    }

    pairs: list[dict[str, Any]] = []
    counts = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
    for index in range(max(0, len(rows) - 1)):
        parent = rows[index]
        child = rows[index + 1]
        key = (str(parent.record_id or ""), str(child.record_id or ""))
        predicted = key in predictions
        expected, label_reasons = _is_labeled_continuation(parent, child, parent_ids)
        bucket = _classify_pair(predicted, expected)
        counts[bucket] += 1
        candidate = predictions.get(key) or {}
        pairs.append(
            {
                "index": index,
                "bucket": bucket,
                "expected": expected,
                "predicted": predicted,
                "confidence": candidate.get("confidence"),
                "parent_record_id": parent.record_id,
                "continuation_record_id": child.record_id,
                "parent_crop": Path(str(parent.crop_path or "")).name,
                "continuation_crop": Path(str(child.crop_path or "")).name,
                "parent_page": (parent.source or {}).get("page_number", (parent.source or {}).get("source_page_number")),
                "continuation_page": (child.source or {}).get("page_number", (child.source or {}).get("source_page_number")),
                "label_reasons": label_reasons,
                "prediction_reasons": list(candidate.get("reasons") or []),
                "prediction_warnings": list(candidate.get("warnings") or []),
            }
        )

    tp, fp, tn, fn = counts["TP"], counts["FP"], counts["TN"], counts["FN"]
    total = max(1, tp + fp + tn + fn)
    return {
        "schema_version": "continuity_session_eval_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "live_group": group_name,
        "records_total": len(rows),
        "pairs_total": max(0, len(rows) - 1),
        "min_confidence": min_confidence,
        "counts": counts,
        "metrics": {
            "accuracy": _ratio(tp + tn, total),
            "precision": _ratio(tp, tp + fp),
            "recall": _ratio(tp, tp + fn),
            "f1": round((2 * tp / max(1, 2 * tp + fp + fn)), 4),
            "false_positive_rate": _ratio(fp, fp + tn),
            "false_negative_rate": _ratio(fn, fn + tp),
        },
        "merge_predictions_total": len(predictions),
        "labeled_continuations_total": tp + fn,
        "pairs": pairs,
        "errors": [item for item in pairs if item["bucket"] in {"FP", "FN"}],
    }


def _resolve_staging_dirs(raw_values: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in raw_values:
        value = str(raw or "").strip()
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            candidate = DEFAULT_STAGING_ROOT / value
            path = candidate if candidate.exists() else (REPO_ROOT / value)
        out.append(path)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Evalua continuidad/fusion de crops en una sesion de staging.")
    parser.add_argument("--session", action="append", default=[], help="Carpeta de staging o nombre bajo .cache/transcriptor_runs/staging.")
    parser.add_argument("--contains", default="", help="Filtra sesiones de staging cuyo nombre contenga este texto.")
    parser.add_argument("--live-contains", default="", help="Filtra sesiones bajo problem_crops_live por texto.")
    parser.add_argument("--min-confidence", type=float, default=0.35)
    parser.add_argument("--max-pairs", type=int, default=None)
    parser.add_argument("--output", default="", help="Ruta JSON de salida. Si se omite, se guarda en reports.")
    args = parser.parse_args()

    sessions = _resolve_staging_dirs(args.session)
    contains = str(args.contains or "").strip().lower()
    if contains:
        sessions.extend(path for path in DEFAULT_STAGING_ROOT.iterdir() if path.is_dir() and contains in path.name.lower())

    reports = [evaluate_staging_dir(path, min_confidence=args.min_confidence, max_pairs=args.max_pairs) for path in sessions]
    live_contains = str(args.live_contains or "").strip()
    if live_contains:
        groups = _load_live_groups(contains=live_contains)
        reports.extend(
            evaluate_live_group(key, rows, min_confidence=args.min_confidence, max_pairs=args.max_pairs)
            for key, rows in sorted(groups.items())
        )
    if not reports:
        raise SystemExit("Indica --session, --contains o --live-contains con resultados.")
    aggregate_counts = {"TP": 0, "FP": 0, "TN": 0, "FN": 0}
    for report in reports:
        for key in aggregate_counts:
            aggregate_counts[key] += int(report["counts"].get(key) or 0)
    tp, fp, tn, fn = aggregate_counts["TP"], aggregate_counts["FP"], aggregate_counts["TN"], aggregate_counts["FN"]
    aggregate = {
        "sessions_total": len(reports),
        "counts": aggregate_counts,
        "metrics": {
            "accuracy": _ratio(tp + tn, tp + fp + tn + fn),
            "precision": _ratio(tp, tp + fp),
            "recall": _ratio(tp, tp + fn),
            "f1": round((2 * tp / max(1, 2 * tp + fp + fn)), 4),
            "false_positive_rate": _ratio(fp, fp + tn),
            "false_negative_rate": _ratio(fn, fn + tp),
        },
    }
    payload = {
        "schema_version": "continuity_eval_report_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "aggregate": aggregate,
        "sessions": reports,
    }

    output = Path(str(args.output or "").strip()) if args.output else (
        DEFAULT_REPORTS_ROOT / f"continuity_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"output": str(output), "aggregate": aggregate}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
