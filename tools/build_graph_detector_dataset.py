from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_name(value: str, fallback: str = "sample") -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in text)
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def _resolve_source_path(manifest_path: Path, raw_source: Any) -> Path:
    raw = str(raw_source or "").strip()
    if not raw:
        return Path("")
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return (manifest_path.parent / raw).resolve()


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


def _hash_to_split(key: str) -> str:
    raw = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16) % 100
    if raw < 80:
        return "train"
    if raw < 90:
        return "val"
    return "test"


@dataclass
class SourceSample:
    source_path: Path
    source_stem: str
    boxes: List[Tuple[int, int, int, int]]
    manifest_path: Path
    root: Path


def _iter_manifest_samples(roots: Iterable[Path]) -> Iterable[SourceSample]:
    for root in roots:
        if not root.exists():
            continue
        for manifest_path in sorted(root.rglob("segments_manifest.json")):
            try:
                payload = _load_json(manifest_path)
            except Exception:
                continue
            source_path = _resolve_source_path(manifest_path, payload.get("source_path"))
            if not source_path or not source_path.exists():
                continue
            if source_path.suffix.lower() not in IMAGE_EXTS:
                continue
            boxes: List[Tuple[int, int, int, int]] = []
            for segment in payload.get("segments") or []:
                if not isinstance(segment, dict):
                    continue
                box = _to_box4(segment.get("bbox_px"))
                if box is not None:
                    boxes.append(box)
            if not boxes:
                continue
            yield SourceSample(
                source_path=source_path.resolve(),
                source_stem=str(payload.get("source_stem") or source_path.stem),
                boxes=boxes,
                manifest_path=manifest_path.resolve(),
                root=root.resolve(),
            )


def _write_yolo_label(
    *,
    label_path: Path,
    boxes: List[Tuple[int, int, int, int]],
    width: int,
    height: int,
) -> int:
    lines: List[str] = []
    valid = 0
    for x1, y1, x2, y2 in boxes:
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
        valid += 1
    label_path.write_text("\n".join(lines), encoding="utf-8")
    return valid


def build_dataset(*, roots: List[Path], out_root: Path) -> Path:
    from PIL import Image

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = out_root / f"graph_detector_{ts}"
    images_root = out_dir / "images"
    labels_root = out_dir / "labels"
    for split in ("train", "val", "test"):
        (images_root / split).mkdir(parents=True, exist_ok=True)
        (labels_root / split).mkdir(parents=True, exist_ok=True)

    stats = Counter()
    manifest_rows: List[Dict[str, Any]] = []
    for sample in _iter_manifest_samples(roots):
        split = _hash_to_split(str(sample.source_path))
        src = sample.source_path
        ext = src.suffix.lower() or ".png"
        file_name = f"{_safe_name(sample.source_stem, 'source')}{ext}"
        dst_image = images_root / split / file_name
        dst_label = labels_root / split / f"{Path(file_name).stem}.txt"

        if not dst_image.exists():
            shutil.copy2(src, dst_image)

        try:
            with Image.open(src) as im:
                width, height = im.size
        except Exception:
            stats["image_open_errors"] += 1
            continue
        if width <= 0 or height <= 0:
            stats["invalid_dimensions"] += 1
            continue

        boxes_written = _write_yolo_label(label_path=dst_label, boxes=sample.boxes, width=width, height=height)
        stats["images_total"] += 1
        stats[f"images_{split}"] += 1
        stats["boxes_total"] += boxes_written
        stats[f"boxes_{split}"] += boxes_written

        manifest_rows.append(
            {
                "image": str(dst_image.relative_to(out_dir)).replace("\\", "/"),
                "label": str(dst_label.relative_to(out_dir)).replace("\\", "/"),
                "split": split,
                "width": width,
                "height": height,
                "boxes_total": boxes_written,
                "source_path": str(sample.source_path),
                "source_stem": sample.source_stem,
                "manifest_path": str(sample.manifest_path),
                "root": str(sample.root),
            }
        )

    (out_dir / "classes.txt").write_text("grafico_problema\n", encoding="utf-8")
    (out_dir / "dataset.yaml").write_text(
        "\n".join(
            [
                "path: .",
                "train: images/train",
                "val: images/val",
                "test: images/test",
                "names:",
                "  0: grafico_problema",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (out_dir / "samples.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in manifest_rows) + ("\n" if manifest_rows else ""),
        encoding="utf-8",
    )
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "roots": [str(root) for root in roots],
        "class_name": "grafico_problema",
        "images_total": stats["images_total"],
        "boxes_total": stats["boxes_total"],
        "splits": {
            "train": {"images": stats["images_train"], "boxes": stats["boxes_train"]},
            "val": {"images": stats["images_val"], "boxes": stats["boxes_val"]},
            "test": {"images": stats["images_test"], "boxes": stats["boxes_test"]},
        },
        "files": {
            "dataset_yaml": "dataset.yaml",
            "classes_txt": "classes.txt",
            "samples_jsonl": "samples.jsonl",
        },
        "notes": [
            "Cada imagen corresponde a una imagen fuente completa del escaneo.",
            "Cada label contiene una o más cajas del gráfico/segmento detectado.",
            "Este dataset está pensado solo para detección de gráficos, no para topología.",
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "README.txt").write_text(
        "\n".join(
            [
                "DATASET DETECTOR DE GRAFICOS",
                "",
                "Formato:",
                "- images/train|val|test",
                "- labels/train|val|test",
                "- dataset.yaml",
                "",
                "Clase unica:",
                "- grafico_problema",
                "",
                "Uso sugerido con Ultralytics:",
                "yolo detect train data=dataset.yaml model=yolov8n.pt imgsz=1024 epochs=100 batch=8",
            ]
        ),
        encoding="utf-8",
    )
    return out_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Construye un dataset YOLO para detectar gráficos en imágenes fuente.")
    parser.add_argument("--roots", nargs="+", required=True, help="Una o más rutas raíz con segments_manifest.json.")
    parser.add_argument(
        "--out-root",
        default="E:/Github/Auditor-IA/.cache/transcriptor_runs/datasets",
        help="Carpeta raíz de salida.",
    )
    args = parser.parse_args()

    roots = [Path(p).expanduser().resolve() for p in args.roots]
    out_root = Path(args.out_root).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    out_dir = build_dataset(roots=roots, out_root=out_root)
    manifest = _load_json(out_dir / "manifest.json")
    print(f"[OK] Dataset detector creado: {out_dir}")
    print(
        "[RESUMEN] images={images} boxes={boxes} train={train} val={val} test={test}".format(
            images=manifest.get("images_total", 0),
            boxes=manifest.get("boxes_total", 0),
            train=manifest.get("splits", {}).get("train", {}).get("images", 0),
            val=manifest.get("splits", {}).get("val", {}).get("images", 0),
            test=manifest.get("splits", {}).get("test", {}).get("images", 0),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
