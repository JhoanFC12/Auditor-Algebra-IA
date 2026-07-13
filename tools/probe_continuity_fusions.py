from __future__ import annotations

import json
import random
import re
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modulos.instance_factory.models import InstancePipelineContext, StagingProblemRecord
from modulos.instance_factory.pipeline import InstancePdfPipelineService

CROPS_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "datasets" / "problem_crops_live"
RECORDS_DIR = CROPS_ROOT / "records"
IMAGES_DIR = CROPS_ROOT / "images"
TRAINING_BANK = REPO_ROOT / ".cache" / "transcriptor_runs" / "datasets" / "normalizer_training_bank" / "samples.jsonl"
REPORTS_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "reports"

PROBLEM_START_RE = re.compile(
    r"^\s*(?:<\s*)?(?:problema|pregunta|n[°ºo]\.?)?\s*\d{1,4}\s*(?:[.)>\-:]|\.>)",
    re.IGNORECASE,
)
OPTION_LABEL_RE = re.compile(r"(?:^|[\s£æ|])([A-E])\s*[\).]", re.IGNORECASE)


@dataclass
class CropRecord:
    record_id: str
    family: str
    project_name: str
    instance: str
    source_page: int
    source_order: int
    crop_path: Path
    bbox: tuple[float, float, float, float]
    raw_ocr: str
    final_latex: str


def _safe_load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _family_from_text(text: str) -> str:
    lowered = text.lower()
    if "nostradamus" in lowered:
        return "Nostradamus"
    if "aseuni" in lowered:
        return "ASEUNI"
    return ""


def _load_training_texts() -> dict[str, dict[str, str]]:
    texts: dict[str, dict[str, str]] = {}
    if not TRAINING_BANK.exists():
        return texts
    with TRAINING_BANK.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            record_id = str(payload.get("record_id") or payload.get("source_record_id") or "").strip()
            if not record_id:
                continue
            raw_ocr = str(payload.get("raw_ocr") or payload.get("ocr_raw") or "").strip()
            final_latex = str(payload.get("final_latex") or payload.get("latex_rendered_item") or "").strip()
            normalized = payload.get("normalized_human")
            if not final_latex and isinstance(normalized, dict):
                final_latex = str(normalized.get("latex_rendered_item") or "").strip()
            texts[record_id] = {"raw_ocr": raw_ocr, "final_latex": final_latex}
    return texts


def _crop_path_from_record(payload: dict[str, Any]) -> Path:
    raw = str(payload.get("crop_path") or "").strip()
    if raw:
        return Path(raw)
    rel = str(payload.get("crop_image_rel") or "").strip()
    if rel:
        return CROPS_ROOT / rel.replace("/", "\\")
    record_id = str(payload.get("crop_id") or payload.get("record_id") or "").strip()
    return IMAGES_DIR / f"{record_id}.png"


def _load_crop_records() -> list[CropRecord]:
    training_texts = _load_training_texts()
    records: list[CropRecord] = []
    for path in RECORDS_DIR.glob("*.json"):
        payload = _safe_load_json(path)
        if not payload:
            continue
        family = _family_from_text(
            " ".join(
                str(payload.get(key) or "")
                for key in ("book_code", "source_instance", "project_name", "source_pdf_path", "session_json")
            )
        )
        if family not in {"Nostradamus", "ASEUNI"}:
            continue
        record_id = str(payload.get("record_id") or payload.get("crop_id") or path.stem).strip()
        crop_path = _crop_path_from_record(payload)
        if not crop_path.exists():
            continue
        text_payload = training_texts.get(record_id, {})
        bbox_raw = list(payload.get("bbox_px") or [0, 0, 0, 0])
        try:
            bbox = tuple(float(value) for value in bbox_raw[:4])
        except Exception:
            bbox = (0.0, 0.0, 0.0, 0.0)
        while len(bbox) < 4:
            bbox = (*bbox, 0.0)
        records.append(
            CropRecord(
                record_id=record_id,
                family=family,
                project_name=str(payload.get("project_name") or payload.get("book_code") or family).strip(),
                instance=str(payload.get("instance_type") or payload.get("source_instance") or "").strip(),
                source_page=int(payload.get("source_page_number") or payload.get("page_number") or 0),
                source_order=int(payload.get("source_order") or payload.get("problem_index") or payload.get("box_index") or 0),
                crop_path=crop_path,
                bbox=bbox[:4],  # type: ignore[index]
                raw_ocr=str(text_payload.get("raw_ocr") or payload.get("ocr_text") or payload.get("corrected_text") or "").strip(),
                final_latex=str(text_payload.get("final_latex") or "").strip(),
            )
        )
    records.sort(key=lambda item: (item.family, item.project_name, item.instance, item.source_page, item.source_order, item.record_id))
    return records


def _starts_problem(text: str) -> bool:
    return bool(PROBLEM_START_RE.search(str(text or "").strip()))


def _option_labels(text: str) -> set[str]:
    return {match.group(1).upper() for match in OPTION_LABEL_RE.finditer(str(text or ""))}


def _bbox_metrics(record: CropRecord) -> dict[str, float]:
    x1, y1, x2, y2 = record.bbox
    return {
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "width": max(0.0, x2 - x1),
        "height": max(0.0, y2 - y1),
    }


def _x_overlap_ratio(first: dict[str, float], second: dict[str, float]) -> float:
    overlap = max(0.0, min(first["x2"], second["x2"]) - max(first["x1"], second["x1"]))
    base = max(1.0, min(first["width"], second["width"]))
    return max(0.0, min(1.0, overlap / base))


def _bbox_is_valid(box: dict[str, float]) -> bool:
    return box["width"] > 0 and box["height"] > 0


def _to_staging_record(record: CropRecord) -> StagingProblemRecord:
    return StagingProblemRecord(
        record_id=record.record_id,
        crop_id=record.record_id,
        crop_path=str(record.crop_path),
        raw_ocr=record.raw_ocr,
        source={
            "page_number": record.source_page,
            "source_page_number": record.source_page,
            "source_order": record.source_order,
            "bbox_px": list(record.bbox),
        },
        review={"final_latex": record.final_latex} if record.final_latex else {},
    )


def _visual_option_block_features(
    record: CropRecord,
    cache: dict[tuple[str, int, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not record.crop_path.exists():
        return {"score": 0.0, "option_rows": 0, "max_segments": 0, "line_rows": 0, "error": "crop_missing"}
    try:
        stat = record.crop_path.stat()
        cache_key = (str(record.crop_path), int(stat.st_size), int(stat.st_mtime_ns))
    except Exception:
        cache_key = (str(record.crop_path), 0, 0)
    if cache is not None and cache_key in cache:
        return dict(cache[cache_key])
    try:
        import numpy as np

        with Image.open(record.crop_path) as image:
            gray = image.convert("L")
            max_width = 520
            if gray.width > max_width:
                ratio = max_width / max(1, gray.width)
                gray = gray.resize((max_width, max(1, int(gray.height * ratio))))
            width, height = gray.size
            mask = np.asarray(gray) < 185
            row_threshold = max(3, int(width * 0.012))
            active_rows = [int(y) for y in np.flatnonzero(mask.sum(axis=1) >= row_threshold)]
            bands: list[tuple[int, int]] = []
            for y in active_rows:
                if not bands or y - bands[-1][1] > 3:
                    bands.append((y, y))
                else:
                    bands[-1] = (bands[-1][0], y)

            option_rows = 0
            bottom_option_rows = 0
            max_segments = 0
            max_word_gap = max(8, int(width * 0.035))
            min_segment_width = max(8, int(width * 0.018))
            for y1, y2 in bands:
                band_height = y2 - y1 + 1
                if band_height <= 1 or band_height > max(24, int(height * 0.16)):
                    continue
                col_threshold = max(1, int(band_height * 0.08))
                active_cols = [int(x) for x in np.flatnonzero(mask[y1 : y2 + 1, :].sum(axis=0) >= col_threshold)]
                segments: list[tuple[int, int]] = []
                for x in active_cols:
                    if not segments or x - segments[-1][1] > max_word_gap:
                        segments.append((x, x))
                    else:
                        segments[-1] = (segments[-1][0], x)
                compact_segments = [
                    (x1, x2)
                    for x1, x2 in segments
                    if x2 - x1 + 1 >= min_segment_width and x2 - x1 + 1 <= width * 0.42
                ]
                if len(compact_segments) >= 2:
                    option_rows += 1
                    if ((y1 + y2) / 2.0) >= height * 0.42:
                        bottom_option_rows += 1
                    max_segments = max(max_segments, len(compact_segments))

            score = 0.0
            if option_rows >= 2:
                score = 0.9
            elif option_rows == 1 and max_segments >= 3:
                score = 0.78
            elif option_rows == 1:
                score = 0.58
            if option_rows and bottom_option_rows == option_rows:
                score = min(0.98, score + 0.04)
            result = {
                "score": round(score, 3),
                "option_rows": option_rows,
                "bottom_option_rows": bottom_option_rows,
                "max_segments": max_segments,
                "line_rows": len(bands),
            }
            if cache is not None:
                cache[cache_key] = dict(result)
            return result
    except Exception as exc:
        return {"score": 0.0, "option_rows": 0, "max_segments": 0, "line_rows": 0, "error": str(exc)}


def _score_pair(
    parent: CropRecord,
    child: CropRecord,
    index: int,
    visual_cache: dict[tuple[str, int, int], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    context = InstancePipelineContext(
        book_code=parent.project_name or "probe",
        instance_type=parent.instance or "probe",
        pdf_path="",
    )
    service = InstancePdfPipelineService(context)
    candidate = service._score_continuation_pair(
        _to_staging_record(parent),
        _to_staging_record(child),
        index=index,
        visual_cache=visual_cache,
    )
    if not candidate:
        return None
    out = dict(candidate)
    out["parent"] = parent
    out["child"] = child
    if out.get("recommendation") == "merge":
        out["recommendation"] = "fusionar"
    elif out.get("recommendation") == "review":
        out["recommendation"] = "revisar"
    parent_text = "\n".join(item for item in (parent.raw_ocr, parent.final_latex) if item)
    child_text = "\n".join(item for item in (child.raw_ocr, child.final_latex) if item)
    features = dict(out.get("features") or {})
    features.setdefault("parent_options", sorted(_option_labels(parent_text)))
    features.setdefault("child_options", sorted(_option_labels(child_text)))
    features.setdefault("text_available", bool(parent_text.strip() or child_text.strip()))
    out["features"] = features
    return {
        **out,
        "parent": parent,
        "child": child,
    }


def _group_by_instance(records: list[CropRecord]) -> dict[str, list[CropRecord]]:
    grouped: dict[str, list[CropRecord]] = {}
    for record in records:
        key = f"{record.family}|{record.project_name}|{record.instance}"
        grouped.setdefault(key, []).append(record)
    return grouped


def _select_examples(records: list[CropRecord], seed: int, weeks_per_family: int, max_examples: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    grouped = _group_by_instance(records)
    rng = random.Random(seed)
    selected: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {"seed": seed, "families": {}, "instances_seen": len(grouped)}

    for family in ("Nostradamus", "ASEUNI"):
        family_groups = [(key, rows) for key, rows in grouped.items() if key.startswith(f"{family}|")]
        diagnostics["families"][family] = {"instances": len(family_groups), "candidates": 0, "selected_instances": []}
        scored_groups: list[tuple[str, list[dict[str, Any]]]] = []
        for key, rows in family_groups:
            rows = sorted(rows, key=lambda item: (item.source_page, item.source_order, item.record_id))
            candidates = []
            visual_cache: dict[tuple[str, int, int], dict[str, Any]] = {}
            for index in range(len(rows) - 1):
                candidate = _score_pair(rows[index], rows[index + 1], index, visual_cache=visual_cache)
                if candidate:
                    candidates.append(candidate)
            if candidates:
                candidates.sort(key=lambda item: (-float(item["confidence"]), int(item["index"])))
                scored_groups.append((key, candidates))
                diagnostics["families"][family]["candidates"] += len(candidates)

        rng.shuffle(scored_groups)
        scored_groups.sort(key=lambda item: (-float(item[1][0]["confidence"]), item[0]))
        for key, candidates in scored_groups[:weeks_per_family]:
            candidate = candidates[0]
            selected.append(candidate)
            diagnostics["families"][family]["selected_instances"].append(
                {
                    "instance": key.split("|", 2)[-1],
                    "candidates": len(candidates),
                    "shown_confidence": candidate["confidence"],
                    "recommendation": candidate["recommendation"],
                }
            )

    selected.sort(key=lambda item: (str(item["parent"].family), str(item["parent"].instance), int(item["index"])))
    return selected[:max_examples], diagnostics


def _negative_reason_bundle(parent: CropRecord, child: CropRecord) -> dict[str, Any] | None:
    page_gap = child.source_page - parent.source_page
    if page_gap < 0 or page_gap > 1:
        return None

    parent_text = "\n".join(item for item in (parent.raw_ocr, parent.final_latex) if item)
    child_text = "\n".join(item for item in (child.raw_ocr, child.final_latex) if item)
    if not parent_text.strip() or not child_text.strip():
        return None

    parent_options = _option_labels(parent_text)
    child_options = _option_labels(child_text)
    parent_complete = len(parent_options) >= 4
    child_starts = _starts_problem(child_text)
    child_has_cont_marker = bool(re.match(r"^\s*(?:\[CONT\.?\]|<\s*CONT\.?\s*>)", child_text, re.IGNORECASE))
    if child_has_cont_marker:
        return None
    if not child_starts and not parent_complete:
        return None

    candidate = _score_pair(parent, child, index=0)
    if candidate and candidate.get("recommendation") == "fusionar":
        return None

    parent_box = _bbox_metrics(parent)
    child_box = _bbox_metrics(child)
    reasons: list[str] = []
    if child_starts:
        reasons.append("el segundo crop inicia numeracion propia")
    if parent_complete:
        reasons.append("el primer crop ya tiene alternativas completas")
    reasons.append("no hay marca [CONT.]")
    return {
        "parent": parent,
        "child": child,
        "confidence": float(candidate.get("confidence") or 0.0) if candidate else 0.0,
        "recommendation": "no fusionar",
        "reasons": reasons,
        "warnings": list(candidate.get("warnings") or []) if candidate else [],
        "features": {
            "parent_page": parent.source_page,
            "child_page": child.source_page,
            "page_gap": page_gap,
            "parent_starts_problem": _starts_problem(parent_text),
            "child_starts_problem": child_starts,
            "parent_options": sorted(parent_options),
            "child_options": sorted(child_options),
            "x_overlap": round(_x_overlap_ratio(parent_box, child_box), 3),
            "text_available": True,
        },
    }


def _select_negative_examples(records: list[CropRecord], seed: int, max_per_family: int = 2) -> list[dict[str, Any]]:
    grouped = _group_by_instance(records)
    rng = random.Random(seed + 17)
    selected: list[dict[str, Any]] = []
    for family in ("Nostradamus", "ASEUNI"):
        family_groups = [(key, rows) for key, rows in grouped.items() if key.startswith(f"{family}|")]
        rng.shuffle(family_groups)
        negatives: list[dict[str, Any]] = []
        for _key, rows in family_groups:
            rows = sorted(rows, key=lambda item: (item.source_page, item.source_order, item.record_id))
            for index in range(len(rows) - 1):
                negative = _negative_reason_bundle(rows[index], rows[index + 1])
                if not negative:
                    continue
                negative["index"] = index
                negatives.append(negative)
        negatives.sort(
            key=lambda item: (
                float(item["confidence"]),
                str(item["parent"].instance),
                int(item["parent"].source_page),
                int(item["parent"].source_order),
            )
        )
        selected.extend(negatives[:max_per_family])
    return selected


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _fit_image(path: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as image:
        image = image.convert("RGB")
        return ImageOps.contain(image, size, method=Image.Resampling.LANCZOS)


def _merged_preview(parent_path: Path, child_path: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(parent_path) as parent, Image.open(child_path) as child:
        parent = parent.convert("RGB")
        child = child.convert("RGB")
        width = max(parent.width, child.width)
        padding = max(12, width // 80)
        canvas = Image.new("RGB", (width, parent.height + child.height + padding), "white")
        canvas.paste(parent, ((width - parent.width) // 2, 0))
        canvas.paste(child, ((width - child.width) // 2, parent.height + padding))
        return ImageOps.contain(canvas, size, method=Image.Resampling.LANCZOS)


def _draw_wrapped(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int], font: ImageFont.ImageFont, fill: str, width_chars: int, line_gap: int = 4) -> int:
    x, y = xy
    for line in textwrap.wrap(text, width=width_chars) or [""]:
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line, font=font)
        y += bbox[3] - bbox[1] + line_gap
    return y


def _draw_image_card(draw: ImageDraw.ImageDraw, canvas: Image.Image, title: str, image: Image.Image, box: tuple[int, int, int, int], font: ImageFont.ImageFont) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=10, outline="#9fb4ca", width=2, fill="#f7fafc")
    draw.text((x1 + 14, y1 + 10), title, font=font, fill="#0f2437")
    inner_x1, inner_y1 = x1 + 14, y1 + 44
    inner_x2, inner_y2 = x2 - 14, y2 - 14
    px = inner_x1 + max(0, (inner_x2 - inner_x1 - image.width) // 2)
    py = inner_y1 + max(0, (inner_y2 - inner_y1 - image.height) // 2)
    canvas.paste(image, (px, py))


def _render_sheet(examples: list[dict[str, Any]], diagnostics: dict[str, Any], output_path: Path) -> None:
    width = 1800
    header_h = 230
    row_h = 560
    margin = 32
    canvas = Image.new("RGB", (width, header_h + row_h * max(1, len(examples)) + margin), "#eef3f8")
    draw = ImageDraw.Draw(canvas)
    title_font = _load_font(34, bold=True)
    h_font = _load_font(22, bold=True)
    body_font = _load_font(18)
    small_font = _load_font(15)

    draw.text((margin, 24), "Prueba aislada: crops sin fusionar vs fusionados", font=title_font, fill="#0b1720")
    subtitle = (
        f"Semanas aleatorias con seed {diagnostics['seed']}. "
        "No modifica staging ni BD. La fusion es una vista previa vertical para OCR."
    )
    _draw_wrapped(draw, subtitle, (margin, 72), body_font, "#33495c", 140)

    x = margin
    for family, info in diagnostics["families"].items():
        label = f"{family}: {info['instances']} instancia(s) vistas, {info['candidates']} candidato(s)"
        draw.rounded_rectangle((x, 128, x + 520, 184), radius=12, fill="#dff6ef", outline="#2bbf9a", width=2)
        draw.text((x + 18, 144), label, font=h_font, fill="#053d35")
        x += 550

    if not examples:
        _draw_wrapped(draw, "No se encontraron candidatos suficientes para la prueba.", (margin, 250), h_font, "#7a1024", 100)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path)
        return

    for row_index, example in enumerate(examples):
        y = header_h + row_index * row_h
        parent: CropRecord = example["parent"]
        child: CropRecord = example["child"]
        draw.rounded_rectangle((margin, y + 12, width - margin, y + row_h - 18), radius=16, fill="#ffffff", outline="#c6d5e3", width=2)
        title = (
            f"{parent.family} | {parent.instance} | p.{parent.source_page}/{child.source_page} | "
            f"conf. {example['confidence']} | {example['recommendation']}"
        )
        draw.text((margin + 18, y + 28), title, font=h_font, fill="#0b1720")
        meta = "Razones: " + "; ".join(example["reasons"][:4])
        if example["warnings"]:
            meta += " | Alertas: " + "; ".join(example["warnings"][:2])
        _draw_wrapped(draw, meta, (margin + 18, y + 58), small_font, "#486176", 170)

        parent_img = _fit_image(parent.crop_path, (440, 190))
        child_img = _fit_image(child.crop_path, (440, 190))
        merged_img = _merged_preview(parent.crop_path, child.crop_path, (580, 410))
        no_merge_box = (margin + 18, y + 112, margin + 590, y + row_h - 40)
        merge_box = (margin + 615, y + 112, margin + 1245, y + row_h - 40)
        text_box = (margin + 1270, y + 112, width - margin - 18, y + row_h - 40)

        draw.rounded_rectangle(no_merge_box, radius=10, outline="#9fb4ca", width=2, fill="#f7fafc")
        draw.text((no_merge_box[0] + 14, no_merge_box[1] + 10), "Sin fusionar: dos crops separados", font=body_font, fill="#0f2437")
        canvas.paste(parent_img, (no_merge_box[0] + 58, no_merge_box[1] + 48))
        canvas.paste(child_img, (no_merge_box[0] + 58, no_merge_box[1] + 250))
        draw.text((no_merge_box[0] + 14, no_merge_box[1] + 224), "continuacion candidata", font=small_font, fill="#52697c")

        _draw_image_card(draw, canvas, "Fusionado: una sola imagen para OCR", merged_img, merge_box, body_font)

        draw.rounded_rectangle(text_box, radius=10, outline="#9fb4ca", width=2, fill="#f7fafc")
        draw.text((text_box[0] + 14, text_box[1] + 10), "Señales usadas", font=body_font, fill="#0f2437")
        signal_lines = [
            f"Padre: {parent.crop_path.name}",
            f"Hijo: {child.crop_path.name}",
            f"Opciones padre: {', '.join(example['features']['parent_options']) or '-'}",
            f"Opciones hijo: {', '.join(example['features']['child_options']) or '-'}",
            f"Overlap X: {example['features']['x_overlap']}",
            f"Texto disponible: {'si' if example['features']['text_available'] else 'no'}",
        ]
        ty = text_box[1] + 48
        for line in signal_lines:
            ty = _draw_wrapped(draw, line, (text_box[0] + 14, ty), small_font, "#243a4d", 48, line_gap=2)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, quality=95)


def _render_compact_slide(examples: list[dict[str, Any]], diagnostics: dict[str, Any], output_path: Path) -> None:
    width, height = 1920, 1080
    canvas = Image.new("RGB", (width, height), "#eef3f8")
    draw = ImageDraw.Draw(canvas)
    title_font = _load_font(34, bold=True)
    h_font = _load_font(18, bold=True)
    body_font = _load_font(14)
    small_font = _load_font(12)

    draw.text((36, 24), "Continuidad de crops: sin fusionar vs fusionado", font=title_font, fill="#0b1720")
    subtitle = (
        f"Seed {diagnostics['seed']} | "
        f"Nostradamus: {diagnostics['families']['Nostradamus']['candidates']} candidato(s) | "
        f"ASEUNI: {diagnostics['families']['ASEUNI']['candidates']} candidato(s) | "
        "prueba aislada, sin modificar staging ni BD"
    )
    draw.text((36, 68), subtitle, font=body_font, fill="#33495c")

    picked: list[dict[str, Any]] = []
    for family in ("Nostradamus", "ASEUNI"):
        family_examples = [item for item in examples if item["parent"].family == family]
        picked.extend(family_examples[:2])
    if len(picked) < 4:
        picked.extend(item for item in examples if item not in picked)
    picked = picked[:4]

    cell_w = (width - 90) // 2
    cell_h = 455
    positions = [
        (36, 120),
        (54 + cell_w, 120),
        (36, 600),
        (54 + cell_w, 600),
    ]
    for idx, example in enumerate(picked):
        x, y = positions[idx]
        parent: CropRecord = example["parent"]
        child: CropRecord = example["child"]
        draw.rounded_rectangle((x, y, x + cell_w, y + cell_h), radius=14, fill="#ffffff", outline="#c6d5e3", width=2)
        title = f"{parent.family} | {parent.instance} | conf. {example['confidence']} | {example['recommendation']}"
        draw.text((x + 16, y + 14), title[:95], font=h_font, fill="#0b1720")

        left_box = (x + 16, y + 54, x + 330, y + cell_h - 20)
        right_box = (x + 345, y + 54, x + 670, y + cell_h - 20)
        info_box = (x + 685, y + 54, x + cell_w - 16, y + cell_h - 20)
        for box, label in ((left_box, "Sin fusionar"), (right_box, "Fusionado")):
            draw.rounded_rectangle(box, radius=10, fill="#f7fafc", outline="#9fb4ca", width=1)
            draw.text((box[0] + 10, box[1] + 8), label, font=body_font, fill="#0f2437")

        parent_img = _fit_image(parent.crop_path, (270, 135))
        child_img = _fit_image(child.crop_path, (270, 135))
        merged_img = _merged_preview(parent.crop_path, child.crop_path, (285, 310))
        canvas.paste(parent_img, (left_box[0] + 22, left_box[1] + 42))
        canvas.paste(child_img, (left_box[0] + 22, left_box[1] + 210))
        canvas.paste(merged_img, (right_box[0] + 20, right_box[1] + 48))

        draw.rounded_rectangle(info_box, radius=10, fill="#f7fafc", outline="#9fb4ca", width=1)
        signals = [
            f"pags: {parent.source_page}->{child.source_page}",
            f"opciones padre: {','.join(example['features']['parent_options']) or '-'}",
            f"opciones hijo: {','.join(example['features']['child_options']) or '-'}",
            f"overlap X: {example['features']['x_overlap']}",
        ]
        ty = info_box[1] + 10
        for line in signals:
            ty = _draw_wrapped(draw, line, (info_box[0] + 10, ty), small_font, "#243a4d", 32, line_gap=2)
        ty += 6
        _draw_wrapped(draw, "; ".join(example["reasons"][:3]), (info_box[0] + 10, ty), small_font, "#486176", 32, line_gap=2)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, quality=95)


def _render_negative_slide(examples: list[dict[str, Any]], output_path: Path) -> None:
    width, height = 1920, 1080
    canvas = Image.new("RGB", (width, height), "#f8eef0")
    draw = ImageDraw.Draw(canvas)
    title_font = _load_font(34, bold=True)
    h_font = _load_font(18, bold=True)
    body_font = _load_font(14)
    small_font = _load_font(12)

    draw.text((36, 24), "Control negativo: pares que NO deben fusionarse", font=title_font, fill="#2b0d16")
    draw.text(
        (36, 68),
        "Misma logica de continuidad. La union se rechaza cuando el segundo crop inicia problema propio o el primero ya cerro con alternativas.",
        font=body_font,
        fill="#5c3240",
    )

    if not examples:
        draw.text((36, 140), "No se encontraron pares negativos con OCR suficiente.", font=h_font, fill="#7a1024")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, quality=95)
        return

    positions = [(36, 120), (978, 120), (36, 600), (978, 600)]
    cell_w, cell_h = 906, 430
    for idx, example in enumerate(examples[:4]):
        x, y = positions[idx]
        parent: CropRecord = example["parent"]
        child: CropRecord = example["child"]
        draw.rounded_rectangle((x, y, x + cell_w, y + cell_h), radius=14, fill="#ffffff", outline="#e59aac", width=2)
        title = f"{parent.family} | {parent.instance} | decision: NO FUSIONAR"
        draw.text((x + 16, y + 14), title[:95], font=h_font, fill="#2b0d16")

        first_box = (x + 16, y + 52, x + 345, y + cell_h - 20)
        second_box = (x + 362, y + 52, x + 691, y + cell_h - 20)
        decision_box = (x + 708, y + 52, x + cell_w - 16, y + cell_h - 20)
        for box, label in ((first_box, "Crop A"), (second_box, "Crop B")):
            draw.rounded_rectangle(box, radius=10, fill="#fff8f9", outline="#e59aac", width=1)
            draw.text((box[0] + 10, box[1] + 8), label, font=body_font, fill="#2b0d16")

        first_img = _fit_image(parent.crop_path, (285, 305))
        second_img = _fit_image(child.crop_path, (285, 305))
        canvas.paste(first_img, (first_box[0] + 22, first_box[1] + 48))
        canvas.paste(second_img, (second_box[0] + 22, second_box[1] + 48))

        draw.rounded_rectangle(decision_box, radius=10, fill="#fff8f9", outline="#e59aac", width=1)
        draw.text((decision_box[0] + 10, decision_box[1] + 10), "Resultado", font=body_font, fill="#2b0d16")
        ty = decision_box[1] + 40
        lines = [
            f"score: {example['confidence']}",
            f"pags: {parent.source_page}->{child.source_page}",
            f"A opciones: {','.join(example['features']['parent_options']) or '-'}",
            f"B opciones: {','.join(example['features']['child_options']) or '-'}",
        ]
        for line in lines:
            ty = _draw_wrapped(draw, line, (decision_box[0] + 10, ty), small_font, "#4f2531", 28, line_gap=2)
        ty += 8
        _draw_wrapped(draw, "; ".join(example["reasons"]), (decision_box[0] + 10, ty), small_font, "#7a1024", 28, line_gap=2)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, quality=95)


def main() -> None:
    seed = 20260623
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = REPORTS_ROOT / f"continuity_probe_{timestamp}"
    sheet_path = report_dir / "lamina_crops_fusionados_vs_sin_fusionar.png"
    compact_path = report_dir / "lamina_resumen_16x9.png"
    negative_path = report_dir / "lamina_control_negativo_no_fusionar.png"
    summary_path = report_dir / "summary.json"

    records = _load_crop_records()
    examples, diagnostics = _select_examples(records, seed=seed, weeks_per_family=3, max_examples=6)
    negative_examples = _select_negative_examples(records, seed=seed, max_per_family=2)
    _render_sheet(examples, diagnostics, sheet_path)
    _render_compact_slide(examples, diagnostics, compact_path)
    _render_negative_slide(negative_examples, negative_path)

    serializable_examples = []
    for item in examples:
        parent: CropRecord = item["parent"]
        child: CropRecord = item["child"]
        serializable_examples.append(
            {
                "family": parent.family,
                "project_name": parent.project_name,
                "instance": parent.instance,
                "parent_record_id": parent.record_id,
                "continuation_record_id": child.record_id,
                "parent_crop_path": str(parent.crop_path),
                "continuation_crop_path": str(child.crop_path),
                "confidence": item["confidence"],
                "recommendation": item["recommendation"],
                "reasons": item["reasons"],
                "warnings": item["warnings"],
                "features": item["features"],
            }
        )
    serializable_negatives = []
    for item in negative_examples:
        parent: CropRecord = item["parent"]
        child: CropRecord = item["child"]
        serializable_negatives.append(
            {
                "family": parent.family,
                "project_name": parent.project_name,
                "instance": parent.instance,
                "parent_record_id": parent.record_id,
                "second_record_id": child.record_id,
                "parent_crop_path": str(parent.crop_path),
                "second_crop_path": str(child.crop_path),
                "confidence": item["confidence"],
                "recommendation": item["recommendation"],
                "reasons": item["reasons"],
                "warnings": item["warnings"],
                "features": item["features"],
            }
        )
    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "records_scanned": len(records),
        "sheet_path": str(sheet_path),
        "compact_sheet_path": str(compact_path),
        "negative_sheet_path": str(negative_path),
        "examples_total": len(examples),
        "negative_examples_total": len(negative_examples),
        "diagnostics": diagnostics,
        "examples": serializable_examples,
        "negative_examples": serializable_negatives,
    }
    report_dir.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "sheet_path": str(sheet_path),
                "compact_sheet_path": str(compact_path),
                "negative_sheet_path": str(negative_path),
                "summary_path": str(summary_path),
                "examples_total": len(examples),
                "negative_examples_total": len(negative_examples),
                "diagnostics": diagnostics,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
