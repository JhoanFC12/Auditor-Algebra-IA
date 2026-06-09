from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Fusiona datasets OCR visuales preservando splits e imagenes.")
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()

    sources = [Path(value).expanduser().resolve() for value in args.source]
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows_by_split: dict[str, list[dict[str, object]]] = {"train": [], "validation": [], "test": []}
    seen: set[str] = set()

    for source_index, source in enumerate(sources, start=1):
        for split in rows_by_split:
            rows_path = source / f"{split}.jsonl"
            if not rows_path.exists():
                continue
            for line in rows_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                record_id = str(row.get("id") or "").strip()
                if not record_id or record_id in seen:
                    continue
                seen.add(record_id)
                image_src = (source / str(row["image"])).resolve()
                image_dir = out_dir / split / "images"
                image_dir.mkdir(parents=True, exist_ok=True)
                image_dst = image_dir / f"{source_index}_{image_src.name}"
                shutil.copy2(image_src, image_dst)
                row["image"] = str(image_dst.relative_to(out_dir)).replace("\\", "/")
                rows_by_split[split].append(row)

    for split, rows in rows_by_split.items():
        (out_dir / f"{split}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
    manifest = {
        "schema": "math_ocr_reasoning_dataset_v2",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sources": [str(source) for source in sources],
        "counts": {split: len(rows) for split, rows in rows_by_split.items()},
        "total": sum(len(rows) for rows in rows_by_split.values()),
        "task": "OCR matematico visual general acumulativo.",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
