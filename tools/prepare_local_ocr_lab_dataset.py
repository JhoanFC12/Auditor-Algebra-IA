from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.modulo0_transcriptor.scan_pipeline.prompts import (
    SYSTEM_PROMPT_RAW_OCR,
    build_prompt_profile_instructions,
)


DEFAULT_GOLDEN_DIRS = [
    ROOT / ".cache" / "transcriptor_runs" / "datasets" / "ocr_geometry_golden_live",
    ROOT / ".cache" / "transcriptor_runs" / "datasets" / "ocr_golden_live",
]

DEFAULT_GOLDEN_STATUSES = {"corrected", "reviewed", "listo"}

SINGLE_IMAGE_TAG_RE = re.compile(r"(?<!\[)\[\s*Imagen\s*=\s*([^\]]+)\](?!\])", re.IGNORECASE)
PROPER_IMAGE_TAG_RE = re.compile(r"\[\[\s*Imagen\s*=\s*([^\]]+)\]\]", re.IGNORECASE)
ANY_IMAGE_TAG_RE = re.compile(r"\[\[?\s*Imagen\s*=\s*([^\]]+?)\]?\]", re.IGNORECASE)
NUMBERED_ITEM_RE = re.compile(r"<\s*\d{1,4}\s*\.\s*>")
CONTINUATION_RE = re.compile(r"^\s*\[CONT\.\]", re.IGNORECASE)


@dataclass
class ExportReport:
    rows: list[dict[str, Any]]
    skipped: list[dict[str, Any]]


def _split_for(sample_id: str) -> str:
    value = int(hashlib.sha1(sample_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    if value < 80:
        return "train"
    if value < 90:
        return "validation"
    return "test"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _safe_file_stem(value: str, fallback: str = "sample") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    return text[:150] or fallback


def _image_tag_training_issues(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return ["empty_text"]
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


def _infer_training_section(row: dict[str, Any]) -> str:
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


def _build_prompt(row: dict[str, Any]) -> str:
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    profile = build_prompt_profile_instructions(
        curso=str(row.get("curso") or source.get("curso") or ""),
        tema=str(row.get("tema") or source.get("tema") or ""),
        book_code=str(row.get("book_code") or source.get("book_code") or ""),
        instance_type=str(row.get("instance_type") or source.get("instance_type") or ""),
        stage="raw_ocr",
    )
    return f"{SYSTEM_PROMPT_RAW_OCR}\n\n{profile}".strip()


def _copy_image(image_src: Path, out_dir: Path, split: str, sample_id: str) -> str:
    if not image_src.exists():
        raise FileNotFoundError(str(image_src))
    suffix = image_src.suffix.lower() or ".png"
    image_dir = out_dir / split / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    image_dst = image_dir / f"{_safe_file_stem(sample_id)}{suffix}"
    if image_src.resolve() != image_dst.resolve():
        shutil.copy2(image_src, image_dst)
    return str(image_dst.relative_to(out_dir)).replace("\\", "/")


def _sample_row(
    *,
    source_kind: str,
    record_path: Path,
    record_id: str,
    image_src: Path,
    target_text: str,
    row: dict[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    sample_id = f"{source_kind}_{record_id}"
    split = _split_for(sample_id)
    rel_image = _copy_image(image_src, out_dir, split, sample_id)
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    book_code = str(row.get("book_code") or source.get("book_code") or "").strip()
    instance_type = str(row.get("instance_type") or source.get("instance_type") or "").strip()
    training_section = _infer_training_section({**row, "book_code": book_code, "instance_type": instance_type})
    prompt = _build_prompt({**row, "book_code": book_code, "instance_type": instance_type})
    return {
        "schema_version": "local_math_ocr_sample_v1",
        "id": sample_id,
        "record_id": record_id,
        "split": split,
        "image": rel_image,
        "text": target_text,
        "raw_candidate": str(row.get("ocr_model_text") or row.get("ocr_text") or row.get("raw_ocr") or ""),
        "prompt": prompt,
        "source_kind": source_kind,
        "source_record_path": str(record_path),
        "source_label": str(row.get("source_label") or source.get("source_label") or row.get("crop_id") or ""),
        "book_code": book_code,
        "instance_type": instance_type,
        "training_section": training_section,
        "status": str(row.get("status") or ""),
        "human_reviewed": True,
    }


def _resolve_golden_image(golden_dir: Path, row: dict[str, Any]) -> Path | None:
    copied_rel = str(row.get("copied_image_rel") or "").strip()
    if copied_rel:
        candidate = golden_dir / copied_rel
        if candidate.exists():
            return candidate.resolve()
    source_path = str(row.get("source_path") or "").strip()
    if source_path:
        candidate = Path(source_path).expanduser()
        if candidate.exists():
            return candidate.resolve()
    return None


def collect_golden_rows(
    golden_dirs: Iterable[Path],
    *,
    out_dir: Path,
    statuses: set[str] | None = None,
    allow_suspect_image_tags: bool = False,
) -> ExportReport:
    allowed = {str(value or "").strip().lower() for value in (statuses or DEFAULT_GOLDEN_STATUSES)}
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for golden_dir in golden_dirs:
        golden_dir = Path(golden_dir).expanduser().resolve()
        records_dir = golden_dir / "records"
        if not records_dir.exists():
            skipped.append({"source": str(golden_dir), "reason": "missing_records_dir"})
            continue
        for record_path in sorted(records_dir.glob("*.json")):
            row = _read_json(record_path)
            if row is None:
                skipped.append({"source": str(record_path), "reason": "invalid_json"})
                continue
            status = str(row.get("status") or "").strip().lower()
            if allowed and status not in allowed:
                skipped.append({"source": str(record_path), "reason": "status", "status": status})
                continue
            target_text = str(row.get("corrected_text") or "").strip()
            if not target_text:
                skipped.append({"source": str(record_path), "reason": "missing_corrected_text"})
                continue
            tag_issues = _image_tag_training_issues(target_text)
            if tag_issues and not allow_suspect_image_tags:
                skipped.append({"source": str(record_path), "reason": "suspect_image_tags", "issues": tag_issues})
                continue
            image_src = _resolve_golden_image(golden_dir, row)
            if image_src is None:
                skipped.append({"source": str(record_path), "reason": "missing_image"})
                continue
            record_id = str(row.get("record_id") or record_path.stem).strip()
            rows.append(
                _sample_row(
                    source_kind=golden_dir.name,
                    record_path=record_path,
                    record_id=record_id,
                    image_src=image_src,
                    target_text=target_text,
                    row=row,
                    out_dir=out_dir,
                )
            )
    return ExportReport(rows=rows, skipped=skipped)


def _iter_staging_record_paths(staging_root: Path) -> Iterable[Path]:
    staging_root = Path(staging_root).expanduser().resolve()
    if (staging_root / "records").exists():
        yield from sorted((staging_root / "records").glob("*.json"))
        return
    for records_dir in sorted(staging_root.glob("*/records")):
        yield from sorted(records_dir.glob("*.json"))


def _staging_raw_ocr_is_human_reviewed(row: dict[str, Any]) -> bool:
    trace = row.get("trace") if isinstance(row.get("trace"), dict) else {}
    last_review = trace.get("last_raw_ocr_review") if isinstance(trace.get("last_raw_ocr_review"), dict) else {}
    if str(last_review.get("source") or "") == "human_raw_ocr_editor":
        return True
    steps = row.get("steps") if isinstance(row.get("steps"), dict) else {}
    ocr_step = steps.get("ocr") if isinstance(steps.get("ocr"), dict) else {}
    return str(ocr_step.get("source") or "") == "human_raw_ocr_editor"


def collect_staging_rows(
    staging_roots: Iterable[Path],
    *,
    out_dir: Path,
    include_unreviewed: bool = False,
) -> ExportReport:
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for staging_root in staging_roots:
        for record_path in _iter_staging_record_paths(staging_root):
            row = _read_json(record_path)
            if row is None:
                skipped.append({"source": str(record_path), "reason": "invalid_json"})
                continue
            raw_ocr = str(row.get("raw_ocr") or "").strip()
            if not raw_ocr:
                skipped.append({"source": str(record_path), "reason": "missing_raw_ocr"})
                continue
            human_reviewed = _staging_raw_ocr_is_human_reviewed(row)
            if not human_reviewed and not include_unreviewed:
                skipped.append({"source": str(record_path), "reason": "raw_ocr_not_human_reviewed"})
                continue
            crop_path = Path(str(row.get("crop_path") or "")).expanduser()
            if not crop_path.exists():
                skipped.append({"source": str(record_path), "reason": "missing_crop"})
                continue
            record_id = str(row.get("record_id") or row.get("crop_id") or record_path.stem).strip()
            sample = _sample_row(
                source_kind="staging",
                record_path=record_path,
                record_id=record_id,
                image_src=crop_path.resolve(),
                target_text=raw_ocr,
                row=row,
                out_dir=out_dir,
            )
            sample["human_reviewed"] = human_reviewed
            rows.append(sample)
    return ExportReport(rows=rows, skipped=skipped)


def write_dataset(
    rows: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    out_dir: Path,
    *,
    sources: list[str],
    max_samples: int = 0,
) -> dict[str, Any]:
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    if max_samples > 0:
        rows = sorted(rows, key=lambda item: item["id"])
        rows = rows[:max_samples]
    rows = sorted(rows, key=lambda item: (item["split"], item["id"]))
    rows_by_split: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    for row in rows:
        rows_by_split.setdefault(str(row.get("split") or "train"), []).append(row)
    for split in ("train", "validation", "test"):
        split_rows = rows_by_split.get(split, [])
        (out_dir / f"{split}.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in split_rows),
            encoding="utf-8",
        )
    (out_dir / "skipped_records.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in skipped),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "local_math_ocr_lab_dataset_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sources": sources,
        "counts": {split: len(rows_by_split.get(split, [])) for split in ("train", "validation", "test")},
        "total": sum(len(rows_by_split.get(split, [])) for split in ("train", "validation", "test")),
        "skipped_total": len(skipped),
        "task": "image_crop_to_corrected_raw_math_ocr",
        "target_policy": "Entrenar solo OCR crudo fiel revisado; normalizacion queda fuera de este dataset.",
        "files": {
            "train": "train.jsonl",
            "validation": "validation.jsonl",
            "test": "test.jsonl",
            "skipped": "skipped_records.jsonl",
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def export_dataset(
    *,
    out_dir: Path,
    golden_dirs: Iterable[Path] | None = None,
    staging_roots: Iterable[Path] | None = None,
    statuses: set[str] | None = None,
    allow_suspect_image_tags: bool = False,
    include_unreviewed_staging: bool = False,
    max_samples: int = 0,
) -> dict[str, Any]:
    if golden_dirs is None:
        selected_golden = [path for path in DEFAULT_GOLDEN_DIRS if path.exists()]
    else:
        selected_golden = list(golden_dirs)
    selected_staging = list(staging_roots or [])
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    golden_report = collect_golden_rows(
        selected_golden,
        out_dir=out_dir,
        statuses=statuses,
        allow_suspect_image_tags=allow_suspect_image_tags,
    )
    staging_report = collect_staging_rows(
        selected_staging,
        out_dir=out_dir,
        include_unreviewed=include_unreviewed_staging,
    )
    rows.extend(golden_report.rows)
    rows.extend(staging_report.rows)
    skipped.extend(golden_report.skipped)
    skipped.extend(staging_report.skipped)
    return write_dataset(
        rows,
        skipped,
        out_dir,
        sources=[*(str(path) for path in selected_golden), *(str(path) for path in selected_staging)],
        max_samples=max_samples,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepara un dataset local para experimentar OCR matematico imagen->texto sin endpoint."
    )
    parser.add_argument("--out-dir", required=True, help="Carpeta de salida del dataset local.")
    parser.add_argument("--golden-dir", action="append", default=None, help="Golden OCR dir con records/ e images/.")
    parser.add_argument("--staging-root", action="append", default=None, help="Staging root o carpeta que contiene */records.")
    parser.add_argument("--status", action="append", default=None, help="Estado golden permitido. Repetible.")
    parser.add_argument("--max-samples", type=int, default=0, help="Limita muestras para smoke training.")
    parser.add_argument("--allow-suspect-image-tags", action="store_true")
    parser.add_argument("--include-unreviewed-staging", action="store_true")
    args = parser.parse_args()

    manifest = export_dataset(
        out_dir=Path(args.out_dir),
        golden_dirs=[Path(item) for item in args.golden_dir] if args.golden_dir else None,
        staging_roots=[Path(item) for item in args.staging_root] if args.staging_root else None,
        statuses={str(item).strip().lower() for item in args.status} if args.status else None,
        allow_suspect_image_tags=bool(args.allow_suspect_image_tags),
        include_unreviewed_staging=bool(args.include_unreviewed_staging),
        max_samples=max(0, int(args.max_samples or 0)),
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
