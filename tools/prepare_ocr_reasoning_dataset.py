from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path


def _split_for(record_id: str) -> str:
    value = int(hashlib.sha1(record_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    if value < 80:
        return "train"
    if value < 90:
        return "validation"
    return "test"


SINGLE_IMAGE_TAG_RE = re.compile(r"(?<!\[)\[\s*Imagen\s*=\s*([^\]]+)\](?!\])", re.IGNORECASE)
PROPER_IMAGE_TAG_RE = re.compile(r"\[\[\s*Imagen\s*=\s*([^\]]+)\]\]", re.IGNORECASE)
ANY_IMAGE_TAG_RE = re.compile(r"\[\[?\s*Imagen\s*=\s*([^\]]+?)\]?\]", re.IGNORECASE)
NUMBERED_ITEM_RE = re.compile(r"<\s*\d{1,4}\s*\.\s*>")
CONTINUATION_RE = re.compile(r"^\s*\[CONT\.\]", re.IGNORECASE)

GENERAL_OCR_PROMPT = (
    "Transcribe fielmente todo el contenido visible de la imagen como OCR matematico final. "
    "No resuelvas, no expliques, no resumas y no inventes texto. "
    "Usa exactamente este formato: si la imagen muestra numero de problema, inicia con <n.>. "
    "Si no aparece numero y parece continuacion del problema anterior, inicia con [CONT.]. "
    "Conserva alternativas A), B), C), D), E) en el orden visible. "
    "Usa LaTeX entre $...$ para expresiones matematicas y notacion geometrica. "
    "No insertes etiquetas de imagen, no uses [[Imagen=...]] y no describas graficos; solo transcribe el texto visible."
)

GEOMETRY_OCR_PROMPT = (
    "Reglas geometricas: escribe puntos entre $...$, por ejemplo $A$, $B$, $C$. "
    "Usa $\\overline{AB}$ solo para el segmento; para medida de segmento usa $AB$ sin overline. "
    "Usa arcos como $\\overparen{AB}$. "
    "Usa $\\sphericalangle ABC$ para angulos y $m\\sphericalangle ABC$ para medidas de angulos. "
    "Las medidas sexagesimales van como $50^\\circ$. "
    "Usa $\\Delta ABC$ para triangulos. "
    "Usa $\\dfrac{...}{...}$ para fracciones y no uses \\displaystyle. "
    "No uses \\angle para angulos ni \\overline para medidas de segmentos."
)


def _image_tag_training_issues(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    tags = [tag.strip() for tag in ANY_IMAGE_TAG_RE.findall(raw)]
    proper_tags = [tag.strip() for tag in PROPER_IMAGE_TAG_RE.findall(raw)]
    issues: list[str] = []
    if SINGLE_IMAGE_TAG_RE.search(raw):
        issues.append("etiqueta_imagen_con_un_corchete")
    if len(tags) != len(proper_tags):
        issues.append("etiqueta_imagen_mal_formada")
    if len(tags) > 1:
        issues.append("multiples_etiquetas_imagen_en_registro")
    is_continuation_only = bool(CONTINUATION_RE.search(raw)) and not NUMBERED_ITEM_RE.search(raw)
    if any(tag.lower() == "img-continuacion" for tag in tags) and not is_continuation_only:
        issues.append("img-continuacion_en_problema_numerado_o_no_cont")
    return sorted(set(issues))


def _infer_training_section(row: dict[str, object]) -> str:
    section = str(row.get("training_section") or "").strip()
    if section:
        return section
    probe = " ".join(
        str(row.get(key) or "")
        for key in ("book_code", "instance_type", "source_label", "project_name", "curso", "tema")
    ).casefold()
    if "geometr" in probe or "circunferencia" in probe or "triangulo" in probe:
        return "Geometria"
    if "trigonom" in probe:
        return "Trigonometria"
    if "aritmet" in probe:
        return "Aritmetica"
    if "algebra" in probe or "polinom" in probe:
        return "Algebra"
    return "General"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepara pares imagen -> OCR corregido para fine-tuning multimodal.")
    parser.add_argument("--golden-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--status",
        action="append",
        default=None,
        help="Estado permitido. Puede repetirse. Ejemplo: --status reviewed",
    )
    parser.add_argument(
        "--allow-suspect-image-tags",
        action="store_true",
        help="No excluye muestras con etiquetas [[Imagen=...]] sospechosas. No recomendado para entrenamiento OCR.",
    )
    args = parser.parse_args()

    golden_dir = Path(args.golden_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    allowed_statuses = {str(status or "").strip().lower() for status in (args.status or []) if str(status or "").strip()}
    records_dir = golden_dir / "records"
    if not records_dir.exists():
        raise FileNotFoundError(f"No existe la carpeta de registros: {records_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    rows_by_split: dict[str, list[dict[str, object]]] = {"train": [], "validation": [], "test": []}
    excluded_records: list[dict[str, object]] = []
    skipped_by_status = 0
    for record_path in sorted(records_dir.glob("*.json")):
        row = json.loads(record_path.read_text(encoding="utf-8"))
        status = str(row.get("status") or "").strip().lower()
        if allowed_statuses and status not in allowed_statuses:
            skipped_by_status += 1
            continue
        record_id = str(row.get("record_id") or record_path.stem).strip()
        corrected = str(row.get("corrected_text") or "").strip()
        if not corrected:
            raise ValueError(f"Falta corrected_text en {record_path.name}")
        tag_issues = _image_tag_training_issues(corrected)
        if tag_issues and not args.allow_suspect_image_tags:
            excluded_records.append(
                {
                    "record_id": record_id,
                    "record_json": str(record_path),
                    "source_label": str(row.get("source_label") or ""),
                    "status": status,
                    "issues": tag_issues,
                    "preview": re.sub(r"\s+", " ", corrected).strip()[:320],
                }
            )
            continue
        rel = str(row.get("copied_image_rel") or "").strip()
        image_src = (golden_dir / rel).resolve()
        if not rel or not image_src.exists():
            raise FileNotFoundError(f"Imagen no encontrada para {record_id}: {image_src}")
        training_section = _infer_training_section(row)
        split = _split_for(record_id)
        image_dir = out_dir / split / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        image_dst = image_dir / f"{record_id}{image_src.suffix.lower() or '.png'}"
        shutil.copy2(image_src, image_dst)
        rows_by_split[split].append(
            {
                "id": record_id,
                "image": str(image_dst.relative_to(out_dir)).replace("\\", "/"),
                "text": corrected,
                "raw_ocr": str(row.get("ocr_model_text") or row.get("ocr_text") or ""),
                "source_label": str(row.get("source_label") or ""),
                "book_code": str(row.get("book_code") or ""),
                "instance_type": str(row.get("instance_type") or ""),
                "status": status,
                "training_section": training_section,
                "training_section_confidence": str(row.get("training_section_confidence") or ""),
            }
        )

    for split, rows in rows_by_split.items():
        (out_dir / f"{split}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
    (out_dir / "excluded_records.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in excluded_records),
        encoding="utf-8",
    )
    manifest = {
        "schema": "math_ocr_reasoning_dataset_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(golden_dir),
        "counts": {split: len(rows) for split, rows in rows_by_split.items()},
        "total": sum(len(rows) for rows in rows_by_split.values()),
        "status_filter": sorted(allowed_statuses),
        "skipped_by_status": skipped_by_status,
        "excluded_suspect_image_tags": len(excluded_records),
        "excluded_records": "excluded_records.jsonl",
        "task": "Transcribir fielmente todo el contenido visible de la imagen como OCR matematico final.",
        "general_training_prompt": GENERAL_OCR_PROMPT,
        "geometry_training_prompt": GEOMETRY_OCR_PROMPT,
        "prompt_policy": "Usar siempre general_training_prompt. Agregar geometry_training_prompt solo si training_section contiene 'Geometria'.",
        "recommended_status_filter": ["reviewed"],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
