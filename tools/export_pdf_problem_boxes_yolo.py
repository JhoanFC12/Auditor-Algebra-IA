from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image


def split_for(record_id: str) -> str:
    bucket = int(hashlib.sha1(record_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "val"
    return "test"


def main() -> int:
    parser = argparse.ArgumentParser(description="Exporta Golden PDF manual a YOLO para detectar problemas completos.")
    parser.add_argument("--golden-instance", required=True)
    parser.add_argument("--out-root", default=".cache/transcriptor_runs/datasets")
    args = parser.parse_args()
    instance = Path(args.golden_instance).expanduser().resolve()
    if not instance.exists():
        raise FileNotFoundError(instance)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out_root).expanduser().resolve() / f"pdf_problem_boxes_yolo_{stamp}"
    counts = {"train": 0, "val": 0, "test": 0}
    boxes_total = 0
    for record_path in sorted((instance / "records").glob("*.json")):
        row = json.loads(record_path.read_text(encoding="utf-8"))
        source = instance / str(row["image_rel"])
        split = split_for(str(row["record_id"]))
        image_dst = out / "images" / split / f"{row['record_id']}.png"
        label_dst = out / "labels" / split / f"{row['record_id']}.txt"
        image_dst.parent.mkdir(parents=True, exist_ok=True)
        label_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, image_dst)
        with Image.open(source) as image:
            width, height = image.size
        labels: list[str] = []
        for raw in row.get("boxes_px", []):
            x1, y1, x2, y2 = [int(value) for value in raw[:4]]
            x1, x2 = sorted((max(0, x1), min(width, x2)))
            y1, y2 = sorted((max(0, y1), min(height, y2)))
            if x2 <= x1 or y2 <= y1:
                continue
            labels.append(f"0 {((x1 + x2) / 2) / width:.8f} {((y1 + y2) / 2) / height:.8f} {(x2 - x1) / width:.8f} {(y2 - y1) / height:.8f}")
        label_dst.write_text("\n".join(labels) + ("\n" if labels else ""), encoding="utf-8")
        counts[split] += 1
        boxes_total += len(labels)
    (out / "dataset.yaml").write_text(
        "path: .\ntrain: images/train\nval: images/val\ntest: images/test\nnames:\n  0: problema_matematico_completo\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "pdf_problem_boxes_yolo_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(instance),
        "counts": counts,
        "pages_total": sum(counts.values()),
        "boxes_total": boxes_total,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"DATASET_OUT={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
