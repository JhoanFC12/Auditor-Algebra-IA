from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
MANIFEST_SCHEMA_VERSION = "graph_detector_feedback_dataset_v1"
DATASET_KIND = "graph_detector_feedback"
DEFAULT_TARGET_CORRECTED_SAMPLES = 200


def _safe_name(text: str) -> str:
    keep = []
    for ch in str(text or "").strip():
        if ch.isalnum() or ch in {"-", "_", "."}:
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep).strip("_") or "sample"


def _normalize_box(entry: Dict[str, Any]) -> Tuple[int, int, int, int] | None:
    bbox_raw = entry.get("bbox_px")
    if not isinstance(bbox_raw, (list, tuple)) or len(bbox_raw) < 4:
        return None
    try:
        x1, y1, x2, y2 = [int(v) for v in bbox_raw[:4]]
    except Exception:
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def _to_yolo(box: Tuple[int, int, int, int], width: int, height: int) -> str:
    x1, y1, x2, y2 = box
    xc = ((x1 + x2) / 2.0) / float(width)
    yc = ((y1 + y2) / 2.0) / float(height)
    bw = (x2 - x1) / float(width)
    bh = (y2 - y1) / float(height)
    return f"0 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}"


def _box_from_list(raw: Any) -> Tuple[int, int, int, int] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        x1, y1, x2, y2 = [int(float(v)) for v in raw[:4]]
    except Exception:
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def _hash_suffix(value: str) -> str:
    return hashlib.sha1(str(value or "").encode("utf-8", errors="ignore")).hexdigest()[:10]


def _resolve_live_image(record_path: Path, payload: Dict[str, Any], segments_root: Path) -> Path:
    source_rel = str(payload.get("source_image_rel", "") or "").strip()
    if source_rel:
        candidate = (segments_root / source_rel).resolve()
        if candidate.exists():
            return candidate
    raw_source = str(payload.get("source_path", "") or "").strip()
    if raw_source:
        candidate = Path(raw_source).expanduser()
        if candidate.exists():
            return candidate
    return record_path


def _boxes_from_live_record(payload: Dict[str, Any]) -> List[Tuple[int, int, int, int]]:
    boxes: List[Tuple[int, int, int, int]] = []
    boxes_px = payload.get("boxes_px")
    if isinstance(boxes_px, list):
        for raw in boxes_px:
            box = _box_from_list(raw)
            if box:
                boxes.append(box)
    if boxes:
        return boxes
    segments = payload.get("segments")
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            box = _box_from_list(segment.get("bbox_px"))
            if box:
                boxes.append(box)
    if boxes:
        return boxes
    detector = payload.get("detector_review")
    final_boxes = detector.get("final_boxes") if isinstance(detector, dict) else []
    if isinstance(final_boxes, list):
        for entry in final_boxes:
            if not isinstance(entry, dict):
                continue
            box = _box_from_list(entry.get("bbox_px"))
            if box:
                boxes.append(box)
    return boxes


def _iter_live_golden_records(segments_root: Path) -> List[Dict[str, Any]]:
    records_dir = segments_root / "records"
    if not records_dir.exists():
        return []
    records: List[Dict[str, Any]] = []
    for record_path in sorted(records_dir.glob("*.json")):
        try:
            payload = json.loads(record_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        source_path = _resolve_live_image(record_path, payload, segments_root)
        if not source_path.exists() or source_path.suffix.lower() not in IMAGE_EXTS:
            continue
        detector = payload.get("detector_review") if isinstance(payload.get("detector_review"), dict) else {}
        final_boxes = _boxes_from_live_record(payload)
        label = str(detector.get("diagram_presence_label", "") or "").strip().lower()
        if label not in {"yes", "no"}:
            label = "yes" if final_boxes else "no"
        records.append(
            {
                "source_kind": "segment_training_live",
                "sample_id": _safe_name(str(payload.get("record_id") or source_path.stem)),
                "source_path": source_path,
                "manifest_path": record_path,
                "review_status": str(detector.get("review_status") or "live").strip().lower() or "live",
                "diagram_presence_label": label,
                "detector_source": str(detector.get("detector_source") or ""),
                "detector_model": str(detector.get("detector_model") or ""),
                "predicted_boxes": detector.get("predicted_boxes", []),
                "final_boxes": [{"bbox_px": list(box), "source": "segment_training_live"} for box in final_boxes],
                "boxes": final_boxes,
            }
        )
    return records


def _iter_manifest_records(segments_root: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    manifests = sorted(segments_root.rglob("segments_manifest.json"))
    for manifest_path in manifests:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
        if not isinstance(payload, dict):
            continue
        audit = payload.get("detector_review", {})
        if not isinstance(audit, dict):
            continue
        review_status = str(audit.get("review_status", "") or "").strip().lower()
        if not review_status:
            continue
        diagram_presence_label = str(audit.get("diagram_presence_label", "") or "").strip().lower()
        if diagram_presence_label not in {"yes", "no"}:
            final_boxes_probe = audit.get("final_boxes", [])
            if isinstance(final_boxes_probe, list) and final_boxes_probe:
                diagram_presence_label = "yes"
            else:
                diagram_presence_label = "no"
        final_boxes_raw = audit.get("final_boxes", [])
        source_path = Path(str(payload.get("source_path", "") or "").strip())
        if not source_path.exists() or source_path.suffix.lower() not in IMAGE_EXTS:
            continue
        final_boxes = [
            box
            for box in (_normalize_box(entry) for entry in final_boxes_raw if isinstance(entry, dict))
            if box
        ]
        if diagram_presence_label == "yes" and not final_boxes:
            continue
        records.append(
            {
                "source_kind": "segments_manifest",
                "sample_id": _safe_name(f"{source_path.stem}_{_hash_suffix(str(manifest_path))}"),
                "source_path": source_path,
                "manifest_path": manifest_path,
                "review_status": review_status,
                "diagram_presence_label": diagram_presence_label,
                "detector_source": str(audit.get("detector_source", "") or ""),
                "detector_model": str(audit.get("detector_model", "") or ""),
                "predicted_boxes": audit.get("predicted_boxes", []),
                "final_boxes": audit.get("final_boxes", []),
                "boxes": final_boxes,
            }
        )
    return records


def build_feedback_dataset(
    *,
    segments_root: Path,
    out_dir: Path,
    corrected_only: bool = True,
    target_corrected_samples: int = DEFAULT_TARGET_CORRECTED_SAMPLES,
) -> Dict[str, Any]:
    images_dir = out_dir / "images"
    labels_dir = out_dir / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    exported = 0
    corrected = 0
    positives = 0
    negatives = 0
    records: List[Dict[str, Any]] = []

    source_records = _iter_manifest_records(segments_root) + _iter_live_golden_records(segments_root)
    seen_sample_ids: set[str] = set()
    for source_record in source_records:
        review_status = str(source_record.get("review_status") or "").strip().lower()
        if corrected_only and review_status != "corrected":
            continue
        source_path = Path(source_record["source_path"])
        try:
            from PIL import Image  # type: ignore
            with Image.open(source_path) as im:
                width, height = int(im.size[0]), int(im.size[1])
        except Exception:
            continue
        if width <= 1 or height <= 1:
            continue
        final_boxes = list(source_record.get("boxes") or [])
        diagram_presence_label = str(source_record.get("diagram_presence_label") or "").strip().lower()
        if diagram_presence_label == "yes" and not final_boxes:
            continue

        sample_id = _safe_name(str(source_record.get("sample_id") or source_path.stem))
        if sample_id in seen_sample_ids:
            sample_id = _safe_name(f"{sample_id}_{_hash_suffix(str(source_record.get('manifest_path')))}")
        seen_sample_ids.add(sample_id)
        dst_image = images_dir / f"{sample_id}{source_path.suffix.lower()}"
        dst_label = labels_dir / f"{sample_id}.txt"
        try:
            shutil.copy2(source_path, dst_image)
        except Exception:
            continue
        if final_boxes:
            dst_label.write_text("\n".join(_to_yolo(box, width, height) for box in final_boxes) + "\n", encoding="utf-8")
            positives += 1
        else:
            dst_label.write_text("", encoding="utf-8")
            negatives += 1
        exported += 1
        if review_status == "corrected":
            corrected += 1
        records.append(
            {
                "sample_id": sample_id,
                "source_kind": str(source_record.get("source_kind") or ""),
                "source_path": str(source_path),
                "manifest_path": str(source_record.get("manifest_path") or ""),
                "review_status": review_status,
                "diagram_presence_label": diagram_presence_label,
                "detector_source": str(source_record.get("detector_source") or ""),
                "detector_model": str(source_record.get("detector_model") or ""),
                "predicted_boxes": source_record.get("predicted_boxes", []),
                "final_boxes": source_record.get("final_boxes", []),
            }
        )

    dataset_yaml = out_dir / "dataset.yaml"
    dataset_yaml.write_text(
        "\n".join(
            [
                "path: .",
                "train: images",
                "val: images",
                "test: images",
                "names:",
                "  0: grafico_problema",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (out_dir / "classes.txt").write_text("grafico_problema\n", encoding="utf-8")
    manifest_out = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset_kind": DATASET_KIND,
        "segments_root": str(segments_root),
        "samples": exported,
        "positive_samples": positives,
        "negative_samples": negatives,
        "corrected_samples": corrected,
        "corrected_only": corrected_only,
        "target_corrected_samples": target_corrected_samples,
        "remaining_to_target": max(0, int(target_corrected_samples) - corrected),
        "records_file": "records.jsonl",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest_out, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "records.jsonl").open("w", encoding="utf-8") as fh:
        for row in records:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return manifest_out


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporta correcciones humanas del detector de graficos a un dataset YOLO.")
    parser.add_argument("--segments-root", required=True, help="Carpeta raiz con manifests de segmentos.")
    parser.add_argument("--out-dir", required=True, help="Carpeta destino del dataset feedback.")
    parser.add_argument(
        "--include-reviewed",
        action="store_true",
        help="Incluye revisiones sin cambios. Por defecto solo exporta review_status=corrected.",
    )
    parser.add_argument(
        "--target-corrected-samples",
        type=int,
        default=DEFAULT_TARGET_CORRECTED_SAMPLES,
        help="Meta de imagenes corregidas para el primer lote de entrenamiento.",
    )
    args = parser.parse_args()

    result = build_feedback_dataset(
        segments_root=Path(args.segments_root).expanduser().resolve(),
        out_dir=Path(args.out_dir).expanduser().resolve(),
        corrected_only=not args.include_reviewed,
        target_corrected_samples=max(1, int(args.target_corrected_samples)),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
