from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _natural_key(name: str) -> List[Any]:
    return [int(ch) if ch.isdigit() else ch.lower() for ch in re.split(r"(\d+)", name)]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_label_payload(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        return [dict(x) for x in raw["items"] if isinstance(x, dict)]
    if isinstance(raw, dict) and raw.get("schema") == "ScanItemJSON-v1":
        return [dict(raw)]
    if isinstance(raw, list):
        return [dict(x) for x in raw if isinstance(x, dict)]
    return []


def build_dataset(*, images_dir: Path, labels_dir: Path, out_dir: Path) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    images_out = out_dir / "images"
    labels_out = out_dir / "labels"
    images_out.mkdir(parents=True, exist_ok=True)
    labels_out.mkdir(parents=True, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    missing_labels: List[str] = []

    images = sorted(
        [p for p in images_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
        key=lambda p: _natural_key(p.name),
    )
    for idx, image_path in enumerate(images, start=1):
        stem = image_path.stem
        label_path = labels_dir / f"{stem}.json"
        if not label_path.exists():
            missing_labels.append(image_path.name)
            continue
        payload = _load_json(label_path)
        items = _normalize_label_payload(payload)
        img_dst = images_out / image_path.name
        lbl_dst = labels_out / f"{stem}.json"
        shutil.copy2(image_path, img_dst)
        lbl_dst.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        rows.append(
            {
                "id": f"{idx:05d}_{stem}",
                "image": str(img_dst.relative_to(out_dir)).replace("\\", "/"),
                "label": str(lbl_dst.relative_to(out_dir)).replace("\\", "/"),
                "items": len(items),
            }
        )

    jsonl_path = out_dir / "dataset.jsonl"
    jsonl_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    manifest = {
        "images_total": len(images),
        "samples_total": len(rows),
        "missing_labels": missing_labels,
        "dataset_jsonl": str(jsonl_path.name),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Construye dataset de escaneo (imagenes + labels JSON).")
    parser.add_argument("--images", required=True, help="Carpeta con imagenes.")
    parser.add_argument("--labels", required=True, help="Carpeta con labels JSON.")
    parser.add_argument("--out", required=True, help="Carpeta de salida.")
    args = parser.parse_args()

    images_dir = Path(args.images).expanduser().resolve()
    labels_dir = Path(args.labels).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()

    if not images_dir.exists():
        print(f"[build_scan_dataset] ERROR: images not found: {images_dir}")
        return 1
    if not labels_dir.exists():
        print(f"[build_scan_dataset] ERROR: labels not found: {labels_dir}")
        return 1

    manifest = build_dataset(images_dir=images_dir, labels_dir=labels_dir, out_dir=out_dir)
    print(
        "[build_scan_dataset] samples={samples} missing_labels={missing}".format(
            samples=manifest["samples_total"],
            missing=len(manifest["missing_labels"]),
        )
    )
    print(f"[build_scan_dataset] out={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

