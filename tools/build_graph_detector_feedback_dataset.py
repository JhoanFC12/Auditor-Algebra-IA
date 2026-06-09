from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


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


def build_feedback_dataset(*, segments_root: Path, out_dir: Path) -> Dict[str, Any]:
    manifests = sorted(segments_root.rglob("segments_manifest.json"))
    images_dir = out_dir / "images"
    labels_dir = out_dir / "labels"
    images_dir.mkdir(parents=True, exist_ok=True)
    labels_dir.mkdir(parents=True, exist_ok=True)

    exported = 0
    corrected = 0
    positives = 0
    negatives = 0
    records: List[Dict[str, Any]] = []

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
        try:
            from PIL import Image  # type: ignore
            with Image.open(source_path) as im:
                width, height = int(im.size[0]), int(im.size[1])
        except Exception:
            continue
        if width <= 1 or height <= 1:
            continue
        final_boxes = [box for box in (_normalize_box(entry) for entry in final_boxes_raw if isinstance(entry, dict)) if box]
        if diagram_presence_label == "yes" and not final_boxes:
            continue

        sample_id = _safe_name(source_path.stem)
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
                "source_path": str(source_path),
                "manifest_path": str(manifest_path),
                "review_status": review_status,
                "diagram_presence_label": diagram_presence_label,
                "detector_source": str(audit.get("detector_source", "") or ""),
                "detector_model": str(audit.get("detector_model", "") or ""),
                "predicted_boxes": audit.get("predicted_boxes", []),
                "final_boxes": audit.get("final_boxes", []),
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
        "schema_version": 1,
        "dataset_kind": "graph_detector_feedback",
        "segments_root": str(segments_root),
        "samples": exported,
        "positive_samples": positives,
        "negative_samples": negatives,
        "corrected_samples": corrected,
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
    args = parser.parse_args()

    result = build_feedback_dataset(
        segments_root=Path(args.segments_root).expanduser().resolve(),
        out_dir=Path(args.out_dir).expanduser().resolve(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
