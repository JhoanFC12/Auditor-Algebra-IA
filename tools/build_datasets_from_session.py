#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _canon_path(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve()).lower()
    except Exception:
        return str(value or "").strip().lower()


def _safe_name(value: str, fallback: str) -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in text)
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def _to_box4(raw: Any) -> Optional[Tuple[int, int, int, int]]:
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        x1, y1, x2, y2 = [int(float(v)) for v in raw[:4]]
    except Exception:
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


@dataclass
class SourceEntry:
    label: str
    path: Path
    key: str


def _load_session(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_sources(payload: Dict[str, Any]) -> List[SourceEntry]:
    out: List[SourceEntry] = []
    seen: set[str] = set()

    files = payload.get("files", [])
    if isinstance(files, list):
        for idx, item in enumerate(files, start=1):
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "") or f"source_{idx}")
            raw_path = str(item.get("path", "") or "").strip()
            if not raw_path:
                continue
            p = Path(raw_path).expanduser()
            if not p.exists():
                continue
            key = _canon_path(str(p))
            if key in seen:
                continue
            seen.add(key)
            out.append(SourceEntry(label=label, path=p.resolve(), key=key))

    if out:
        return out

    # Fallback for old sessions without "files".
    overrides = payload.get("segmentation", {}).get("overrides", {})
    if isinstance(overrides, dict):
        for idx, raw_path in enumerate(overrides.keys(), start=1):
            p = Path(str(raw_path)).expanduser()
            if not p.exists():
                continue
            key = _canon_path(str(p))
            if key in seen:
                continue
            seen.add(key)
            out.append(SourceEntry(label=p.name or f"source_{idx}", path=p.resolve(), key=key))
    return out


def _collect_segmentation_boxes(payload: Dict[str, Any]) -> Dict[str, List[Tuple[int, int, int, int]]]:
    out: Dict[str, List[Tuple[int, int, int, int]]] = {}
    overrides = payload.get("segmentation", {}).get("overrides", {})
    if not isinstance(overrides, dict):
        return out
    for raw_path, raw_boxes in overrides.items():
        key = _canon_path(str(raw_path))
        boxes: List[Tuple[int, int, int, int]] = []
        if isinstance(raw_boxes, list):
            for raw_box in raw_boxes:
                box = _to_box4(raw_box)
                if box is not None:
                    boxes.append(box)
        if boxes:
            out[key] = boxes
    return out


def _collect_figure_boxes(payload: Dict[str, Any]) -> Dict[str, List[Tuple[int, int, int, int]]]:
    out: Dict[str, List[Tuple[int, int, int, int]]] = {}

    fig_map = payload.get("figure_boxes_by_source", {})
    if isinstance(fig_map, dict):
        for raw_key, entries in fig_map.items():
            key = _canon_path(str(raw_key))
            boxes: List[Tuple[int, int, int, int]] = []
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    if str(entry.get("source", "")).strip().lower() != "manual":
                        continue
                    if not bool(entry.get("confirmed", True)):
                        continue
                    box = _to_box4(entry.get("bbox_px"))
                    if box is not None:
                        boxes.append(box)
            if boxes:
                out[key] = boxes

    # Legacy fallback: one manual box per source.
    legacy_map = payload.get("ocr_exclusion_boxes", {})
    if isinstance(legacy_map, dict):
        for raw_key, raw_box in legacy_map.items():
            key = _canon_path(str(raw_key))
            if key in out:
                continue
            box = _to_box4(raw_box)
            if box is None:
                continue
            out[key] = [box]

    return out


def _write_yolo_dataset(
    *,
    out_dir: Path,
    sources: List[SourceEntry],
    figure_boxes_by_key: Dict[str, List[Tuple[int, int, int, int]]],
    seed: int,
) -> Dict[str, int]:
    from PIL import Image

    yolo_dir = out_dir / "yolo_bbox"
    for split in ("train", "val", "test"):
        (yolo_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (yolo_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    shuffled = list(sources)
    random.Random(seed).shuffle(shuffled)
    total = max(1, len(shuffled))

    stats = {"images": 0, "labels": 0, "boxes": 0, "positive_images": 0}
    for idx, src in enumerate(shuffled, start=1):
        ratio = float(idx) / float(total)
        split = "train" if ratio <= 0.8 else ("val" if ratio <= 0.9 else "test")
        sample_id = f"{idx:04d}_{_safe_name(src.path.stem, f'source_{idx:04d}')}"
        ext = src.path.suffix or ".png"
        dst_img = yolo_dir / "images" / split / f"{sample_id}{ext}"
        dst_lbl = yolo_dir / "labels" / split / f"{sample_id}.txt"

        shutil.copy2(src.path, dst_img)
        stats["images"] += 1

        lines: List[str] = []
        try:
            with Image.open(src.path) as im:
                width, height = im.size
        except Exception:
            width = 0
            height = 0
        if width > 0 and height > 0:
            for box in figure_boxes_by_key.get(src.key, []):
                x1, y1, x2, y2 = box
                x1 = max(0, min(width - 1, int(x1)))
                x2 = max(0, min(width, int(x2)))
                y1 = max(0, min(height - 1, int(y1)))
                y2 = max(0, min(height, int(y2)))
                if x2 <= x1 or y2 <= y1:
                    continue
                bw = float(x2 - x1) / float(width)
                bh = float(y2 - y1) / float(height)
                cx = (float(x1) + float(x2)) / 2.0 / float(width)
                cy = (float(y1) + float(y2)) / 2.0 / float(height)
                lines.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

        dst_lbl.write_text("\n".join(lines), encoding="utf-8")
        stats["labels"] += 1
        stats["boxes"] += len(lines)
        if lines:
            stats["positive_images"] += 1

    (yolo_dir / "classes.txt").write_text("figura_problema\n", encoding="utf-8")
    (yolo_dir / "dataset.yaml").write_text(
        "\n".join(
            [
                f"path: {str(yolo_dir).replace(chr(92), '/')}",
                "train: images/train",
                "val: images/val",
                "test: images/test",
                "names:",
                "  0: figura_problema",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return stats


def _build_vlm_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    prompt = (
        "Transcribe el problema en formato scan final. "
        "Devuelve una sola linea con estructura \\\\item y separadores \u00A3/\u00E6."
    )

    pairs = payload.get("training_pairs_by_item", {})
    if isinstance(pairs, dict):
        for raw_key, raw_pair in pairs.items():
            if not isinstance(raw_pair, dict):
                continue
            completion = str(raw_pair.get("human_final_output", "") or "").strip()
            if not completion:
                continue
            metadata = dict(raw_pair.get("metadata", {}) or {})
            image_path = ""
            raw_paths = metadata.get("human_image_paths", [])
            if isinstance(raw_paths, list):
                for item in raw_paths:
                    candidate = str(item or "").strip()
                    if candidate:
                        image_path = candidate
                        break
            if not image_path:
                image_path = str(metadata.get("source_path", "") or "").strip()
            rows.append(
                {
                    "id": str(raw_key),
                    "input_image_path": image_path,
                    "prompt": prompt,
                    "completion": completion,
                    "metadata": metadata,
                }
            )
    return rows


def _build_annotation_queue(
    *,
    out_dir: Path,
    sources: List[SourceEntry],
    seg_boxes_by_key: Dict[str, List[Tuple[int, int, int, int]]],
) -> Dict[str, int]:
    from PIL import Image

    ann_dir = out_dir / "vlm_annotation_queue"
    crops_dir = ann_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    crop_count = 0
    for src in sources:
        boxes = seg_boxes_by_key.get(src.key, [])
        if not boxes:
            continue
        try:
            with Image.open(src.path) as im:
                width, height = im.size
                for bidx, (x1, y1, x2, y2) in enumerate(boxes, start=1):
                    x1c = max(0, min(width - 1, int(x1)))
                    x2c = max(0, min(width, int(x2)))
                    y1c = max(0, min(height - 1, int(y1)))
                    y2c = max(0, min(height, int(y2)))
                    if x2c <= x1c or y2c <= y1c:
                        continue
                    crop = im.crop((x1c, y1c, x2c, y2c))
                    crop_name = f"{_safe_name(src.path.stem, 'source')}_seg_{bidx:02d}.png"
                    crop_path = crops_dir / crop_name
                    crop.save(crop_path, format="PNG")
                    row_id = hashlib.sha1(f"{src.key}|{bidx}".encode("utf-8")).hexdigest()[:16]
                    rows.append(
                        {
                            "id": row_id,
                            "input_image_path": str(crop_path).replace("\\", "/"),
                            "prompt": (
                                "Transcribe el problema en formato scan final. "
                                "Devuelve una sola linea con estructura \\item y separadores \u00A3/\u00E6."
                            ),
                            "completion": "",
                            "metadata": {
                                "source_label": src.label,
                                "source_path": str(src.path),
                                "segment_index": int(bidx),
                                "bbox_px": [int(x1c), int(y1c), int(x2c), int(y2c)],
                                "needs_manual_completion": True,
                            },
                        }
                    )
                    crop_count += 1
        except Exception:
            continue

    (ann_dir / "pairs_template.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    return {"template_rows": len(rows), "template_crops": crop_count}


def build_dataset(*, session_path: Path, out_root: Path, seed: int) -> Path:
    payload = _load_session(session_path)
    sources = _collect_sources(payload)
    seg_boxes = _collect_segmentation_boxes(payload)
    fig_boxes = _collect_figure_boxes(payload)
    vlm_rows = _build_vlm_rows(payload)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = out_root / f"dataset_train_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    yolo_stats = _write_yolo_dataset(
        out_dir=out_dir,
        sources=sources,
        figure_boxes_by_key=fig_boxes,
        seed=seed,
    )

    vlm_path = out_dir / "pairs_texto.jsonl"
    vlm_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in vlm_rows) + ("\n" if vlm_rows else ""),
        encoding="utf-8",
    )

    queue_stats = _build_annotation_queue(out_dir=out_dir, sources=sources, seg_boxes_by_key=seg_boxes)

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "session_path": str(session_path),
        "sources_total": len(sources),
        "segmentation_sources": len(seg_boxes),
        "figure_sources": len(fig_boxes),
        "yolo_stats": yolo_stats,
        "vlm_rows": len(vlm_rows),
        "annotation_queue": queue_stats,
        "notes": [
            "YOLO usa solo cajas manuales confirmadas cuando existen.",
            "pairs_texto.jsonl incluye solo filas con human_final_output.",
            "Si vlm_rows=0, usa vlm_annotation_queue/pairs_template.jsonl para completar manualmente.",
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "session_snapshot.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_dir


def _resolve_latest_session(sessions_dir: Path) -> Optional[Path]:
    candidates = sorted(sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Build YOLO/VLM datasets from a transcriptor session JSON.")
    parser.add_argument("--session", type=str, default="", help="Path to session JSON. Default: latest session.")
    parser.add_argument(
        "--out-root",
        type=str,
        default=".cache/transcriptor_runs/datasets",
        help="Output root directory.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Split random seed.")
    args = parser.parse_args()

    sessions_dir = Path(".cache/transcriptor_runs/sessions")
    session_path = Path(args.session).expanduser() if args.session else _resolve_latest_session(sessions_dir)
    if not session_path or not session_path.exists():
        print("[ERROR] No session file found. Use --session <path>.")
        return 1

    out_root = Path(args.out_root).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)

    out_dir = build_dataset(session_path=session_path.resolve(), out_root=out_root.resolve(), seed=int(args.seed))
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(f"[OK] Dataset creado: {out_dir}")
    print(
        "[RESUMEN] fuentes={sources_total} yolo_boxes={boxes} yolo_pos={pos} "
        "vlm_rows={vlm} queue_rows={queue}".format(
            sources_total=manifest.get("sources_total", 0),
            boxes=manifest.get("yolo_stats", {}).get("boxes", 0),
            pos=manifest.get("yolo_stats", {}).get("positive_images", 0),
            vlm=manifest.get("vlm_rows", 0),
            queue=manifest.get("annotation_queue", {}).get("template_rows", 0),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
