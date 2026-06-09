from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path


SYSTEM_PROMPT = (
    "Eres un normalizador fiel de OCR matematico. Corrige errores tipograficos y expresa la notacion "
    "matematica en LaTeX sin resolver, resumir, completar ni inventar contenido. Conserva el orden, "
    "los marcadores <n.>, las alternativas y [CONT.] cuando aparezcan."
)


def _split_for(record_id: str) -> str:
    value = int(hashlib.sha1(record_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    if value < 80:
        return "train"
    if value < 90:
        return "validation"
    return "test"


def export_confirmed_records(golden_dir: Path, out_dir: Path) -> None:
    golden_dir = Path(golden_dir).expanduser().resolve()
    out_dir = Path(out_dir).expanduser().resolve()
    source = golden_dir / "records_confirmed.jsonl"
    if not source.exists():
        raise FileNotFoundError(f"No existe el indice confirmado: {source}")
    out_dir.mkdir(parents=True, exist_ok=True)

    rows_by_split: dict[str, list[dict[str, object]]] = {"train": [], "validation": [], "test": []}
    for line in source.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        record_id = str(row.get("record_id") or "").strip()
        raw_ocr = str(row.get("raw_ocr") or "").strip()
        corrected = str(row.get("normalized_text") or "").strip()
        if not record_id or not raw_ocr or not corrected:
            continue
        split = _split_for(record_id)
        rows_by_split[split].append(
            {
                "id": record_id,
                "raw_ocr": raw_ocr,
                "normalized_text": corrected,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": raw_ocr},
                    {"role": "assistant", "content": corrected},
                ],
                "source_label": str(row.get("source_label") or ""),
                "book_code": str(row.get("book_code") or ""),
                "instance_type": str(row.get("instance_type") or ""),
            }
        )

    total = sum(len(rows) for rows in rows_by_split.values())
    if total == 0:
        raise ValueError("No hay pares confirmados para exportar. Revisa y confirma ejemplos en la Golden de normalizacion.")
    for split, rows in rows_by_split.items():
        (out_dir / f"{split}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
    manifest = {
        "schema": "math_ocr_normalization_dataset_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(source),
        "counts": {split: len(rows) for split, rows in rows_by_split.items()},
        "total": total,
        "task": "OCR crudo -> OCR matematico normalizado fiel",
        "system_prompt": SYSTEM_PROMPT,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepara pares OCR crudo -> OCR matematico normalizado.")
    parser.add_argument("--golden-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    export_confirmed_records(Path(args.golden_dir), Path(args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
