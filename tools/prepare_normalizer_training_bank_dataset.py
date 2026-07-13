from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BANK_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "datasets" / "normalizer_training_bank"

SYSTEM_PROMPT = (
    "Eres un normalizador fiel de OCR matematico para Auditor-IA. "
    "Recibes un JSON de staging con raw_ocr, metadata y segmentacion grafica. "
    "Devuelve solamente un item LaTeX final, sin JSON, sin explicaciones y sin inventar contenido. "
    "Usa el formato: \\item[\\textbf{n.}] [[curso=...]] [[tema=...]] "
    "[[Estado=sin_revisar]] [[Clave=...]] enunciado [[Imagen=img-n]] "
    "\u00a3A)...\u00e6B)...\u00e6C)...\u00a3D)...\u00e6\u00e6E)...\u00a3. "
    "Respeta exactamente los separadores de alternativas \u00a3 y \u00e6; no los cambies por listas A) B) C) D) E). "
    "Usa [[Imagen=img-n]] solo cuando la segmentacion indique grafico o el humano lo haya marcado. "
    "No describas graficos. Si el JSON trae continuations o imagen OCR fusionada, integra ese contenido en el problema padre. "
    "No uses [CONT.] como contrato: puede no existir, no debes pedirlo y nunca debe aparecer en la salida final. "
    "Si procesas un lote externo, conserva cada separador ----nombre_imagen.png----- antes de su item LaTeX."
)


def _split_for(record_id: str) -> str:
    value = int(hashlib.sha1(record_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    if value < 82:
        return "train"
    if value < 91:
        return "validation"
    return "test"


def _compact_figure(figure: dict[str, Any]) -> dict[str, Any]:
    segments = figure.get("segments") if isinstance(figure.get("segments"), list) else []
    detector = figure.get("detector") if isinstance(figure.get("detector"), dict) else {}
    total = int(figure.get("segments_total") or len(segments) or 0)
    return {
        "status": str(figure.get("status") or ""),
        "has_figure": total > 0,
        "segments_total": total,
        "detector_source": str(detector.get("detector_source") or ""),
        "review_status": str(detector.get("review_status") or ""),
    }


def _source_payload(row: dict[str, Any]) -> dict[str, Any]:
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    return {
        "book_code": str(source.get("book_code") or ""),
        "instance_type": str(source.get("instance_type") or ""),
        "page_number": source.get("page_number"),
        "problem_number": source.get("problem_number"),
        "box_index": source.get("box_index"),
        "crop_name": Path(str(row.get("crop_path") or "")).name,
    }


def _input_payload(row: dict[str, Any]) -> dict[str, Any]:
    continuations = row.get("continuations") if isinstance(row.get("continuations"), list) else []
    images = row.get("images") if isinstance(row.get("images"), list) else []
    normalized = row.get("normalized_human") if isinstance(row.get("normalized_human"), dict) else {}
    figure = row.get("figure_segmentation") if isinstance(row.get("figure_segmentation"), dict) else {}
    return {
        "schema_version": "normalizer_training_input_v1",
        "record_id": str(row.get("record_id") or ""),
        "raw_ocr": str(row.get("raw_ocr") or ""),
        "source": _source_payload(row),
        "figure_segmentation": _compact_figure(figure),
        "human_hints": {
            "curso": str(normalized.get("curso") or ""),
            "tema": str(normalized.get("tema") or ""),
            "has_figure": bool(normalized.get("tiene_grafico")),
            "figure_tag": str(normalized.get("figure_tag") or ""),
        },
        "continuations": [
            {
                "record_id": str(item.get("record_id") or ""),
                "raw_ocr": str(item.get("raw_ocr") or ""),
                "crop_name": Path(str(item.get("crop_path") or "")).name,
            }
            for item in continuations
            if isinstance(item, dict)
        ],
        "images": [
            {
                "role": str(item.get("role") or ""),
                "crop_id": str(item.get("crop_id") or ""),
                "file_name": Path(str(item.get("bank_path") or item.get("source_path") or "")).name,
            }
            for item in images
            if isinstance(item, dict)
        ],
    }


def _load_rows(bank_root: Path) -> list[dict[str, Any]]:
    source = bank_root / "samples.jsonl"
    if not source.exists():
        raise FileNotFoundError(f"No existe samples.jsonl: {source}")
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        raw = str(row.get("raw_ocr") or "").strip()
        target = str(row.get("final_latex") or "").strip()
        if not raw or not target:
            continue
        if not target.startswith("\\item[\\textbf{"):
            raise ValueError(f"Target invalido en linea {line_no}: {target[:120]}")
        rows.append(row)
    if not rows:
        raise ValueError("No hay muestras validas para exportar.")
    return rows


def export_dataset(bank_root: Path, out_dir: Path) -> dict[str, Any]:
    bank_root = bank_root.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows_by_split: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    sources: dict[str, int] = {}
    continuations = 0
    image_tagged = 0
    for row in _load_rows(bank_root):
        record_id = str(row.get("record_id") or row.get("sample_id") or "")
        split = _split_for(record_id)
        input_json = json.dumps(_input_payload(row), ensure_ascii=False, separators=(",", ":"))
        target = str(row.get("final_latex") or "").strip()
        if "[[Imagen=" in target:
            image_tagged += 1
        if row.get("continuations"):
            continuations += 1
        source = _source_payload(row)
        source_key = f"{source.get('book_code')}::{source.get('instance_type')}"
        sources[source_key] = sources.get(source_key, 0) + 1
        rows_by_split[split].append(
            {
                "id": record_id,
                "prompt": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": input_json}],
                "completion": [{"role": "assistant", "content": target}],
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": input_json},
                    {"role": "assistant", "content": target},
                ],
                "raw_ocr": str(row.get("raw_ocr") or ""),
                "final_latex": target,
                "source": source,
            }
        )

    for split, rows in rows_by_split.items():
        path = out_dir / f"{split}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    manifest = {
        "schema": "math_ocr_final_latex_sft_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "bank_root": str(bank_root),
        "counts": {split: len(rows) for split, rows in rows_by_split.items()},
        "total": sum(len(rows) for rows in rows_by_split.values()),
        "samples_with_image_tag": image_tagged,
        "samples_with_continuations": continuations,
        "split_strategy": "record_id_sha1_82_9_9",
        "sources": sources,
        "task": "normalizer_training_input_v1 -> final LaTeX item",
        "system_prompt": SYSTEM_PROMPT,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "README.md").write_text(
        "# Math OCR Final LaTeX Normalization Golden V1\n\n"
        "Supervised fine-tuning dataset for Auditor-IA normalizer V1.\n\n"
        "Input: compact staging JSON with raw OCR, source metadata, figure segmentation and continuation hints.\n\n"
        "Output: final LaTeX item ready for human review before local DB promotion.\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Exporta normalizer_training_bank como dataset SFT.")
    parser.add_argument("--bank-root", default=str(DEFAULT_BANK_ROOT))
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    manifest = export_dataset(Path(args.bank_root), Path(args.out_dir))
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
