from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable

from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import (
    DEFAULT_PROBLEM_CROPS_LIVE_ROOT,
    PdfProblemGoldenController,
    ProblemPageRecord,
    sort_boxes_reading_order,
)

from .model_inventory import build_model_inventory_manifest, resolve_model_defaults, select_server_model_path
from .continuations import continuation_flags_enabled, has_continuation_marker
from .models import (
    PIPELINE_CONTRACT_VERSION,
    InstancePipelineContext,
    PipelineStep,
    StageStatus,
    StagingProblemRecord,
    build_pipeline_contract,
    utc_now_text,
)
from .normalizer_inference import (
    HfOcrNormalizerClient,
    normalizer_input_from_record,
    repair_final_latex_with_normalizer_input,
)
from .page_selection import parse_page_selection
from .staging import InstanceStagingStore
from .training_bank import (
    persist_figure_segment_correction,
    persist_problem_detector_correction,
    persist_raw_ocr_correction,
)


OCR_INPUT_ARTIFACT_KEY = "ocr_input_crop_path"
MERGED_CROP_ARTIFACT_KEY = "merged_crop_path"
PROBLEM_START_RE = re.compile(
    r"^\s*(?:<\s*)?(?:problema|pregunta|n[°ºo]\.?)?\s*\d{1,4}\s*(?:[.)>\-:]|\.>)",
    re.IGNORECASE,
)
OPTION_LABEL_RE = re.compile(r"(?:^|[\s£æ|])([A-E])\s*[\?¿!:\-]*\s*[\).]", re.IGNORECASE)
AUX_PROBLEM_START_RE = re.compile(
    r"^\s*(?:<\s*)?(?:problema|pregunta|n[Â°Âºo]\.?)?\s*\d{1,4}\s*(?:[.)>\-:]|(?=\s+[A-ZÁÉÍÓÚÑ]))",
    re.IGNORECASE,
)
AUX_PROBLEM_LABEL_START_RE = re.compile(
    r"^\s*(?:[^\w<]{0,6}\s*)?(?:problema|pregunta|ejercicio|item)\b",
    re.IGNORECASE,
)
AUX_PROBLEM_NUMBER_PREFIX_RE = re.compile(
    r"^\s*(?:[^\w<]{0,6}\s*)?(?:n|no|num|numero)\s*\W*\s*(?:\d{1,4}|[a-z]{1,5}\d*)\b",
    re.IGNORECASE,
)
AUX_PROBLEM_NUMBER_EARLY_RE = re.compile(
    r"\b(?:n|no|num|numero)\s*\W*\s*(?:\d{1,4}|[a-z]{1,5}\d*)\b",
    re.IGNORECASE,
)
AUX_NOISY_PROBLEM_NUMBER_EARLY_RE = re.compile(
    r"\b(?:n|no|nro|num|numero)\s*[^\w\s]{0,4}\s*\d{1,4}[a-z]?\b",
    re.IGNORECASE,
)
AUX_UNNUMBERED_PROBLEM_PHRASE_RE = re.compile(
    r"^\s*(?:[^\w<]{0,10}\s*)?(?:"
    r"en\s+(?:el|la|un|una)\b|"
    r"segun\b|"
    r"sea\b|"
    r"si\s+\w|"
    r"se\s+(?:tiene|tienen|cumple|ubica|muestra)\b|"
    r"calcule\b|"
    r"halle\b|"
    r"halla\b|"
    r"determine\b|"
    r"determinar\b|"
    r"indique\b"
    r")",
    re.IGNORECASE,
)
AUX_PROBLEM_CONTEXT_EARLY_RE = re.compile(
    r"\b(?:exami\w*|pr[ia]cti\w*|seminario)\b",
    re.IGNORECASE,
)
AUX_GEOMETRY_OBJECT_PROBLEM_EARLY_RE = re.compile(
    r"\b(?:triangulo|cuadrado|trapecio|romboide|circunferencia|pentagono|poligono)\s+[A-Z]{2,8}\b",
    re.IGNORECASE,
)
DEFAULT_TESSERACT_PATHS = (
    Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
    Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
)
CONTINUATION_BOOLEAN_WEIGHTS = {
    "order_consecutive": 0.16,
    "order_near": 0.06,
    "x_overlap_strong": 0.14,
    "x_overlap_compatible": 0.08,
    "same_page_after": 0.08,
    "vertical_close": 0.12,
    "child_not_taller": 0.04,
    "cross_page": 0.10,
    "parent_bottom": 0.10,
    "child_top": 0.08,
    "child_options_strong": 0.20,
    "child_options_weak": 0.10,
    "parent_missing_options": 0.22,
    "child_no_leading_number": 0.24,
    "parent_has_leading_number": 0.14,
    "split_multiple_choice_rule": 0.28,
    "parent_has_options_penalty": -0.30,
    "child_has_leading_number_penalty": -0.45,
}
_CONTINUITY_DETECTOR_MODEL_CACHE: dict[str, Any] = {}


def _continuity_detector_enabled() -> bool:
    value = str(os.environ.get("PDF_FACTORY_CONTINUITY_DETECTOR") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _continuity_detector_conf_threshold(class_name: str) -> float:
    specific_key = f"PDF_FACTORY_CONTINUITY_DETECTOR_{class_name.upper()}_CONF"
    for key in (specific_key, "PDF_FACTORY_CONTINUITY_DETECTOR_CONF"):
        try:
            return max(0.01, min(0.99, float(os.environ.get(key) or "")))
        except Exception:
            continue
    return 0.22


def _continuity_detector_payload_from_subboxes(
    detections: list[Any] | tuple[Any, ...],
    *,
    source: str,
    checked: bool = True,
) -> dict[str, Any]:
    counts = {"problem": 0, "problem_number": 0, "answer_block": 0}
    max_conf = {"problem": 0.0, "problem_number": 0.0, "answer_block": 0.0}
    clean: list[dict[str, Any]] = []
    for index, raw in enumerate(list(detections or [])):
        if not isinstance(raw, dict):
            continue
        class_name = str(raw.get("class_key") or raw.get("class_name") or raw.get("class") or "").strip()
        key = re.sub(r"[^a-z0-9]+", "_", class_name.lower()).strip("_")
        if key in {"numero", "number"}:
            key = "problem_number"
        elif key in {"alternativas", "alternatives", "options"}:
            key = "answer_block"
        if key not in counts:
            continue
        try:
            confidence = float(raw.get("conf") or raw.get("confidence") or 1.0)
        except Exception:
            confidence = 1.0
        counts[key] += 1
        max_conf[key] = max(max_conf[key], confidence)
        if len(clean) < 12:
            clean.append(
                {
                    "class": key,
                    "confidence": round(confidence, 3),
                    "bbox": [round(float(value), 1) for value in list(raw.get("bbox_px") or raw.get("bbox") or [])[:4]],
                    "index": int(raw.get("idx") or raw.get("index") or index),
                    "source": str(raw.get("source") or source),
                }
            )
    number_conf = _continuity_detector_conf_threshold("problem_number")
    answer_conf = _continuity_detector_conf_threshold("answer_block")
    has_problem_number = max_conf["problem_number"] >= number_conf
    has_answer_block = max_conf["answer_block"] >= answer_conf
    return {
        "available": True,
        "source": source,
        "checked": bool(checked),
        "counts": counts,
        "max_conf": {key: round(value, 3) for key, value in max_conf.items()},
        "detections_total": sum(counts.values()),
        "subbox_detections_total": counts["problem_number"] + counts["answer_block"],
        "has_problem_number": has_problem_number,
        "has_answer_block": has_answer_block,
        "complete_problem": has_problem_number and has_answer_block,
        "detections": clean,
    }


def _continuity_detector_model_path() -> Path | None:
    override = str(os.environ.get("PDF_FACTORY_CONTINUITY_DETECTOR_MODEL") or "").strip().strip('"')
    if override:
        path = Path(override).expanduser()
        return path if path.exists() else None
    try:
        from .runtime_env import load_factory_runtime_env

        load_factory_runtime_env()
    except Exception:
        pass
    try:
        raw = str(select_server_model_path("number_alt_detector", allow_not_ready=True) or "").strip()
    except Exception:
        raw = str(os.environ.get("PDF_PROBLEM_MODEL") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.exists() else None


def _load_continuity_detector_model(path: Path) -> Any:
    key = str(path.resolve())
    if key not in _CONTINUITY_DETECTOR_MODEL_CACHE:
        from ultralytics import YOLO

        _CONTINUITY_DETECTOR_MODEL_CACHE[key] = YOLO(key)
    return _CONTINUITY_DETECTOR_MODEL_CACHE[key]


def _canonical_human_review_text(value: str) -> str:
    return "\n".join(
        line.rstrip()
        for line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ).strip()


def _effective_ocr_image_path(record: StagingProblemRecord) -> Path:
    artifacts = dict(record.artifacts or {})
    for key in (OCR_INPUT_ARTIFACT_KEY, MERGED_CROP_ARTIFACT_KEY):
        value = str(artifacts.get(key) or "").strip()
        if value:
            return Path(value)
    return Path(record.crop_path)


def _record_has_effective_crop(record: StagingProblemRecord) -> bool:
    raw_path = str(record.crop_path or "").strip()
    artifacts = dict(record.artifacts or {})
    artifact_path = str(artifacts.get(OCR_INPUT_ARTIFACT_KEY) or artifacts.get(MERGED_CROP_ARTIFACT_KEY) or "").strip()
    if not raw_path and not artifact_path:
        return False
    try:
        return _effective_ocr_image_path(record).exists()
    except OSError:
        return False


def _record_replaced_by_merged_crop(record: StagingProblemRecord) -> bool:
    source = dict(record.source or {})
    return bool(str(source.get("replaced_by_record_id") or "").strip())


def _record_excluded_from_ocr_work(record: StagingProblemRecord) -> bool:
    source = dict(record.source or {})
    return bool(
        str(source.get("merged_into_record_id") or "").strip()
        or str(source.get("replaced_by_record_id") or "").strip()
    )


def _record_excluded_from_continuation_scan(record: StagingProblemRecord) -> bool:
    source = dict(record.source or {})
    if str(source.get("ocr_input_mode") or "").strip() == "merged_crops_replacement":
        return True
    return _record_excluded_from_ocr_work(record)


def _record_detector_class(record: StagingProblemRecord) -> str:
    source = dict(record.source or {})
    detector_box = source.get("detector_box") if isinstance(source.get("detector_box"), dict) else {}
    raw = (
        source.get("box_class_key")
        or source.get("box_class_name")
        or source.get("box_role")
        or detector_box.get("class_key")
        or detector_box.get("class_name")
        or detector_box.get("role")
        or ""
    )
    return re.sub(r"[^a-z0-9]+", "_", str(raw or "").strip().lower()).strip("_")


def _record_is_problem_crop(record: StagingProblemRecord) -> bool:
    source = dict(record.source or {})
    explicit = _record_detector_class(record)
    if explicit:
        return explicit == "problem"
    if "continuity_problem_crop" in source:
        return bool(source.get("continuity_problem_crop"))
    return True


def _record_ocr_text(record: StagingProblemRecord) -> str:
    review = dict(record.review or {})
    candidates = [
        record.raw_ocr,
        dict(record.normalized or {}).get("latex_rendered_item"),
        dict(record.normalized or {}).get("enunciado_latex"),
        review.get("final_latex"),
        review.get("latex_rendered_item"),
        review.get("human_review_text"),
    ]
    return "\n".join(str(item or "").strip() for item in candidates if str(item or "").strip())


def _starts_problem(text: str) -> bool:
    return bool(PROBLEM_START_RE.search(str(text or "").strip()))


def _option_labels(text: str) -> set[str]:
    return {match.group(1).upper() for match in OPTION_LABEL_RE.finditer(str(text or ""))}


def _looks_like_problem_label_token(token: str) -> bool:
    lowered = str(token or "").strip().lower()
    if not lowered.startswith("p"):
        return False
    score = max(
        SequenceMatcher(None, lowered, "problema").ratio(),
        SequenceMatcher(None, lowered, "pregunta").ratio(),
    )
    return score >= 0.58 or ("m" in lowered and score >= 0.5)


def _aux_starts_problem(text: str) -> bool:
    value = str(text or "").strip()
    if _starts_problem(value):
        return True
    for line in value.splitlines()[:4]:
        clean_line = line.strip()
        if (
            AUX_PROBLEM_START_RE.search(clean_line)
            or AUX_PROBLEM_LABEL_START_RE.search(clean_line)
            or AUX_PROBLEM_NUMBER_PREFIX_RE.search(clean_line)
            or AUX_PROBLEM_NUMBER_EARLY_RE.search(clean_line[:48])
            or AUX_NOISY_PROBLEM_NUMBER_EARLY_RE.search(clean_line[:72])
        ):
            return True
        for token in re.findall(r"[A-Za-z]{4,16}", clean_line[:64])[:4]:
            if _looks_like_problem_label_token(token):
                return True
    return False


def _aux_looks_like_unnumbered_problem(text: str) -> bool:
    value = " ".join(str(text or "").split())
    if not value or _aux_starts_problem(value):
        return False
    if AUX_UNNUMBERED_PROBLEM_PHRASE_RE.search(value[:180]):
        return True
    if AUX_PROBLEM_CONTEXT_EARLY_RE.search(value[:90]):
        return True
    if AUX_GEOMETRY_OBJECT_PROBLEM_EARLY_RE.search(value[:120]):
        return True
    for token in re.findall(r"[A-Za-z]{4,16}", value[:48])[:3]:
        if _looks_like_problem_label_token(token):
            return True
    return False


def _resolve_tesseract_cmd() -> Path | None:
    for key in ("PDF_FACTORY_TESSERACT_CMD", "TESSERACT_CMD", "TESSERACT_EXE"):
        raw = str(os.environ.get(key) or "").strip().strip('"')
        if raw:
            path = Path(raw)
            if path.exists():
                return path
    found = shutil.which("tesseract")
    if found:
        return Path(found)
    for path in DEFAULT_TESSERACT_PATHS:
        if path.exists():
            return path
    return None


def _continuity_tesseract_enabled() -> bool:
    value = str(os.environ.get("PDF_FACTORY_CONTINUITY_TESSERACT") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _preprocess_aux_ocr_image(image: Any, *, target_width: int | None = None, mode: str = "fixed") -> Any:
    from PIL import Image, ImageOps

    image = ImageOps.autocontrast(image.convert("L"))
    if target_width is None:
        try:
            target_width = int(os.environ.get("PDF_FACTORY_CONTINUITY_TESSERACT_WIDTH") or "1200")
        except Exception:
            target_width = 1200
    if image.width > 0 and image.width < target_width:
        ratio = target_width / max(1, image.width)
        image = image.resize((target_width, max(1, int(image.height * ratio))))
    if mode in {"otsu", "adaptive"}:
        try:
            import cv2
            import numpy as np

            arr = np.asarray(image)
            if mode == "adaptive":
                arr = cv2.adaptiveThreshold(
                    arr,
                    255,
                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY,
                    31,
                    11,
                )
            else:
                _, arr = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            return Image.fromarray(arr)
        except Exception:
            pass
    return image.point(lambda px: 255 if px > 182 else 0)


def _preprocess_for_aux_ocr(path: Path):
    from PIL import Image

    image = Image.open(path)
    return _preprocess_aux_ocr_image(image)


def _run_aux_tesseract_pass(
    pytesseract: Any,
    image: Any,
    *,
    lang: str,
    timeout: float,
    psm: int = 6,
    whitelist: str | None = None,
) -> str:
    config = f"--psm {psm}"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"
    return str(
        pytesseract.image_to_string(
            image,
            lang=lang,
            config=config,
            timeout=timeout,
        )
        or ""
    )


def _auxiliary_continuity_zone_ocr(
    path: Path,
    pytesseract: Any,
    *,
    lang: str,
    timeout: float,
) -> tuple[str, list[dict[str, Any]]]:
    from PIL import Image

    with Image.open(path) as source:
        source = source.convert("RGB")
        width, height = source.size
        top = source.crop((0, 0, width, max(1, int(height * 0.28))))
        left = source.crop((0, 0, max(1, int(width * 0.42)), height))
        bottom = source.crop((0, max(0, int(height * 0.42)), width, height))

        number_whitelist = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz.°ºNnPpRrOoBbLlEeMmAa"
        passes: list[tuple[str, Any, str, str | None]] = [
            ("top_otsu", top, "otsu", number_whitelist),
            ("left_otsu", left, "otsu", number_whitelist),
            ("bottom_otsu", bottom, "otsu", None),
            ("bottom_adaptive", bottom, "adaptive", None),
        ]
        zone_texts: list[dict[str, Any]] = []
        collected: list[str] = []
        labels: set[str] = set()
        starts_problem = False

        for zone, zone_image, mode, whitelist in passes:
            target_width = 950 if zone.startswith(("top", "left")) else 1200
            prepared = _preprocess_aux_ocr_image(zone_image, target_width=target_width, mode=mode)
            text = _run_aux_tesseract_pass(
                pytesseract,
                prepared,
                lang=lang,
                timeout=timeout,
                psm=6,
                whitelist=whitelist,
            )
            collected.append(text)
            labels.update(_option_labels(text))
            starts_problem = starts_problem or _aux_starts_problem(text)
            zone_texts.append(
                {
                    "zone": zone,
                    "mode": mode,
                    "text_preview": " ".join(str(text or "").split())[:220],
                    "starts_problem": _aux_starts_problem(text),
                    "option_labels": sorted(_option_labels(text)),
                }
            )

        if len(labels) < 4:
            prepared = _preprocess_aux_ocr_image(source, target_width=1200, mode="otsu")
            text = _run_aux_tesseract_pass(
                pytesseract,
                prepared,
                lang=lang,
                timeout=timeout,
                psm=6,
            )
            collected.append(text)
            labels.update(_option_labels(text))
            starts_problem = starts_problem or _aux_starts_problem(text)
            zone_texts.append(
                {
                    "zone": "full_otsu",
                    "mode": "otsu",
                    "text_preview": " ".join(str(text or "").split())[:220],
                    "starts_problem": _aux_starts_problem(text),
                    "option_labels": sorted(_option_labels(text)),
                }
            )

    return "\n".join(item for item in collected if str(item or "").strip()), zone_texts


def _preprocess_for_aux_ocr_legacy(path: Path):
    from PIL import Image, ImageOps

    image = Image.open(path).convert("L")
    image = ImageOps.autocontrast(image)
    try:
        target_width = int(os.environ.get("PDF_FACTORY_CONTINUITY_TESSERACT_WIDTH") or "1200")
    except Exception:
        target_width = 1200
    if image.width > 0 and image.width < target_width:
        ratio = target_width / max(1, image.width)
        image = image.resize((target_width, max(1, int(image.height * ratio))))
    return image.point(lambda px: 255 if px > 182 else 0)


def _auxiliary_continuity_ocr_features(
    record: StagingProblemRecord,
    cache: dict[tuple[str, int, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not _continuity_tesseract_enabled():
        return {"available": False, "disabled": True}
    path = Path(str(record.crop_path or ""))
    if not path.exists():
        return {"available": False, "error": "crop_missing"}
    try:
        stat = path.stat()
        cache_key = (str(path), int(stat.st_size), int(stat.st_mtime_ns))
    except Exception:
        cache_key = (str(path), 0, 0)
    if cache is not None and cache_key in cache:
        return dict(cache[cache_key])

    cmd = _resolve_tesseract_cmd()
    if not cmd:
        result = {"available": False, "error": "tesseract_not_found"}
        if cache is not None:
            cache[cache_key] = dict(result)
        return result
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = str(cmd)
        lang = str(os.environ.get("PDF_FACTORY_CONTINUITY_TESSERACT_LANG") or "eng").strip() or "eng"
        timeout = float(os.environ.get("PDF_FACTORY_CONTINUITY_TESSERACT_TIMEOUT") or "4")
        text, zone_texts = _auxiliary_continuity_zone_ocr(
            path,
            pytesseract,
            lang=lang,
            timeout=timeout,
        )
        labels = _option_labels(text)
        result = {
            "available": True,
            "cmd": str(cmd),
            "lang": lang,
            "starts_problem": _aux_starts_problem(text),
            "looks_like_unnumbered_problem": _aux_looks_like_unnumbered_problem(text),
            "option_labels": sorted(labels),
            "option_count": len(labels),
            "has_options": len(labels) >= 2,
            "complete_options": len(labels) >= 4,
            "text": str(text or ""),
            "text_preview": " ".join(str(text or "").split())[:220],
            "zone_texts": zone_texts,
            "ocr_passes": len(zone_texts),
        }
    except Exception as exc:
        result = {"available": False, "cmd": str(cmd), "error": str(exc)}
    if cache is not None:
        cache[cache_key] = dict(result)
    return result


def _bbox_metrics(record: StagingProblemRecord) -> dict[str, float]:
    source = dict(record.source or {})
    bbox = source.get("bbox_px") or []
    try:
        x1, y1, x2, y2 = [float(value) for value in list(bbox)[:4]]
    except Exception:
        return {"x1": 0.0, "y1": 0.0, "x2": 0.0, "y2": 0.0, "width": 0.0, "height": 0.0}
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


def _source_order_value(source: dict[str, Any]) -> int | None:
    for key in ("source_order", "problem_index", "box_index", "page_problem_index"):
        try:
            value = int(source.get(key))
        except Exception:
            continue
        if value >= 0:
            return value
    return None


def _page_number_value(source: dict[str, Any]) -> int:
    for key in ("page_number", "source_page_number"):
        try:
            value = int(source.get(key))
        except Exception:
            continue
        if value >= 0:
            return value
    return 10**9


def _continuity_uses_global_source_order(records: list[StagingProblemRecord]) -> bool:
    values = [
        value
        for record in records
        if (value := _source_order_value(dict(record.source or {}))) is not None
    ]
    if len(values) < 3:
        return False
    unique_ratio = len(set(values)) / max(1, len(values))
    max_value = max(values)
    pages = {
        _page_number_value(dict(record.source or {}))
        for record in records
        if _page_number_value(dict(record.source or {})) < 10**9
    }
    return unique_ratio >= 0.65 and max_value >= max(8, len(pages) * 2, int(len(values) * 0.45))


def _continuity_reading_order_key(
    record: StagingProblemRecord,
    *,
    global_source_order: bool,
) -> tuple[int, int, int, int, str]:
    source = dict(record.source or {})
    order = _source_order_value(source)
    order_key = order if order is not None else 10**9
    page_key = _page_number_value(source)
    bbox = _bbox_metrics(record)
    y_key = int(bbox.get("y1") or 0)
    x_key = int(bbox.get("x1") or 0)
    if global_source_order:
        return (order_key, page_key, y_key, x_key, str(record.record_id or ""))
    return (page_key, order_key, y_key, x_key, str(record.record_id or ""))


def _page_image_size(record: StagingProblemRecord) -> tuple[int, int] | None:
    path = str(dict(record.source or {}).get("page_image") or "").strip()
    if not path:
        return None
    try:
        from PIL import Image

        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None


def _effective_crop_path(record: StagingProblemRecord) -> Path:
    source = dict(record.source or {})
    raw = str(record.crop_path or source.get("crop_path") or "").strip()
    return Path(raw) if raw else Path("")


def _visual_option_block_features(
    record: StagingProblemRecord,
    cache: dict[tuple[str, int, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    path = _effective_crop_path(record)
    if not path.exists():
        return {"score": 0.0, "option_rows": 0, "max_segments": 0, "line_rows": 0, "error": "crop_missing"}
    try:
        stat = path.stat()
        cache_key = (str(path), int(stat.st_size), int(stat.st_mtime_ns))
    except Exception:
        cache_key = (str(path), 0, 0)
    if cache is not None and cache_key in cache:
        return dict(cache[cache_key])
    try:
        from PIL import Image
        import numpy as np

        with Image.open(path) as image:
            gray = image.convert("L")
            max_width = 520
            if gray.width > max_width:
                ratio = max_width / max(1, gray.width)
                gray = gray.resize((max_width, max(1, int(gray.height * ratio))))
            width, height = gray.size
            if width <= 0 or height <= 0:
                return {"score": 0.0, "option_rows": 0, "max_segments": 0, "line_rows": 0, "error": "empty_image"}
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
            vertical_option_rows = 0
            bottom_option_rows = 0
            max_segments = 0
            option_row_details: list[dict[str, Any]] = []
            vertical_option_details: list[dict[str, Any]] = []
            leading_number_score = 0.0
            leading_number_band: dict[str, Any] | None = None
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
                if not leading_number_band and compact_segments:
                    first_x1, first_x2 = compact_segments[0]
                    first_width = first_x2 - first_x1 + 1
                    second_x1 = compact_segments[1][0] if len(compact_segments) > 1 else width
                    top_ratio = ((y1 + y2) / 2.0) / max(1, height)
                    left_ratio = first_x1 / max(1, width)
                    width_ratio = first_width / max(1, width)
                    gap_ratio = max(0, second_x1 - first_x2) / max(1, width)
                    if (
                        len(compact_segments) <= 2
                        and top_ratio <= 0.3
                        and left_ratio <= 0.14
                        and width_ratio <= 0.18
                        and gap_ratio >= 0.018
                    ):
                        leading_number_score = 0.86
                        leading_number_band = {
                            "y": round(top_ratio, 3),
                            "x": round(left_ratio, 3),
                            "width": round(width_ratio, 3),
                            "gap_after": round(gap_ratio, 3),
                            "segments": len(compact_segments),
                        }
                mid_y = (y1 + y2) / 2.0
                option_like_row = len(compact_segments) >= 3 or mid_y >= height * 0.42
                if len(compact_segments) >= 2 and option_like_row:
                    option_rows += 1
                    if mid_y >= height * 0.42:
                        bottom_option_rows += 1
                    max_segments = max(max_segments, len(compact_segments))
                    option_row_details.append(
                        {
                            "y": round(mid_y / max(1, height), 3),
                            "segments": len(compact_segments),
                        }
                    )
                if compact_segments and active_cols:
                    first_x1, first_x2 = compact_segments[0]
                    span_width = active_cols[-1] - active_cols[0] + 1
                    row_y_ratio = mid_y / max(1, height)
                    starts_like_option_label = first_x1 <= width * 0.24
                    short_option_span = span_width <= width * 0.42
                    vertical_answer_span = (
                        row_y_ratio >= 0.28
                        and span_width <= width * 0.82
                        and len(compact_segments) <= 5
                    )
                    after_header_zone = row_y_ratio >= 0.12
                    if starts_like_option_label and (short_option_span or vertical_answer_span) and after_header_zone:
                        vertical_option_rows += 1
                        vertical_option_details.append(
                            {
                                "y": round(row_y_ratio, 3),
                                "span": round(span_width / max(1, width), 3),
                                "segments": len(compact_segments),
                            }
                        )

            score = 0.0
            if option_rows >= 2:
                score = 0.9
            elif option_rows == 1 and max_segments >= 3:
                score = 0.78
            elif option_rows == 1:
                score = 0.58
            if vertical_option_rows >= 4:
                score = max(score, 0.94)
            elif vertical_option_rows >= 3:
                score = max(score, 0.9)
            elif vertical_option_rows >= 2:
                score = max(score, 0.62)
            if option_rows and bottom_option_rows == option_rows:
                score = min(0.98, score + 0.04)
            vertical_complete_options = vertical_option_rows >= 4 or (vertical_option_rows >= 3 and option_rows >= 1)
            result = {
                "score": round(score, 3),
                "option_rows": option_rows,
                "vertical_option_rows": vertical_option_rows,
                "vertical_complete_options": vertical_complete_options,
                "bottom_option_rows": bottom_option_rows,
                "max_segments": max_segments,
                "line_rows": len(bands),
                "details": option_row_details[:4],
                "vertical_details": vertical_option_details[:5],
                "leading_number_score": round(leading_number_score, 3),
                "leading_number": leading_number_band or {},
            }
            if cache is not None:
                cache[cache_key] = dict(result)
            return result
    except Exception as exc:
        return {"score": 0.0, "option_rows": 0, "max_segments": 0, "line_rows": 0, "error": str(exc)}


def _continuity_detector_features(
    record: StagingProblemRecord,
    cache: dict[tuple[str, int, int, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    source = dict(record.source or {})
    if source.get("continuity_subboxes_checked") or source.get("continuity_subboxes"):
        return _continuity_detector_payload_from_subboxes(
            list(source.get("continuity_subboxes") or []),
            source="page_detector_subboxes",
            checked=bool(source.get("continuity_subboxes_checked", True)),
        )
    if not _continuity_detector_enabled():
        return {"available": False, "disabled": True}
    path = _effective_crop_path(record)
    if not path.exists():
        return {"available": False, "error": "crop_missing"}
    model_path = _continuity_detector_model_path()
    if model_path is None:
        return {"available": False, "error": "model_not_configured"}
    try:
        stat = path.stat()
        cache_key = (str(path), int(stat.st_size), int(stat.st_mtime_ns), str(model_path.resolve()))
    except Exception:
        cache_key = (str(path), 0, 0, str(model_path))
    if cache is not None and cache_key in cache:
        return dict(cache[cache_key])

    try:
        model = _load_continuity_detector_model(model_path)
        imgsz = int(os.environ.get("PDF_FACTORY_CONTINUITY_DETECTOR_IMGSZ") or "768")
        conf = _continuity_detector_conf_threshold("DEFAULT")
        iou = float(os.environ.get("PDF_FACTORY_CONTINUITY_DETECTOR_IOU") or "0.45")
        max_det = int(os.environ.get("PDF_FACTORY_CONTINUITY_DETECTOR_MAX_DET") or "40")
        result = model.predict(
            str(path),
            imgsz=imgsz,
            conf=conf,
            iou=iou,
            max_det=max_det,
            verbose=False,
        )[0]
        boxes = result.boxes
        names = getattr(model, "names", {}) or {}
        counts = {"problem": 0, "problem_number": 0, "answer_block": 0}
        max_conf = {"problem": 0.0, "problem_number": 0.0, "answer_block": 0.0}
        detections: list[dict[str, Any]] = []
        if boxes is not None:
            classes = boxes.cls.cpu().tolist()
            confs = boxes.conf.cpu().tolist()
            xyxys = boxes.xyxy.cpu().tolist()
            for index, (cls_value, conf_value, xyxy) in enumerate(zip(classes, confs, xyxys)):
                class_id = int(cls_value)
                class_name = str(names.get(class_id, class_id))
                if class_name not in counts:
                    continue
                confidence = float(conf_value)
                counts[class_name] += 1
                max_conf[class_name] = max(max_conf[class_name], confidence)
                if len(detections) < 12:
                    detections.append(
                        {
                            "class": class_name,
                            "confidence": round(confidence, 3),
                            "bbox": [round(float(value), 1) for value in list(xyxy)[:4]],
                            "index": index,
                        }
                    )
        number_conf = _continuity_detector_conf_threshold("problem_number")
        answer_conf = _continuity_detector_conf_threshold("answer_block")
        has_problem_number = max_conf["problem_number"] >= number_conf
        has_answer_block = max_conf["answer_block"] >= answer_conf
        result_payload = {
            "available": True,
            "model_path": str(model_path),
            "imgsz": imgsz,
            "conf": conf,
            "iou": iou,
            "counts": counts,
            "max_conf": {key: round(value, 3) for key, value in max_conf.items()},
            "detections_total": sum(counts.values()),
            "subbox_detections_total": counts["problem_number"] + counts["answer_block"],
            "has_problem_number": has_problem_number,
            "has_answer_block": has_answer_block,
            "complete_problem": has_problem_number and has_answer_block,
            "detections": detections,
        }
    except Exception as exc:
        result_payload = {"available": False, "model_path": str(model_path), "error": str(exc)}
    if cache is not None:
        cache[cache_key] = dict(result_payload)
    return result_payload


class InstancePdfPipelineService:
    def __init__(
        self,
        context: InstancePipelineContext,
        *,
        golden_controller: PdfProblemGoldenController | None = None,
        staging_store: InstanceStagingStore | None = None,
    ) -> None:
        self.context = context
        self.golden = golden_controller or PdfProblemGoldenController()
        self.staging = staging_store or InstanceStagingStore(context)
        self.models = resolve_model_defaults()
        self._pages_cache_signature: tuple[tuple[str, int, int], ...] | None = None
        self._pages_cache_rows: list[ProblemPageRecord] | None = None

    def _server_model_reference(self, stage_key: str, *, override: str = "") -> str:
        raw_override = str(override or "").strip()
        if raw_override:
            return raw_override
        return select_server_model_path(stage_key, self.models, allow_not_ready=True)

    @classmethod
    def from_library_instance(
        cls,
        book: dict[str, Any],
        instance: dict[str, Any],
        *,
        db_name: str = "",
        session_path: str | Path | None = None,
        golden_controller: PdfProblemGoldenController | None = None,
        staging_store: InstanceStagingStore | None = None,
    ) -> "InstancePdfPipelineService":
        context = InstancePipelineContext.from_library_instance(
            book,
            instance,
            db_name=db_name,
            session_path=session_path,
        )
        return cls(context, golden_controller=golden_controller, staging_store=staging_store)

    @classmethod
    def run_from_library_instance(
        cls,
        book: dict[str, Any],
        instance: dict[str, Any],
        *,
        db_name: str = "",
        session_path: str | Path | None = None,
        golden_controller: PdfProblemGoldenController | None = None,
        staging_store: InstanceStagingStore | None = None,
        **run_options: Any,
    ) -> dict[str, Any]:
        service = cls.from_library_instance(
            book,
            instance,
            db_name=db_name,
            session_path=session_path,
            golden_controller=golden_controller,
            staging_store=staging_store,
        )
        return service.run_instance_pipeline(**run_options)

    def load_pages(self) -> list[ProblemPageRecord]:
        signature = self._page_records_signature()
        if signature is not None and self._pages_cache_signature == signature and self._pages_cache_rows is not None:
            return self._clone_page_rows(self._pages_cache_rows)
        rows = self._dedupe_page_rows(self.golden.load_instance(self.context.instance_name))
        signature = self._page_records_signature()
        if signature is not None:
            self._pages_cache_signature = signature
            self._pages_cache_rows = self._clone_page_rows(rows)
        return self._clone_page_rows(rows)

    def _invalidate_pages_cache(self) -> None:
        self._pages_cache_signature = None
        self._pages_cache_rows = None

    @staticmethod
    def _clone_page_rows(rows: list[ProblemPageRecord]) -> list[ProblemPageRecord]:
        return copy.deepcopy(list(rows or []))

    def _page_records_signature(self) -> tuple[tuple[str, int, int], ...] | None:
        instance_dir = getattr(self.golden, "instance_dir", None)
        if not callable(instance_dir):
            return None
        try:
            records_dir = Path(instance_dir(self.context.instance_name)) / "records"
        except Exception:
            return None
        if not records_dir.exists():
            return tuple()
        signature: list[tuple[str, int, int]] = []
        try:
            paths = sorted(records_dir.glob("*.json"), key=lambda item: item.name.lower())
        except Exception:
            return None
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            signature.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
        return tuple(signature)

    @staticmethod
    def _dedupe_page_rows(rows: list[ProblemPageRecord]) -> list[ProblemPageRecord]:
        by_page: dict[int, ProblemPageRecord] = {}
        for index, row in enumerate(rows or []):
            page_number = int(row.page_number or 0)
            if page_number <= 0:
                continue
            current = by_page.get(page_number)
            if current is None or InstancePdfPipelineService._page_row_score(row, index) >= InstancePdfPipelineService._page_row_score(current, -1):
                by_page[page_number] = row
        return [by_page[key] for key in sorted(by_page)]

    @staticmethod
    def _page_row_score(row: ProblemPageRecord, index: int) -> tuple[int, int, int, int, str]:
        image_exists = 1 if Path(row.image_path).exists() else 0
        detector = str(row.detector_source or "").lower()
        return (
            1 if detector.startswith("pdf_factory") else 0,
            1 if bool(row.reviewed) else 0,
            len(row.boxes or []),
            image_exists,
            int(index),
            str(row.record_id or ""),
        )

    def resolve_page_selection(self, raw_pages: str) -> list[int]:
        import fitz

        pdf_path = self.context.resolved_pdf_path()
        if not pdf_path.exists():
            raise FileNotFoundError(f"No se encontro el PDF: {pdf_path}")
        with fitz.open(pdf_path) as document:
            return parse_page_selection(raw_pages, document.page_count)

    def _model_snapshot(
        self,
        *,
        provider: str = "",
        confidence_overrides: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        provider_overrides = {"ocr": provider} if provider else None
        return self.models.to_dict(
            provider_overrides=provider_overrides,
            confidence_overrides=confidence_overrides,
        )

    def build_stage_overview(
        self,
        *,
        pages: list[ProblemPageRecord] | None = None,
        records: list[StagingProblemRecord] | None = None,
        summary: dict[str, int] | None = None,
    ) -> list[dict[str, str]]:
        pages = pages if pages is not None else self.load_pages()
        records = records if records is not None else self.staging.load_records()
        summary = summary if summary is not None else self.staging.summarize_records(records)
        boxes_total = sum(len(row.boxes) for row in pages)
        reviewed_pages = sum(1 for row in pages if row.reviewed)
        pages_status = self._status_from_counts(
            total=len(pages),
            ready=reviewed_pages,
            needs_review=len(pages) - reviewed_pages if pages else 0,
        )
        boxes_status = self._aggregate_step_status(records, PipelineStep.BOXES)
        if not records:
            boxes_status = StageStatus.READY if boxes_total else StageStatus.PENDING
        crops_status = self._aggregate_step_status(records, PipelineStep.CROPS)
        ocr_status = self._aggregate_step_status(records, PipelineStep.OCR)
        normalization_status = self._aggregate_step_status(records, PipelineStep.NORMALIZATION)
        staging_status = self._aggregate_record_status(records)
        review_detail = (
            f"{summary['ready']}/{summary['records_total']} listos"
            if summary["records_total"]
            else "sin registros"
        )
        return [
            {
                "stage": "Paginas",
                "status": pages_status,
                "detail": f"{len(pages)} pagina(s), {reviewed_pages}/{len(pages)} revisada(s)",
            },
            {
                "stage": "Boxes",
                "status": boxes_status,
                "detail": f"{boxes_total} box(es) detectados para revisar",
            },
            {
                "stage": "Crops",
                "status": crops_status,
                "detail": f"{summary['crops_found']}/{summary['records_total']} crop(s) disponibles",
            },
            {
                "stage": "OCR / Segmentacion",
                "status": ocr_status,
                "detail": f"{summary['ocr_done']}/{summary['records_total']} con OCR, {summary['segments_done']} con segmentacion",
            },
            {
                "stage": "Revision / Normalizacion pendiente",
                "status": normalization_status,
                "detail": f"{summary['normalized_done']}/{summary['records_total']} borrador(es) de revision",
            },
            {
                "stage": "Staging",
                "status": staging_status,
                "detail": f"{review_detail}; {summary['errors']} con error; no inserta directo en problemas",
            },
        ]

    def build_contract_report(self) -> dict[str, Any]:
        records = self.staging.load_records()
        pages = self.load_pages()
        summary = self.staging.summarize_records(records)
        return {
            "schema_version": "instance_pdf_pipeline_contract_report_v1",
            "contract_version": PIPELINE_CONTRACT_VERSION,
            "context": self.context.to_dict(),
            "contract": build_pipeline_contract(),
            "validation": self.staging.validate_contract(records),
            "stage_overview": self.build_stage_overview(pages=pages, records=records, summary=summary),
            "summary": summary,
        }

    def run_instance_pipeline(
        self,
        *,
        pages: str | list[int] | None = None,
        dpi: int = 300,
        confidence: float = 0.25,
        detect_pages: bool = False,
        materialize: bool = True,
        run_ocr: bool = False,
        normalize_existing: bool = False,
        provider: str = "hf",
        curso: str = "SIN_CURSO",
        tema: str = "SIN_TEMA",
        start_n: int = 1,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Run the instance PDF -> staging contract without GUI orchestration."""
        executed: list[dict[str, Any]] = []
        selected_pages: list[int] = []
        should_detect = bool(detect_pages or pages)
        if should_detect:
            if pages is None:
                raise ValueError("pages es requerido cuando detect_pages=True")
            selected_pages = self.resolve_page_selection(pages) if isinstance(pages, str) else [int(page) for page in pages]
            detected = self.detect_pdf_pages(selected_pages, dpi=dpi, confidence=confidence)
            executed.append(
                {
                    "step": PipelineStep.PAGES,
                    "status": StageStatus.READY if detected else StageStatus.PENDING,
                    "pages": selected_pages,
                    "records": len(detected),
                }
            )
        if materialize:
            materialized = self.materialize_crops_to_staging()
            executed.append(
                {
                    "step": PipelineStep.CROPS,
                    "status": self._aggregate_record_status(materialized),
                    "records": len(materialized),
                }
            )
        if run_ocr:
            processed = self.run_ocr_and_segmentation(
                provider=provider,
                curso=curso,
                tema=tema,
                start_n=start_n,
                limit=limit,
            )
            executed.append(
                {
                    "step": "ocr_segmentacion_normalizacion",
                    "status": self._aggregate_record_status(processed),
                    "records": len(processed),
                }
            )
        elif normalize_existing:
            normalized = self.normalize_existing_ocr()
            executed.append(
                {
                    "step": PipelineStep.NORMALIZATION,
                    "status": self._aggregate_record_status(normalized),
                    "records": len(normalized),
                }
            )
        self.staging.rewrite_manifest()
        records = self.staging.load_records()
        return {
            "schema_version": "instance_pdf_pipeline_run_v1",
            "contract_version": PIPELINE_CONTRACT_VERSION,
            "context": self.context.to_dict(),
            "selected_pages": selected_pages,
            "executed": executed,
            "status": self._aggregate_record_status(records),
            "stage_overview": self.build_stage_overview(records=records),
            "staging_root": str(self.staging.root),
            "contract_report": self.build_contract_report(),
            "model_inventory": build_model_inventory_manifest(self.models),
            "policy": {
                "target": "staging_only",
                "never_insert_directly_into_problemas": True,
                "human_corrections_are_training_data": True,
            },
        }

    run_from_instance = run_instance_pipeline

    def build_instance_summary(
        self,
        *,
        pages: list[ProblemPageRecord] | None = None,
        records: list[StagingProblemRecord] | None = None,
        summary: dict[str, int] | None = None,
    ) -> dict[str, int]:
        pages = pages if pages is not None else self.load_pages()
        records = records if records is not None else self.staging.load_records()
        summary = summary if summary is not None else self.staging.summarize_records(records)
        return {
            **summary,
            "pages_total": len(pages),
            "pages_reviewed": sum(1 for row in pages if row.reviewed),
            "boxes_total": sum(len(row.boxes) for row in pages),
            "pages_with_boxes": sum(1 for row in pages if row.boxes),
        }

    def build_page_box_overview(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self.load_pages():
            boxes_total = len(row.boxes)
            status = StageStatus.PENDING
            if row.reviewed and boxes_total:
                status = StageStatus.READY
            elif row.reviewed:
                status = StageStatus.NEEDS_REVIEW
            elif boxes_total:
                status = StageStatus.NEEDS_REVIEW
            rows.append(
                {
                    "record_id": row.record_id,
                    "page_number": int(row.page_number),
                    "status": status,
                    "boxes_total": boxes_total,
                    "reviewed": bool(row.reviewed),
                    "layout_mode": row.layout_mode,
                    "detector_source": row.detector_source,
                    "image_path": str(row.image_path),
                }
            )
        return sorted(rows, key=lambda item: (int(item.get("page_number") or 0), str(item.get("record_id") or "")))

    def update_page_boxes(
        self,
        record_id: str,
        boxes: list[Any],
        *,
        detector_detections: list[Any] | None = None,
        layout_mode: str = "auto",
        reviewed: bool = True,
        reorder: bool = False,
        force_training_capture: bool = False,
    ) -> ProblemPageRecord:
        """Persist reviewed page boxes from a UI without opening the legacy editor."""
        setattr(self, "_last_problem_detector_correction", None)
        setattr(self, "_last_problem_detector_correction_error", None)
        rows = self._dedupe_page_rows(self.load_pages())
        target: ProblemPageRecord | None = None
        for row in rows:
            if str(row.record_id) == str(record_id):
                target = row
                break
        if target is None:
            raise KeyError(f"Pagina no encontrada en la instancia: {record_id}")
        clean_boxes = self._coerce_boxes(boxes)
        previous_boxes = list(target.boxes or [])
        previous_signature = self._boxes_signature(previous_boxes)
        previous_detections = self._page_detector_training_detections(target)
        previous_detector_signature = self._detector_detections_signature(previous_detections)
        baseline_reviewed = bool(target.reviewed)
        target.layout_mode = str(layout_mode or target.layout_mode or "auto")
        target.boxes = sort_boxes_reading_order(clean_boxes, target.layout_mode) if reorder else clean_boxes
        target.box_details = [
            {
                "idx": index,
                "bbox_px": [int(value) for value in box],
                "class_name": "problem",
                "class_key": "problem",
                "role": "problem",
                "conf": 1.0,
                "source": "human_review",
            }
            for index, box in enumerate(target.boxes, start=1)
        ]
        target.detector_detections = self._compose_page_detector_detections(
            target.box_details,
            detector_detections if detector_detections is not None else list(getattr(target, "detector_detections", []) or []),
        )
        target.reviewed = bool(reviewed)
        current_signature = self._boxes_signature(target.boxes)
        current_detections = self._page_detector_training_detections(target)
        current_detector_signature = self._detector_detections_signature(current_detections)
        training_changed = previous_signature != current_signature or previous_detector_signature != current_detector_signature
        if (training_changed or force_training_capture) and target.reviewed:
            try:
                correction = persist_problem_detector_correction(
                    context=self.context,
                    page_record_id=str(target.record_id or record_id),
                    page_number=int(target.page_number or 0),
                    page_image=Path(target.image_path),
                    pdf_path=str(target.pdf_path or self.context.pdf_path or ""),
                    detector_source=str(target.detector_source or ""),
                    layout_mode=str(target.layout_mode or "auto"),
                    previous_boxes=previous_boxes,
                    human_boxes=list(target.boxes or []),
                    previous_detections=previous_detections,
                    human_detections=current_detections,
                    baseline_reviewed=baseline_reviewed,
                    force=bool(force_training_capture),
                    capture_reason="manual_page_training_capture" if force_training_capture and not training_changed else "human_correction",
                )
                if correction.get("saved"):
                    setattr(self, "_last_problem_detector_correction", correction)
            except Exception as exc:
                setattr(
                    self,
                    "_last_problem_detector_correction_error",
                    {
                        "record_id": str(record_id),
                        "error": str(exc),
                        "stage": "problem_detector_correction_capture",
                    },
                )
        self.golden.upsert_instance_rows(self.context.instance_name, [target])
        self._invalidate_pages_cache()
        updated_records: list[StagingProblemRecord] = []
        if previous_signature != current_signature:
            updated_records = self._invalidate_downstream_for_page_boxes_change(target, previous_boxes=previous_boxes)
        invalidated_records = [
            record
            for record in updated_records
            if dict(dict(record.audit or {}).get("downstream_state") or {}).get("status") == "invalidated"
            and dict(dict(record.audit or {}).get("downstream_state") or {}).get("reason") == "page_boxes_changed"
        ]
        setattr(self, "_last_page_boxes_invalidated_count", len(invalidated_records))
        setattr(self, "_last_page_boxes_invalidated_records", list(invalidated_records))
        setattr(self, "_last_page_boxes_updated_records", list(updated_records))
        return target

    def capture_problem_detector_training_pages(
        self,
        record_ids: list[str] | None = None,
        *,
        reviewed_only: bool = True,
    ) -> dict[str, Any]:
        """Copy reviewed full-page annotations into the problem-detector training bank."""
        wanted = {str(item) for item in list(record_ids or []) if str(item or "").strip()}
        rows = self._dedupe_page_rows(self.load_pages())
        results: list[dict[str, Any]] = []
        saved = skipped = errors = 0
        for row in rows:
            record_id = str(getattr(row, "record_id", "") or "")
            if wanted and record_id not in wanted:
                continue
            page_number = int(getattr(row, "page_number", 0) or 0)
            if reviewed_only and not bool(getattr(row, "reviewed", False)):
                skipped += 1
                results.append(
                    {
                        "record_id": record_id,
                        "page_number": page_number,
                        "status": "skipped",
                        "reason": "page_not_reviewed",
                    }
                )
                continue
            detections = self._page_detector_training_detections(row)
            if not detections:
                skipped += 1
                results.append(
                    {
                        "record_id": record_id,
                        "page_number": page_number,
                        "status": "skipped",
                        "reason": "no_detector_segments",
                    }
                )
                continue
            try:
                correction = persist_problem_detector_correction(
                    context=self.context,
                    page_record_id=record_id,
                    page_number=page_number,
                    page_image=Path(getattr(row, "image_path", "")),
                    pdf_path=str(getattr(row, "pdf_path", "") or self.context.pdf_path or ""),
                    detector_source=str(getattr(row, "detector_source", "") or ""),
                    layout_mode=str(getattr(row, "layout_mode", "") or "auto"),
                    previous_boxes=list(getattr(row, "boxes", []) or []),
                    human_boxes=list(getattr(row, "boxes", []) or []),
                    previous_detections=detections,
                    human_detections=detections,
                    baseline_reviewed=bool(getattr(row, "reviewed", False)),
                    force=True,
                    capture_reason="manual_reviewed_pages_batch",
                )
                if correction.get("saved"):
                    saved += 1
                    results.append(
                        {
                            "record_id": record_id,
                            "page_number": page_number,
                            "status": "saved",
                            "correction_id": correction.get("correction_id"),
                            "metadata_path": correction.get("metadata_path"),
                            "label_path": correction.get("label_path"),
                        }
                    )
                else:
                    skipped += 1
                    results.append(
                        {
                            "record_id": record_id,
                            "page_number": page_number,
                            "status": "skipped",
                            "reason": correction.get("reason") or "not_saved",
                        }
                    )
            except Exception as exc:
                errors += 1
                results.append(
                    {
                        "record_id": record_id,
                        "page_number": page_number,
                        "status": "error",
                        "error": str(exc),
                    }
                )
        return {
            "schema_version": "problem_detector_training_page_capture_v1",
            "saved": saved,
            "skipped": skipped,
            "errors": errors,
            "total_considered": len(results),
            "results": results,
        }

    def delete_page_record(self, record_id: str) -> list[ProblemPageRecord]:
        """Remove a detected PDF page from the factory flow and invalidate dependent staging rows."""
        rows = self._dedupe_page_rows(self.load_pages())
        target: ProblemPageRecord | None = None
        for row in rows:
            if str(row.record_id) == str(record_id):
                target = row
                break
        if target is None:
            raise KeyError(f"Pagina no encontrada en la instancia: {record_id}")
        delete_row = getattr(self.golden, "delete_instance_row", None)
        if callable(delete_row):
            delete_row(self.context.instance_name, str(target.record_id))
        else:
            remaining = [row for row in rows if str(row.record_id) != str(target.record_id)]
            self.golden.save_instance(self.context.instance_name, remaining)
        self._invalidate_pages_cache()
        invalidated_records = self._invalidate_downstream_for_page_removed(target)
        setattr(self, "_last_page_removed_invalidated_count", len(invalidated_records))
        setattr(self, "_last_page_removed_invalidated_records", list(invalidated_records))
        return self.load_pages()

    @staticmethod
    def _coerce_boxes(raw_boxes: list[Any]) -> list[tuple[int, int, int, int]]:
        clean: list[tuple[int, int, int, int]] = []
        for raw in raw_boxes or []:
            if not isinstance(raw, (list, tuple)) or len(raw) < 4:
                continue
            try:
                x1, y1, x2, y2 = [int(round(float(value))) for value in list(raw)[:4]]
            except Exception:
                continue
            left, right = sorted((x1, x2))
            top, bottom = sorted((y1, y2))
            if right - left < 8 or bottom - top < 8:
                continue
            clean.append((left, top, right, bottom))
        return clean

    @staticmethod
    def _detector_class_key(value: Any) -> str:
        text = str(value or "").strip().lower()
        key = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
        if key in {"problem_number", "numero", "number"}:
            return "problem_number"
        if key in {"answer_block", "alternatives", "alternativas", "options"}:
            return "answer_block"
        return "problem"

    @classmethod
    def _coerce_detector_detections(cls, raw_detections: list[Any] | tuple[Any, ...]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index, raw in enumerate(list(raw_detections or []), start=1):
            if not isinstance(raw, dict):
                continue
            bbox = raw.get("bbox_px") or raw.get("xyxy") or []
            clean_boxes = cls._coerce_boxes([bbox])
            if not clean_boxes:
                continue
            class_key = cls._detector_class_key(raw.get("class_key") or raw.get("class_name") or raw.get("role"))
            try:
                idx = int(raw.get("idx") or index)
            except Exception:
                idx = index
            try:
                conf = float(raw.get("conf") or 1.0)
            except Exception:
                conf = 1.0
            rows.append(
                {
                    **dict(raw),
                    "idx": idx,
                    "bbox_px": [int(value) for value in clean_boxes[0]],
                    "class_name": class_key,
                    "class_key": class_key,
                    "role": class_key,
                    "conf": conf,
                    "source": str(raw.get("source") or "human_review"),
                }
            )
        return rows

    @classmethod
    def _compose_page_detector_detections(
        cls,
        problem_details: list[dict[str, Any]],
        detector_detections: list[Any] | tuple[Any, ...],
    ) -> list[dict[str, Any]]:
        problem_rows = cls._coerce_detector_detections(problem_details)
        auxiliary_rows = [
            row
            for row in cls._coerce_detector_detections(detector_detections)
            if cls._detector_class_key(row.get("class_key") or row.get("class_name")) != "problem"
        ]
        return problem_rows + auxiliary_rows

    @classmethod
    def _page_detector_training_detections(cls, page: Any) -> list[dict[str, Any]]:
        detections = list(getattr(page, "detector_detections", []) or [])
        if not detections:
            detections = list(getattr(page, "box_details", []) or [])
        return cls._coerce_detector_detections(detections)

    @staticmethod
    def _box_area(box: list[int] | tuple[int, int, int, int]) -> int:
        if len(box) < 4:
            return 0
        return max(0, int(box[2]) - int(box[0])) * max(0, int(box[3]) - int(box[1]))

    @classmethod
    def _box_intersection_ratio(
        cls,
        inner: list[int] | tuple[int, int, int, int],
        outer: list[int] | tuple[int, int, int, int],
    ) -> float:
        if len(inner) < 4 or len(outer) < 4:
            return 0.0
        left = max(int(inner[0]), int(outer[0]))
        top = max(int(inner[1]), int(outer[1]))
        right = min(int(inner[2]), int(outer[2]))
        bottom = min(int(inner[3]), int(outer[3]))
        overlap = cls._box_area([left, top, right, bottom])
        return overlap / max(1, cls._box_area(inner))

    @classmethod
    def _problem_box_match_for_crop(
        cls,
        page: ProblemPageRecord | None,
        bbox: list[Any] | tuple[Any, ...],
    ) -> dict[str, Any]:
        if page is None:
            return {"matched": False, "available": False}
        crop_boxes = cls._coerce_boxes([bbox])
        if not crop_boxes:
            return {"matched": False, "available": False}
        crop_box = crop_boxes[0]
        crop_area = max(1, cls._box_area(crop_box))
        best: dict[str, Any] = {
            "matched": False,
            "available": False,
            "crop_cover": 0.0,
            "problem_cover": 0.0,
            "area_ratio": 0.0,
            "detector_box": {},
        }
        for detection in cls._page_detector_training_detections(page):
            class_key = cls._detector_class_key(detection.get("class_key") or detection.get("class_name") or detection.get("role"))
            if class_key != "problem":
                continue
            det_boxes = cls._coerce_boxes([detection.get("bbox_px") or []])
            if not det_boxes:
                continue
            det_box = det_boxes[0]
            left = max(int(crop_box[0]), int(det_box[0]))
            top = max(int(crop_box[1]), int(det_box[1]))
            right = min(int(crop_box[2]), int(det_box[2]))
            bottom = min(int(crop_box[3]), int(det_box[3]))
            overlap = cls._box_area([left, top, right, bottom])
            problem_area = max(1, cls._box_area(det_box))
            crop_cover = overlap / crop_area
            problem_cover = overlap / problem_area
            score = crop_cover * problem_cover
            if score <= float(best.get("crop_cover") or 0.0) * float(best.get("problem_cover") or 0.0):
                continue
            best = {
                "matched": crop_cover >= 0.72 and problem_cover >= 0.55,
                "available": True,
                "crop_cover": round(crop_cover, 3),
                "problem_cover": round(problem_cover, 3),
                "area_ratio": round(crop_area / problem_area, 3),
                "detector_box": dict(detection),
            }
        return best

    @classmethod
    def _continuity_subboxes_for_crop(
        cls,
        page: ProblemPageRecord | None,
        bbox: list[Any] | tuple[Any, ...],
    ) -> list[dict[str, Any]]:
        if page is None:
            return []
        crop_boxes = cls._coerce_boxes([bbox])
        if not crop_boxes:
            return []
        crop_box = crop_boxes[0]
        rows: list[dict[str, Any]] = []
        for detection in cls._page_detector_training_detections(page):
            class_key = cls._detector_class_key(detection.get("class_key") or detection.get("class_name") or detection.get("role"))
            if class_key == "problem":
                continue
            det_boxes = cls._coerce_boxes([detection.get("bbox_px") or []])
            if not det_boxes:
                continue
            det_box = det_boxes[0]
            center_x = (det_box[0] + det_box[2]) / 2.0
            center_y = (det_box[1] + det_box[3]) / 2.0
            center_inside = crop_box[0] <= center_x <= crop_box[2] and crop_box[1] <= center_y <= crop_box[3]
            if not center_inside and cls._box_intersection_ratio(det_box, crop_box) < 0.45:
                continue
            rows.append(
                {
                    **dict(detection),
                    "class_key": class_key,
                    "class_name": class_key,
                    "bbox_px": [int(value) for value in det_box],
                    "bbox_in_crop_px": [
                        int(det_box[0]) - int(crop_box[0]),
                        int(det_box[1]) - int(crop_box[1]),
                        int(det_box[2]) - int(crop_box[0]),
                        int(det_box[3]) - int(crop_box[1]),
                    ],
                    "source": str(detection.get("source") or "page_detector"),
                }
            )
        return rows

    def _attach_continuity_subboxes_from_pages(self, records: list[StagingProblemRecord]) -> None:
        try:
            pages = self.load_pages()
        except Exception:
            return
        by_record_id = {str(page.record_id or ""): page for page in pages if str(page.record_id or "")}
        by_page_number: dict[int, ProblemPageRecord] = {}
        for page in pages:
            try:
                by_page_number.setdefault(int(page.page_number), page)
            except Exception:
                continue
        for record in records:
            source = dict(record.source or {})
            if source.get("continuity_subboxes_checked") or source.get("continuity_subboxes"):
                continue
            page = by_record_id.get(str(source.get("source_record_id") or ""))
            if page is None:
                try:
                    page = by_page_number.get(int(source.get("page_number") or 0))
                except Exception:
                    page = None
            if page is None:
                continue
            problem_match = self._problem_box_match_for_crop(page, source.get("bbox_px") or [])
            updated_source = {
                **source,
                "continuity_subboxes": self._continuity_subboxes_for_crop(page, source.get("bbox_px") or []),
                "continuity_subboxes_checked": True,
            }
            if problem_match.get("available"):
                updated_source["continuity_problem_crop"] = bool(problem_match.get("matched"))
                updated_source["continuity_problem_match"] = problem_match
            record.source = updated_source

    @classmethod
    def _detector_detections_signature(cls, detections: list[Any] | tuple[Any, ...]) -> str:
        rows = cls._coerce_detector_detections(detections)
        compact = [
            {
                "class_key": row.get("class_key") or row.get("class_name"),
                "bbox_px": [int(value) for value in row.get("bbox_px", [])[:4]],
            }
            for row in rows
        ]
        return json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def _boxes_signature(cls, boxes: list[Any] | tuple[Any, ...]) -> str:
        return json.dumps([list(box) for box in cls._coerce_boxes(list(boxes or []))], separators=(",", ":"))

    @classmethod
    def _coerced_bbox(cls, raw_bbox: Any) -> list[int]:
        clean_bbox = cls._coerce_boxes([raw_bbox])
        if clean_bbox:
            return [int(value) for value in clean_bbox[0]]
        if not isinstance(raw_bbox, (list, tuple)):
            return []
        try:
            return [int(round(float(value))) for value in list(raw_bbox)[:4]]
        except Exception:
            return [int(value) for value in list(raw_bbox)[:4] if isinstance(value, int)]

    @classmethod
    def _bbox_signature(cls, raw_bbox: Any) -> str:
        bbox = cls._coerced_bbox(raw_bbox)
        if not bbox:
            return ""
        return json.dumps([int(v) for v in bbox[:4]], separators=(",", ":"))

    @classmethod
    def _source_dependency_signature(cls, source: dict[str, Any]) -> str:
        bbox_key = cls._bbox_signature(source.get("bbox_px") or [])
        return "|".join(
            [
                str(source.get("book_code") or "").strip(),
                str(source.get("instance_type") or "").strip(),
                str(source.get("pdf_path") or "").strip(),
                str(source.get("page_number") or "").strip(),
                str(source.get("source_record_id") or "").strip(),
                bbox_key,
            ]
        )

    @staticmethod
    def _int_sort_value(value: Any, default: int = 10**9) -> int:
        try:
            number = int(value)
        except Exception:
            return default
        return number if number >= 0 else default

    @classmethod
    def _crop_payload_sort_key(cls, crop_id: str, payload: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
        bbox = payload.get("bbox_px") or []
        y1 = cls._int_sort_value(bbox[1] if isinstance(bbox, (list, tuple)) and len(bbox) > 1 else None)
        x1 = cls._int_sort_value(bbox[0] if isinstance(bbox, (list, tuple)) and len(bbox) > 0 else None)
        return (
            cls._int_sort_value(payload.get("source_page_number")),
            cls._int_sort_value(payload.get("source_order")),
            cls._int_sort_value(payload.get("box_index") or payload.get("page_problem_index") or payload.get("problem_index")),
            y1,
            x1,
            str(crop_id or ""),
        )

    def _invalidate_downstream_for_page_boxes_change(
        self,
        page: ProblemPageRecord,
        *,
        previous_boxes: list[Any] | tuple[Any, ...],
    ) -> list[StagingProblemRecord]:
        changed: list[StagingProblemRecord] = []
        previous_box_signature = self._boxes_signature(list(previous_boxes or []))
        current_box_signature = self._boxes_signature(list(page.boxes or []))
        current_boxes_by_signature: dict[str, tuple[int, list[int]]] = {}
        for index, box in enumerate(self._coerce_boxes(list(page.boxes or [])), start=1):
            signature = self._bbox_signature(box)
            if signature and signature not in current_boxes_by_signature:
                current_boxes_by_signature[signature] = (index, [int(value) for value in box[:4]])
        for record in self.staging.load_records():
            source = dict(record.source or {})
            same_source_record = str(source.get("source_record_id") or "") == str(page.record_id or "")
            same_page = str(source.get("page_number") or "") == str(page.page_number or "")
            if not (same_source_record or same_page):
                continue
            bbox_signature = self._bbox_signature(source.get("bbox_px") or [])
            current_match = current_boxes_by_signature.get(bbox_signature)
            if current_match is not None:
                box_index, bbox = current_match
                updated_source = {
                    **dict(source),
                    "page_number": page.page_number,
                    "source_record_id": page.record_id,
                    "bbox_px": bbox,
                    "box_index": box_index,
                    "page_problem_index": box_index,
                    "page_boxes_signature": current_box_signature,
                }
                if updated_source != source:
                    relinks = list(dict(record.trace or {}).get("source_relinks") or [])
                    relinks.append(
                        {
                            "updated_at": utc_now_text(),
                            "reason": "page_boxes_reordered_or_extended",
                            "previous_source": dict(source),
                            "updated_source": dict(updated_source),
                            "previous_page_boxes_signature": previous_box_signature,
                            "current_page_boxes_signature": current_box_signature,
                            "page_record_id": page.record_id,
                        }
                    )
                    record.source = updated_source
                    record.trace = {**dict(record.trace or {}), "source_relinks": relinks[-20:]}
                    record.set_step(PipelineStep.PAGES, StageStatus.READY, "pagina fuente relinkada tras edicion de boxes")
                    record.set_step(
                        PipelineStep.BOXES,
                        StageStatus.READY,
                        "box fuente preservado tras cambio de orden",
                        bbox_px=bbox,
                        source_record_id=page.record_id,
                    )
                    self._mark_record_downstream_active(record, reason="page_box_source_preserved")
                    record.sync_status_from_steps()
                    record.touch()
                    changed.append(record)
                continue
            self._invalidate_record_downstream(
                record,
                reason="page_boxes_changed",
                clear_crop=True,
                previous_source=dict(source),
                updated_source={
                    **dict(source),
                    "page_number": page.page_number,
                    "source_record_id": page.record_id,
                    "page_boxes_signature": current_box_signature,
                },
                metadata={
                    "previous_page_boxes_signature": previous_box_signature,
                    "current_page_boxes_signature": current_box_signature,
                    "page_record_id": page.record_id,
                },
            )
            changed.append(record)
        if changed:
            self.staging.upsert_many(changed)
        return changed

    def _invalidate_downstream_for_page_removed(self, page: ProblemPageRecord) -> list[StagingProblemRecord]:
        changed: list[StagingProblemRecord] = []
        for record in self.staging.load_records():
            source = dict(record.source or {})
            same_source_record = str(source.get("source_record_id") or "") == str(page.record_id or "")
            same_page = str(source.get("page_number") or "") == str(page.page_number or "")
            if not (same_source_record or same_page):
                continue
            self._invalidate_record_downstream(
                record,
                reason="page_removed_from_boxes",
                clear_crop=True,
                previous_source=dict(source),
                updated_source={
                    **dict(source),
                    "page_number": page.page_number,
                    "source_record_id": page.record_id,
                    "source_removed": True,
                },
                metadata={
                    "page_record_id": page.record_id,
                    "page_number": page.page_number,
                },
            )
            record.set_step(PipelineStep.PAGES, StageStatus.PENDING, "pagina fuente eliminada de boxes")
            record.set_step(PipelineStep.BOXES, StageStatus.PENDING, "boxes fuente eliminados")
            record.sync_status_from_steps()
            changed.append(record)
        if changed:
            self.staging.upsert_many(changed)
        return changed

    def _invalidate_record_downstream(
        self,
        record: StagingProblemRecord,
        *,
        reason: str,
        clear_crop: bool,
        previous_source: dict[str, Any] | None = None,
        updated_source: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        previous = dict(previous_source or record.source or {})
        if updated_source is not None:
            record.source = dict(updated_source)
        if clear_crop:
            record.crop_path = ""
            record.set_step(PipelineStep.CROPS, StageStatus.PENDING, "crop pendiente de regenerar por cambio de box fuente")
        record.raw_ocr = ""
        record.structured_ocr = {}
        record.figure_segmentation = {}
        record.normalized = {}
        record.review = {}
        record.artifacts = {}
        record.golden_sync = {}
        record.errors = []
        record.set_step(PipelineStep.OCR, StageStatus.PENDING, "OCR invalidado por cambio de box fuente")
        record.set_step(PipelineStep.SEGMENTATION, StageStatus.PENDING, "segmentacion grafica invalidada por cambio de box fuente")
        record.set_step(PipelineStep.NORMALIZATION, StageStatus.PENDING, "normalizacion invalidada por cambio de box fuente")
        record.set_step(PipelineStep.REVIEW, StageStatus.PENDING, "revision invalidada por cambio de box fuente")
        invalidations = list(dict(record.trace or {}).get("downstream_invalidations") or [])
        invalidations.append(
            {
                "updated_at": utc_now_text(),
                "reason": str(reason or "source_changed"),
                "previous_source": previous,
                "updated_source": dict(record.source or {}),
                **dict(metadata or {}),
            }
        )
        record.trace = {**dict(record.trace or {}), "downstream_invalidations": invalidations[-20:]}
        record.audit = {
            **dict(record.audit or {}),
            "downstream_state": {
                "status": "invalidated",
                "reason": str(reason or "source_changed"),
                "updated_at": utc_now_text(),
            },
        }
        record.sync_status_from_steps()

    def _mark_record_downstream_active(self, record: StagingProblemRecord, *, reason: str) -> None:
        downstream = dict(dict(record.audit or {}).get("downstream_state") or {})
        if downstream.get("status") != "invalidated":
            return
        record.audit = {
            **dict(record.audit or {}),
            "downstream_state": {
                "status": "active",
                "reason": str(reason or "source_regenerated"),
                "updated_at": utc_now_text(),
            },
        }

    def _reset_normalization_and_review(self, record: StagingProblemRecord, *, reason: str) -> None:
        record.normalized = {}
        record.review = {}
        artifacts = dict(record.artifacts or {})
        for key in (
            "latest_review",
            "review_snapshot",
            "normalizer_output",
            "normalizer_input",
            "normalized",
        ):
            artifacts.pop(key, None)
        record.artifacts = artifacts
        resets = list(dict(record.trace or {}).get("downstream_resets") or [])
        resets.append(
            {
                "updated_at": utc_now_text(),
                "reason": str(reason or "upstream_changed"),
                "cleared": ["normalized", "review"],
            }
        )
        record.trace = {**dict(record.trace or {}), "downstream_resets": resets[-20:]}
        record.set_step(PipelineStep.NORMALIZATION, StageStatus.PENDING, "normalizacion pendiente por cambio previo")
        record.set_step(PipelineStep.REVIEW, StageStatus.PENDING, "revision final pendiente por cambio previo")

    def build_record_stage_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for record in self.staging.load_records():
            source = dict(record.source or {})
            normalized = dict(record.normalized or {})
            segmentation = dict(record.figure_segmentation or {})
            structured = dict(record.structured_ocr or {})
            records_steps = {
                step: record.step_status(step)
                for step in (
                    PipelineStep.PAGES,
                    PipelineStep.BOXES,
                    PipelineStep.CROPS,
                    PipelineStep.OCR,
                    PipelineStep.SEGMENTATION,
                    PipelineStep.NORMALIZATION,
                    PipelineStep.REVIEW,
                )
            }
            rows.append(
                {
                    "record_id": record.record_id,
                    "status": record.status,
                    "page_number": source.get("page_number") or "",
                    "bbox_px": source.get("bbox_px") or [],
                    "crop_exists": bool(record.crop_path and Path(record.crop_path).exists()),
                    "crop_name": Path(record.crop_path).name,
                    "ocr_items": int(structured.get("items_total") or 0),
                    "segments_total": int(segmentation.get("segments_total") or 0),
                    "normalized_number": normalized.get("numero") or "",
                    "errors_total": len(record.errors),
                    "steps": records_steps,
                }
            )
        return rows

    def detect_pdf_pages(
        self,
        pages: list[int],
        *,
        dpi: int = 300,
        confidence: float = 0.25,
        detector_model: str = "",
        replace_existing: bool = False,
        preserve_reviewed: bool = True,
    ) -> list[ProblemPageRecord]:
        import fitz

        pdf_path = self.context.resolved_pdf_path()
        if not pdf_path.exists():
            raise FileNotFoundError(f"No se encontro el PDF: {pdf_path}")
        rows = self._dedupe_page_rows(self.load_pages())
        selected_page_numbers = {int(page) for page in pages}
        existing_by_page = {int(row.page_number or 0): row for row in rows}
        skipped_reviewed_pages: list[int] = []
        removed_rows: list[ProblemPageRecord] = []
        if replace_existing:
            removed_rows = [
                row
                for row in rows
                if int(row.page_number or 0) not in selected_page_numbers
                and not (preserve_reviewed and bool(row.reviewed))
            ]
            rows = [
                row
                for row in rows
                if int(row.page_number or 0) in selected_page_numbers
                or (preserve_reviewed and bool(row.reviewed))
            ]
        temp = Path(tempfile.mkdtemp(prefix="pdf_factory_pages_"))
        with fitz.open(pdf_path) as document:
            matrix = fitz.Matrix(int(dpi) / 72.0, int(dpi) / 72.0)
            for page_number in pages:
                if page_number < 1 or page_number > document.page_count:
                    raise ValueError(f"Pagina fuera del PDF: {page_number}")
                existing = existing_by_page.get(int(page_number))
                if preserve_reviewed and existing is not None and bool(existing.reviewed):
                    skipped_reviewed_pages.append(int(page_number))
                    continue
                rendered = temp / f"page_{page_number:04d}.png"
                document[page_number - 1].get_pixmap(matrix=matrix, alpha=False).save(str(rendered))
                row = self.golden.add_rendered_page(
                    self.context.instance_name,
                    pdf_path=pdf_path,
                    page_number=page_number,
                    rendered_path=rendered,
                )
                active_detector = self._server_model_reference("pdf_detector", override=detector_model)
                row.boxes = self.golden.predict_boxes(
                    row.image_path,
                    confidence=confidence,
                    layout_mode=row.layout_mode,
                    model=active_detector,
                )
                prediction_details = dict(getattr(self.golden, "last_prediction_details", {}) or {})
                if prediction_details:
                    row.box_details = [
                        dict(item)
                        for item in list(prediction_details.get("problem_boxes") or [])
                        if isinstance(item, dict)
                    ]
                    row.detector_detections = [
                        dict(item)
                        for item in list(prediction_details.get("detections") or [])
                        if isinstance(item, dict)
                    ]
                row.detector_source = f"pdf_factory:{active_detector or self.models.pdf_detector}"
                row.reviewed = False
                rows = [existing for existing in rows if int(existing.page_number or 0) != int(row.page_number or 0)]
                rows.append(row)
        final_rows = self._dedupe_page_rows(rows)
        if removed_rows:
            delete_row = getattr(self.golden, "delete_instance_row", None)
            if callable(delete_row):
                for row in removed_rows:
                    try:
                        delete_row(self.context.instance_name, str(row.record_id))
                    except KeyError:
                        continue
                self.golden.upsert_instance_rows(self.context.instance_name, final_rows)
            else:
                self.golden.save_instance(self.context.instance_name, final_rows)
        else:
            self.golden.upsert_instance_rows(self.context.instance_name, final_rows)
        self._invalidate_pages_cache()
        invalidated_records: list[StagingProblemRecord] = []
        for removed in removed_rows:
            invalidated_records.extend(self._invalidate_downstream_for_page_removed(removed))
        setattr(self, "_last_pages_detect_removed_count", len(removed_rows))
        setattr(self, "_last_pages_detect_skipped_reviewed_pages", list(skipped_reviewed_pages))
        setattr(self, "_last_pages_detect_invalidated_count", len(invalidated_records))
        setattr(self, "_last_pages_detect_invalidated_records", list(invalidated_records))
        return self.load_pages()

    def _clear_existing_base_crop_fusion_state(self) -> int:
        """Remove stale fusion metadata before rebuilding staging from detector boxes."""
        changed: list[StagingProblemRecord] = []
        for record in self.staging.load_records():
            source = dict(record.source or {})
            if str(source.get("ocr_input_mode") or "").strip() == "merged_crops_replacement":
                continue
            if str(source.get("replaced_by_record_id") or "").strip():
                continue
            if str(source.get("merged_into_record_id") or "").strip():
                continue
            normalized = dict(record.normalized or {})
            removed = False
            if "continuacion" in normalized:
                normalized.pop("continuacion", None)
                removed = True
            if "continuaciones_fusionadas" in normalized:
                normalized.pop("continuaciones_fusionadas", None)
                removed = True
            if not removed:
                continue
            record.normalized = normalized
            changed.append(record)
        if changed:
            self.staging.upsert_many(changed)
        return len(changed)

    def materialize_crops_to_staging(self, rows: list[ProblemPageRecord] | None = None) -> list[StagingProblemRecord]:
        self._clear_existing_base_crop_fusion_state()
        page_rows = rows if rows is not None else self.load_pages()
        session_path = self.context.resolved_session_path()
        target, crop_ids = self.golden.materialize_problem_crops_for_downstream(
            self.context.instance_name,
            page_rows,
            return_crop_ids=True,
            persist_source_instance=False,
            session_path=session_path,
            book_code=self.context.book_code,
            instance_type=self.context.instance_type,
            project_name=self.context.project_name,
            pdf_path=self.context.pdf_path,
        )
        crop_payloads: list[tuple[tuple[int, int, int, int, int, str], str, Path, dict[str, Any]]] = []
        for crop_id in crop_ids:
            record_path = Path(target) / "records" / f"{crop_id}.json"
            if not record_path.exists():
                continue
            try:
                crop_payload = json.loads(record_path.read_text(encoding="utf-8"))
            except Exception:
                crop_payload = {}
            crop_payloads.append((self._crop_payload_sort_key(crop_id, crop_payload), crop_id, record_path, crop_payload))

        pages_by_record_id = {str(row.record_id or ""): row for row in page_rows if str(row.record_id or "")}
        pages_by_number: dict[int, ProblemPageRecord] = {}
        for row in page_rows:
            try:
                pages_by_number.setdefault(int(row.page_number), row)
            except Exception:
                continue
        prepared_payloads: list[tuple[str, Path, Path, dict[str, Any], dict[str, Any]]] = []
        for _sort_key, crop_id, record_path, crop_payload in sorted(crop_payloads, key=lambda item: item[0]):
            crop_rel = str(crop_payload.get("crop_image_rel") or "").strip()
            crop_path = Path(target) / crop_rel if crop_rel else Path("")
            source_record_id = str(crop_payload.get("source_record_id") or "")
            page = pages_by_record_id.get(source_record_id)
            if page is None:
                try:
                    page = pages_by_number.get(int(crop_payload.get("source_page_number") or 0))
                except Exception:
                    page = None
            detector_box = crop_payload.get("detector_box") if isinstance(crop_payload.get("detector_box"), dict) else {}
            box_class_name = (
                crop_payload.get("box_class_key")
                or crop_payload.get("box_class_name")
                or detector_box.get("class_key")
                or detector_box.get("class_name")
                or detector_box.get("role")
                or ""
            )
            box_class = self._detector_class_key(box_class_name)
            if box_class and box_class != "problem":
                continue
            problem_match = self._problem_box_match_for_crop(page, crop_payload.get("bbox_px") or [])
            if page is not None and problem_match.get("available") and not problem_match.get("matched") and not box_class:
                continue
            continuity_subboxes = self._continuity_subboxes_for_crop(page, crop_payload.get("bbox_px") or [])
            is_problem_crop = bool(box_class == "problem" or problem_match.get("matched") or not problem_match.get("available"))
            new_source = {
                "book_code": self.context.book_code,
                "instance_type": self.context.instance_type,
                "pdf_path": crop_payload.get("source_pdf_path") or self.context.pdf_path,
                "page_number": crop_payload.get("source_page_number"),
                "page_image": crop_payload.get("source_page_image"),
                "source_order": crop_payload.get("source_order"),
                "box_index": crop_payload.get("box_index"),
                "page_problem_index": crop_payload.get("page_problem_index"),
                "problem_index": crop_payload.get("problem_index"),
                "bbox_px": crop_payload.get("bbox_px") or [],
                "box_class_name": box_class or "problem",
                "detector_box": detector_box,
                "crop_id": crop_id,
                "crop_path": str(crop_path),
                "crop_image_rel": crop_rel,
                "source_record_id": source_record_id,
                "source_instance": crop_payload.get("source_instance_full") or crop_payload.get("source_instance") or "",
                "layout_mode": crop_payload.get("layout_mode") or "",
                "session_json": crop_payload.get("session_json") or "",
                "problem_crops_live_record": str(record_path),
                "continuity_subboxes": continuity_subboxes,
                "continuity_subboxes_checked": page is not None,
                "continuity_problem_crop": is_problem_crop,
                "continuity_problem_match": problem_match,
            }
            prepared_payloads.append((crop_id, crop_path, record_path, crop_payload, new_source))

        active_crop_ids = {crop_id for crop_id, *_rest in prepared_payloads}
        for existing_record in self.staging.load_records():
            existing_id = str(existing_record.record_id or "").strip()
            existing_crop_id = str(existing_record.crop_id or "").strip()
            if existing_id in active_crop_ids or existing_crop_id in active_crop_ids:
                continue
            self.staging.delete_record(existing_id or existing_crop_id, rewrite_manifest=False)

        existing_records = self.staging.load_records()
        existing_by_dependency: dict[str, StagingProblemRecord] = {}
        for existing_record in existing_records:
            signature = self._source_dependency_signature(dict(existing_record.source or {}))
            if not signature or not self._bbox_signature(dict(existing_record.source or {}).get("bbox_px") or []):
                continue
            existing_by_dependency.setdefault(signature, copy.deepcopy(existing_record))

        for crop_id, _crop_path, _record_path, _crop_payload, new_source in prepared_payloads:
            signature = self._source_dependency_signature(new_source)
            match = existing_by_dependency.get(signature)
            if match is None or str(match.record_id or "") == str(crop_id or ""):
                continue
            if str(match.record_id or "") not in active_crop_ids:
                self.staging.delete_record(match.record_id, rewrite_manifest=False)

        out: list[StagingProblemRecord] = []
        for crop_id, crop_path, record_path, crop_payload, new_source in prepared_payloads:
            existing = self.staging.get_record(crop_id)
            source_match = existing_by_dependency.get(self._source_dependency_signature(new_source))
            relinked_from = ""
            source_changed = False
            if existing is None and source_match is not None and str(source_match.record_id or "") != str(crop_id or ""):
                relinked_from = str(source_match.record_id or "")
                payload = copy.deepcopy(source_match.to_dict())
                payload["record_id"] = crop_id
                payload["crop_id"] = crop_id
                payload["crop_path"] = str(crop_path)
                payload["source"] = dict(new_source)
                record = StagingProblemRecord.from_dict(payload)
            else:
                record = existing or StagingProblemRecord(record_id=crop_id, crop_id=crop_id, crop_path=str(crop_path))
            previous_source = dict(record.source or {})
            if existing and self._source_dependency_signature(previous_source) != self._source_dependency_signature(new_source):
                self._invalidate_record_downstream(
                    record,
                    reason="crop_source_changed",
                    clear_crop=False,
                    previous_source=previous_source,
                    updated_source=new_source,
                    metadata={
                        "crop_id": crop_id,
                        "previous_bbox_px": previous_source.get("bbox_px") or [],
                        "current_bbox_px": new_source.get("bbox_px") or [],
                    },
                )
                source_changed = True
            elif relinked_from:
                relinks = list(dict(record.trace or {}).get("source_relinks") or [])
                relinks.append(
                    {
                        "updated_at": utc_now_text(),
                        "reason": "crop_id_relinked_after_box_order_change",
                        "previous_record_id": relinked_from,
                        "new_record_id": crop_id,
                        "source": dict(new_source),
                    }
                )
                record.trace = {**dict(record.trace or {}), "source_relinks": relinks[-20:]}
                self._mark_record_downstream_active(record, reason="crop_id_relinked_after_box_order_change")
            record.crop_path = str(crop_path)
            record.record_id = crop_id
            record.crop_id = crop_id
            record.status = StageStatus.normalize(record.status)
            cleaned_normalized = dict(record.normalized or {})
            cleaned_normalized.pop("continuacion", None)
            cleaned_normalized.pop("continuaciones_fusionadas", None)
            record.normalized = cleaned_normalized
            if record.status == StageStatus.ERROR and record.step_status(PipelineStep.CROPS) == StageStatus.ERROR:
                record.status = StageStatus.PENDING
            record.source = new_source
            record.set_step(
                PipelineStep.PAGES,
                StageStatus.READY,
                "pagina fuente vinculada a instancia",
                page_number=crop_payload.get("source_page_number"),
                page_image=crop_payload.get("source_page_image"),
            )
            record.set_step(
                PipelineStep.BOXES,
                StageStatus.READY,
                "box de problema materializado desde Modulo 13",
                bbox_px=crop_payload.get("bbox_px") or [],
                source_record_id=crop_payload.get("source_record_id") or "",
            )
            record.set_step(
                PipelineStep.CROPS,
                StageStatus.READY if crop_path.exists() else StageStatus.ERROR,
                "crop disponible en staging/live" if crop_path.exists() else "crop no encontrado",
                crop_path=str(crop_path),
            )
            if source_changed and crop_path.exists():
                self._mark_record_downstream_active(record, reason="crop_source_regenerated_after_change")
            if not record.raw_ocr:
                record.set_step(PipelineStep.OCR, StageStatus.PENDING, "pendiente de OCR")
            if not record.figure_segmentation:
                record.set_step(PipelineStep.SEGMENTATION, StageStatus.PENDING, "pendiente de segmentacion")
            if not record.normalized:
                record.set_step(PipelineStep.NORMALIZATION, StageStatus.PENDING, "pendiente de normalizacion")
            if not record.review:
                record.set_step(PipelineStep.REVIEW, StageStatus.PENDING, "pendiente de revision humana")
            record.models = {**record.models, **self._model_snapshot()}
            record.confidence.setdefault(
                "pdf_box",
                float(record.models.get("stages", {}).get("pdf_detector", {}).get("confidence", 0.0) or 0.0),
            )
            record.trace = {
                **dict(record.trace or {}),
                "materialized_at": utc_now_text(),
                "raw_sources": {
                    "problem_crops_live_record": str(record_path),
                    "crop_payload_schema": str(crop_payload.get("schema_version") or ""),
                },
            }
            record.sync_status_from_steps()
            if relinked_from and (record.raw_ocr or record.structured_ocr or record.figure_segmentation or record.normalized):
                self._write_raw_artifacts(record)
            record.touch()
            out.append(record)
        self.staging.upsert_many(out, existing_by_identity={})
        return out

    def merge_records_for_ocr(self, record_id: str, continuation_record_ids: list[str]) -> list[StagingProblemRecord]:
        parent_id = str(record_id or "").strip()
        child_ids = [str(item or "").strip() for item in list(continuation_record_ids or []) if str(item or "").strip()]
        if not parent_id:
            raise ValueError("record_id requerido.")
        if not child_ids:
            raise ValueError("Debe indicar al menos una continuacion.")
        all_records = self.staging.load_records()
        by_id = {str(row.record_id or ""): row for row in all_records}
        parent = by_id.get(parent_id)
        if parent is None:
            raise KeyError(parent_id)
        children: list[StagingProblemRecord] = []
        for child_id in child_ids:
            if child_id == parent_id:
                raise ValueError("Un registro no puede fusionarse consigo mismo.")
            child = by_id.get(child_id)
            if child is None:
                raise KeyError(child_id)
            children.append(child)
        ordered = [parent, *children]
        image_paths = [Path(str(row.crop_path or "")) for row in ordered]
        missing = [str(path) for path in image_paths if not path.exists()]
        if missing:
            raise FileNotFoundError(f"No se encontro crop para fusionar: {missing[0]}")

        merge_source_ids = [str(row.record_id or "") for row in ordered]
        merge_digest = hashlib.sha1("|".join(merge_source_ids).encode("utf-8", errors="ignore")).hexdigest()[:12]
        merged_record_id = f"{parent.record_id}__fusion_{merge_digest}"
        merged_crop_id = merged_record_id
        output_dir = self.staging.artifact_dir("merged_crops", merged_record_id, probe_file="crop.png")
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "crop.png"
        merge_meta = self._write_vertical_crop_merge(image_paths, output_path)
        now = utc_now_text()

        parent_source = dict(parent.source or {})
        merged_source = {
            **parent_source,
            "ocr_input_mode": "merged_crops_replacement",
            "merged_from_record_ids": merge_source_ids,
            "merged_from_crop_ids": [str(row.crop_id or "") for row in ordered],
            "source_parent_record_id": parent.record_id,
            "source_continuation_record_ids": [str(row.record_id or "") for row in children],
            "source_continuation_crop_ids": [str(row.crop_id or "") for row in children],
        }
        if "source_order" in parent_source:
            merged_source["source_order"] = parent_source.get("source_order")
        if "box_index" in parent_source:
            merged_source["box_index"] = parent_source.get("box_index")

        merged_record = StagingProblemRecord(
            record_id=merged_record_id,
            crop_id=merged_crop_id,
            crop_path=str(output_path),
            status=StageStatus.PENDING,
            source=merged_source,
            models=copy.deepcopy(parent.models or {}),
            confidence=copy.deepcopy(parent.confidence or {}),
            artifacts={
                "source_crop_paths": [str(path) for path in image_paths],
                "source_record_ids": merge_source_ids,
                "source_crop_ids": [str(row.crop_id or "") for row in ordered],
                **merge_meta,
                OCR_INPUT_ARTIFACT_KEY: str(output_path),
                MERGED_CROP_ARTIFACT_KEY: str(output_path),
            },
            trace={
                **dict(parent.trace or {}),
                "created_from_ocr_crop_merge": {
                    "created_at": now,
                    "merged_record_id": merged_record_id,
                    "parent_record_id": parent.record_id,
                    "continuation_record_ids": [str(row.record_id or "") for row in children],
                    **merge_meta,
                },
            },
            created_at=now,
            updated_at=now,
        )
        merged_record.set_step(PipelineStep.CROPS, StageStatus.READY, "crop fusionado disponible", crop_path=str(output_path))
        merged_record.set_step(PipelineStep.OCR, StageStatus.PENDING, "OCR pendiente sobre crop fusionado")
        merged_record.set_step(PipelineStep.SEGMENTATION, StageStatus.PENDING, "segmentacion pendiente sobre crop fusionado")
        merged_record.set_step(PipelineStep.NORMALIZATION, StageStatus.PENDING, "pendiente de OCR fusionado")
        merged_record.set_step(PipelineStep.REVIEW, StageStatus.PENDING, "pendiente de revision humana")
        merged_record.sync_status_from_steps()

        updated = [merged_record]
        for index, source_record in enumerate(ordered):
            role = "parent" if index == 0 else "continuation"
            source_record.source = {
                **dict(source_record.source or {}),
                "ocr_input_mode": "replaced_by_merged_crop",
                "replaced_by_record_id": merged_record_id,
                "replaced_by_crop_id": merged_crop_id,
                "replacement_role": role,
                "replacement_order": index,
                "replacement_source_record_ids": merge_source_ids,
            }
            if role == "continuation":
                source_record.normalized = {
                    **dict(source_record.normalized or {}),
                    "continuacion": {
                        **(
                            dict(source_record.normalized.get("continuacion") or {})
                            if isinstance(source_record.normalized.get("continuacion"), dict)
                            else {}
                        ),
                        "es_continuacion": True,
                        "fusionar_con_anterior": True,
                        "parent_record_id": parent.record_id,
                        "ocr_input_fusionado": True,
                    },
                }
            source_record.set_step(PipelineStep.OCR, StageStatus.READY, f"reemplazado por crop fusionado {merged_record_id}")
            source_record.set_step(PipelineStep.SEGMENTATION, StageStatus.READY, f"reemplazado por crop fusionado {merged_record_id}")
            source_record.set_step(PipelineStep.NORMALIZATION, StageStatus.READY, f"reemplazado por crop fusionado {merged_record_id}")
            source_record.set_step(PipelineStep.REVIEW, StageStatus.READY, f"reemplazado por crop fusionado {merged_record_id}")
            source_record.trace = {
                **dict(source_record.trace or {}),
                "replaced_by_merged_crop": {
                    "updated_at": now,
                    "merged_record_id": merged_record_id,
                    "merged_crop_path": str(output_path),
                    "replacement_role": role,
                    "replacement_order": index,
                },
            }
            source_record.errors = [
                str(item)
                for item in list(source_record.errors or [])
                if not str(item).startswith(("ocr_crudo:", "ocr_estructura:", "segmentacion_grafica:"))
            ]
            source_record.sync_status_from_steps()
            source_record.touch()
            updated.append(source_record)

        self.staging.upsert_many(updated)
        return updated

    def detect_continuation_candidates(
        self,
        *,
        min_confidence: float = 0.35,
        max_candidates: int = 50,
    ) -> list[dict[str, Any]]:
        return list(
            self.scan_continuation_candidates(
                min_confidence=min_confidence,
                max_candidates=max_candidates,
            ).get("candidates")
            or []
        )

    def scan_continuation_candidates(
        self,
        *,
        min_confidence: float = 0.35,
        max_candidates: int = 50,
    ) -> dict[str, Any]:
        all_rows = self.staging.load_records()
        rows = [
            record
            for record in all_rows
            if not _record_excluded_from_continuation_scan(record)
            and _record_has_effective_crop(record)
        ]
        self._attach_continuity_subboxes_from_pages(rows)
        classified_problem_rows = [
            record
            for record in rows
            if _record_detector_class(record) or "continuity_problem_crop" in dict(record.source or {})
        ]
        if classified_problem_rows:
            rows = [record for record in rows if _record_is_problem_crop(record)]
        global_source_order = _continuity_uses_global_source_order(rows)
        rows = sorted(
            rows,
            key=lambda record: _continuity_reading_order_key(
                record,
                global_source_order=global_source_order,
            ),
        )
        candidates: list[dict[str, Any]] = []
        seen_candidate_pairs: set[tuple[str, str]] = set()
        visual_cache: dict[tuple[str, int, int], dict[str, Any]] = {}
        aux_ocr_cache: dict[tuple[str, int, int], dict[str, Any]] = {}
        detector_cache: dict[tuple[str, int, int, str], dict[str, Any]] = {}
        profile_cache: dict[int, dict[str, Any]] = {}

        def add_candidate(candidate: dict[str, Any] | None) -> None:
            if not candidate:
                return
            pair = (
                str(candidate.get("parent_record_id") or ""),
                str(candidate.get("continuation_record_id") or ""),
            )
            if not pair[0] or not pair[1] or pair in seen_candidate_pairs:
                return
            try:
                confidence = float(candidate.get("confidence") or 0.0)
            except Exception:
                confidence = 0.0
            if confidence >= max(0.0, min(1.0, float(min_confidence))):
                seen_candidate_pairs.add(pair)
                candidates.append(candidate)

        def profile_at(index: int) -> dict[str, Any]:
            if index not in profile_cache:
                profile_cache[index] = self._classify_continuation_crop(
                    rows[index],
                    visual_cache=visual_cache,
                    aux_ocr_cache=aux_ocr_cache,
                    detector_cache=detector_cache,
                )
            return profile_cache[index]

        for index in range(len(rows) - 1):
            parent = rows[index]
            child = rows[index + 1]
            candidate = self._score_continuation_pair(
                parent,
                child,
                index=index,
                visual_cache=visual_cache,
                aux_ocr_cache=aux_ocr_cache,
                detector_cache=detector_cache,
                require_visual_prefilter=False,
            )
            add_candidate(candidate)

        page_midpoints: dict[int, float] = {}
        page_boxes: dict[int, list[dict[str, float]]] = {}
        for record in rows:
            source = dict(record.source or {})
            page_key = _page_number_value(source)
            box = _bbox_metrics(record)
            if page_key >= 10**9 or not _bbox_is_valid(box):
                continue
            page_boxes.setdefault(page_key, []).append(box)
        for page_key, boxes in page_boxes.items():
            min_x = min(box["x1"] for box in boxes)
            max_x = max(box["x2"] for box in boxes)
            page_midpoints[page_key] = (min_x + max_x) / 2.0

        def layout_order_key(record: StagingProblemRecord) -> tuple[int, int, int, int, int, str]:
            source = dict(record.source or {})
            page_key = _page_number_value(source)
            box = _bbox_metrics(record)
            midpoint = page_midpoints.get(page_key)
            column_key = 0
            if midpoint is not None and box["x1"] >= midpoint:
                column_key = 1
            order = _source_order_value(source)
            return (
                page_key,
                column_key,
                int(box.get("y1") or 0),
                int(box.get("x1") or 0),
                order if order is not None else 10**9,
                str(record.record_id or ""),
            )

        layout_rows = sorted(rows, key=layout_order_key)
        for layout_index in range(len(layout_rows) - 1):
            parent = layout_rows[layout_index]
            child = layout_rows[layout_index + 1]
            candidate = self._score_continuation_pair(
                parent,
                child,
                index=layout_index,
                visual_cache=visual_cache,
                aux_ocr_cache=aux_ocr_cache,
                detector_cache=detector_cache,
                require_visual_prefilter=False,
                order_gap_override=1,
                order_basis="layout",
            )
            add_candidate(candidate)
        candidates.sort(key=lambda item: (-float(item.get("confidence") or 0.0), int(item.get("index") or 0)))
        max_candidates_value = max(1, int(max_candidates or 50))
        limited = candidates[:max_candidates_value]
        if len(rows) <= 80:
            profiles = [profile_at(index) for index in range(len(rows))]
            summary_mode = "exact"
        else:
            profiles = [profile_cache[index] for index in sorted(profile_cache)]
            summary_mode = "fast_partial"
        return {
            "schema_version": "pdf_factory_continuation_scan_v1",
            "summary": self._continuation_scan_summary(
                profiles,
                candidates=candidates,
                returned_candidates=limited,
                max_candidates=max_candidates_value,
                total_crops=len(rows),
                summary_mode=summary_mode,
            ),
            "candidates": limited,
        }

    def _classify_continuation_crop(
        self,
        record: StagingProblemRecord,
        *,
        visual_cache: dict[tuple[str, int, int], dict[str, Any]] | None = None,
        aux_ocr_cache: dict[tuple[str, int, int], dict[str, Any]] | None = None,
        detector_cache: dict[tuple[str, int, int, str], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        visual = {
            "available": False,
            "skipped": "visual_heuristic_disabled_for_boolean_continuity",
        }
        detector = _continuity_detector_features(record, detector_cache)
        detector_available = bool(detector.get("available"))
        detector_subbox_informative = bool(detector.get("subbox_detections_total"))
        aux_ocr = {
            "available": False,
            "skipped": "ocr_disabled_for_boolean_continuity",
        }
        aux_available = bool(aux_ocr.get("available"))
        detector_has_number = bool(detector.get("has_problem_number"))
        detector_has_options = bool(detector.get("has_answer_block"))
        detector_complete_options = detector_has_options
        visual_has_number = False
        visual_has_options = False
        visual_complete_options = False
        aux_has_number = bool(aux_ocr.get("starts_problem"))
        aux_has_options = bool(aux_ocr.get("has_options"))
        aux_complete_options = bool(aux_ocr.get("complete_options"))
        has_number = detector_has_number
        has_options = detector_has_options
        complete_options = detector_complete_options
        complete_problem = has_number and complete_options
        if complete_problem:
            role = "complete_problem"
        elif has_number and has_options:
            role = "ambiguous"
        elif has_number and not complete_options:
            role = "possible_parent"
        elif not has_number and has_options:
            role = "possible_continuation"
        else:
            role = "ambiguous"
        return {
            "record_id": record.record_id,
            "role": role,
            "has_number": has_number,
            "has_options": has_options,
            "complete_options": complete_options,
            "complete_problem": complete_problem,
            "visual_has_number": visual_has_number,
            "visual_has_options": visual_has_options,
            "visual_complete_options": visual_complete_options,
            "detector_has_number": detector_has_number,
            "detector_has_options": detector_has_options,
            "detector_complete_options": detector_complete_options,
            "detector_available": detector_available,
            "detector_subbox_informative": detector_subbox_informative,
            "aux_has_number": aux_has_number,
            "aux_has_options": aux_has_options,
            "aux_complete_options": aux_complete_options,
            "auxiliary_ocr_available": aux_available,
            "detector": detector,
            "visual": visual,
            "auxiliary_ocr": aux_ocr,
        }

    @staticmethod
    def _continuation_scan_summary(
        profiles: list[dict[str, Any]],
        *,
        candidates: list[dict[str, Any]],
        returned_candidates: list[dict[str, Any]] | None = None,
        max_candidates: int | None = None,
        total_crops: int | None = None,
        summary_mode: str = "exact",
    ) -> dict[str, Any]:
        returned = list(returned_candidates if returned_candidates is not None else candidates)
        roles: dict[str, int] = {}
        aux_available = 0
        detector_available = 0
        detector_subbox_informative = 0
        for profile in profiles:
            role = str(profile.get("role") or "ambiguous")
            roles[role] = roles.get(role, 0) + 1
            if profile.get("auxiliary_ocr_available"):
                aux_available += 1
            if profile.get("detector_available"):
                detector_available += 1
            if profile.get("detector_subbox_informative"):
                detector_subbox_informative += 1
        complete_discarded = roles.get("complete_problem", 0)
        total = int(total_crops if total_crops is not None else len(profiles))
        return {
            "schema_version": "pdf_factory_continuation_scan_summary_v1",
            "summary_mode": str(summary_mode or "exact"),
            "profiled_crops": len(profiles),
            "total_crops": total,
            "candidate_pool": max(0, total - complete_discarded),
            "complete_discarded": complete_discarded,
            "possible_parents": roles.get("possible_parent", 0),
            "possible_continuations": roles.get("possible_continuation", 0),
            "ambiguous": roles.get("ambiguous", 0),
            "auxiliary_ocr_available": aux_available,
            "auxiliary_ocr_missing": max(0, len(profiles) - aux_available),
            "detector_available": detector_available,
            "detector_missing": max(0, len(profiles) - detector_available),
            "detector_subbox_informative": detector_subbox_informative,
            "candidate_pairs": len(returned),
            "candidate_pairs_total": len(candidates),
            "candidate_pairs_returned": len(returned),
            "candidate_pairs_limited": len(candidates) > len(returned),
            "max_candidates": int(max_candidates or len(returned) or 0),
            "merge_recommended": sum(1 for item in returned if item.get("recommendation") == "merge"),
            "merge_recommended_total": sum(1 for item in candidates if item.get("recommendation") == "merge"),
            "review_recommended": sum(1 for item in returned if item.get("recommendation") != "merge"),
            "review_recommended_total": sum(1 for item in candidates if item.get("recommendation") != "merge"),
            "roles": roles,
        }

    def _score_continuation_pair(
        self,
        parent: StagingProblemRecord,
        child: StagingProblemRecord,
        *,
        index: int,
        visual_cache: dict[tuple[str, int, int], dict[str, Any]] | None = None,
        aux_ocr_cache: dict[tuple[str, int, int], dict[str, Any]] | None = None,
        detector_cache: dict[tuple[str, int, int, str], dict[str, Any]] | None = None,
        parent_profile: dict[str, Any] | None = None,
        child_profile: dict[str, Any] | None = None,
        require_visual_prefilter: bool = False,
        order_gap_override: int | None = None,
        order_basis: str = "source_order",
    ) -> dict[str, Any] | None:
        parent_source = dict(parent.source or {})
        child_source = dict(child.source or {})
        if _record_excluded_from_ocr_work(parent):
            return None
        if _record_excluded_from_ocr_work(child):
            return None
        if str(child.record_id or "") in set(str(item or "") for item in list(parent_source.get("continuation_record_ids") or [])):
            return None

        parent_page = parent_source.get("page_number") or parent_source.get("source_page_number")
        child_page = child_source.get("page_number") or child_source.get("source_page_number")
        try:
            page_gap = int(child_page) - int(parent_page)
        except Exception:
            page_gap = 0
        if page_gap < 0 or page_gap > 1:
            return None

        parent_box = _bbox_metrics(parent)
        child_box = _bbox_metrics(child)
        if not (_bbox_is_valid(parent_box) and _bbox_is_valid(child_box)):
            return None

        x_overlap = _x_overlap_ratio(parent_box, child_box)
        order_parent = _source_order_value(parent_source)
        order_child = _source_order_value(child_source)
        source_order_gap = (order_child - order_parent) if order_parent is not None and order_child is not None else None
        order_gap = int(order_gap_override) if order_gap_override is not None else source_order_gap
        same_page = page_gap == 0
        vertical_gap = child_box["y1"] - parent_box["y2"]
        child_after_parent = page_gap == 1 or child_box["y1"] >= parent_box["y1"] - 12
        height_ratio = child_box["height"] / max(1.0, parent_box["height"])
        max_close_gap = max(140.0, parent_box["height"] * 0.55)
        parent_page_size = _page_image_size(parent)
        child_page_size = _page_image_size(child)
        parent_bottom_ratio = (
            parent_box["y2"] / max(1.0, float(parent_page_size[1]))
            if parent_page_size
            else 0.0
        )
        child_top_ratio = (
            child_box["y1"] / max(1.0, float(child_page_size[1]))
            if child_page_size
            else 0.0
        )
        same_page_column_wrap_signal = (
            same_page
            and order_gap == 1
            and child_box["x1"] >= parent_box["x2"]
            and (not parent_page_size or parent_bottom_ratio >= 0.7)
            and (not child_page_size or child_top_ratio <= 0.42)
        )
        child_after_parent = child_after_parent or same_page_column_wrap_signal
        same_page_cut_signal = (
            same_page
            and child_after_parent
            and (
                same_page_column_wrap_signal
                or (
                    x_overlap >= 0.72
                    and -18.0 <= vertical_gap <= max_close_gap
                    and height_ratio <= 0.7
                )
            )
        )
        cross_page_cut_signal = (
            page_gap == 1
            and (not parent_page_size or parent_bottom_ratio >= 0.72)
            and (not child_page_size or child_top_ratio <= 0.38)
        )
        geometry_candidate = (
            same_page_cut_signal
            or cross_page_cut_signal
            or (
                same_page
                and child_after_parent
                and order_gap == 1
                and x_overlap >= 0.55
            )
        )
        if order_gap is not None and (order_gap < 1 or order_gap > 3):
            return None
        if require_visual_prefilter:
            return None
        parent_profile = parent_profile or self._classify_continuation_crop(
            parent,
            visual_cache=visual_cache,
            aux_ocr_cache=aux_ocr_cache,
            detector_cache=detector_cache,
        )
        child_profile = child_profile or self._classify_continuation_crop(
            child,
            visual_cache=visual_cache,
            aux_ocr_cache=aux_ocr_cache,
            detector_cache=detector_cache,
        )
        parent_options_visual = dict(parent_profile.get("visual") or {})
        child_options_visual = dict(child_profile.get("visual") or {})
        parent_detector = dict(parent_profile.get("detector") or {})
        child_detector = dict(child_profile.get("detector") or {})
        parent_aux_ocr = dict(parent_profile.get("auxiliary_ocr") or {})
        child_aux_ocr = dict(child_profile.get("auxiliary_ocr") or {})
        aux_ocr_available = False
        parent_option_score = 0.0
        child_option_score = 0.0
        parent_number_score = 0.0
        child_number_score = 0.0
        parent_has_number_signal = bool(parent_profile.get("has_number"))
        child_has_number_signal = bool(child_profile.get("has_number"))
        parent_complete_options_signal = bool(parent_profile.get("complete_options"))
        child_complete_options_signal = bool(child_profile.get("complete_options"))
        parent_has_any_options_signal = bool(parent_profile.get("has_options"))
        has_child_option_signal = bool(child_profile.get("has_options"))
        parent_complete_problem_signal = bool(parent_profile.get("complete_problem"))
        child_complete_problem_signal = bool(child_profile.get("complete_problem"))
        detector_available = bool(parent_detector.get("available")) and bool(child_detector.get("available"))
        parent_detector_has_number = bool(parent_detector.get("has_problem_number"))
        child_detector_has_number = bool(child_detector.get("has_problem_number"))
        parent_detector_has_options = bool(parent_detector.get("has_answer_block"))
        child_detector_has_options = bool(child_detector.get("has_answer_block"))
        parent_detector_subboxes = int(parent_detector.get("subbox_detections_total") or 0)
        child_detector_subboxes = int(child_detector.get("subbox_detections_total") or 0)
        detector_contradicts_split = detector_available and (
            parent_detector_has_options
            or child_detector_has_number
            or bool(parent_detector.get("complete_problem"))
            or bool(child_detector.get("complete_problem"))
        )
        if detector_contradicts_split:
            return None
        detector_pair_informative = detector_available and parent_detector_subboxes > 0 and child_detector_subboxes > 0
        if detector_pair_informative and not (parent_detector_has_number and child_detector_has_options):
            return None
        parent_problem_start_signal = parent_has_number_signal
        child_problem_start_signal = child_has_number_signal
        parent_option_fragment_signal = (
            not parent_problem_start_signal
            and parent_has_any_options_signal
        )
        child_independent_problem_signal = child_problem_start_signal and (
            has_child_option_signal
            or child_complete_options_signal
        )
        # Si el siguiente crop ya trae numeracion propia y alternativas, es otro
        # problema completo/iniciado; no es continuacion del anterior aunque el
        # crop anterior solo tenga numeracion.
        if child_independent_problem_signal:
            return None
        if parent_option_fragment_signal and child_problem_start_signal:
            return None
        if parent_complete_problem_signal or child_complete_problem_signal:
            return None
        child_no_number_signal = not child_has_number_signal
        parent_missing_options_signal = not parent_has_any_options_signal
        split_multiple_choice_signal = (
            parent_has_number_signal
            and parent_missing_options_signal
            and child_no_number_signal
            and has_child_option_signal
        )
        if not split_multiple_choice_signal:
            return None
        strict_boolean_neighbor_signal = bool(
            order_gap == 1
            and page_gap in {0, 1}
            and split_multiple_choice_signal
        )
        if not geometry_candidate and not strict_boolean_neighbor_signal:
            return None

        weights = CONTINUATION_BOOLEAN_WEIGHTS
        score = 0.0
        reasons: list[str] = []
        warnings: list[str] = []

        if order_gap is None:
            score += 0.08
            reasons.append("registros consecutivos en staging")
            warnings.append("sin orden numerico de segmentacion; se usa solo vecindad")
        elif order_gap == 1:
            score += weights["order_consecutive"]
            if str(order_basis or "") == "layout":
                reasons.append("orden de lectura geometrico consecutivo")
                if source_order_gap != 1:
                    warnings.append("orden manual de boxes no consecutivo; se uso lectura por columnas")
            else:
                reasons.append("orden de segmentacion consecutivo")
        elif 1 < order_gap <= 3:
            score += weights["order_near"]
            reasons.append("orden de segmentacion cercano")
            warnings.append("hay otros registros entre ambos crops")
        else:
            return None

        if x_overlap >= 0.72:
            score += weights["x_overlap_strong"]
            reasons.append("misma columna o bloque compatible")
        elif x_overlap >= 0.55:
            score += weights["x_overlap_compatible"]
            reasons.append("alineacion horizontal compatible")
        else:
            warnings.append("alineacion horizontal debil")

        if same_page:
            if not child_after_parent:
                if not strict_boolean_neighbor_signal:
                    return None
                warnings.append("geometria no lineal; se conserva por marcas consecutivas")
            score += weights["same_page_after"]
            if same_page_column_wrap_signal:
                reasons.append("segundo crop aparece despues por salto de columna en la misma pagina")
                if parent_bottom_ratio >= 0.7:
                    score += weights["parent_bottom"]
                    reasons.append("primer crop queda cerca del final de columna/pagina")
                if child_top_ratio <= 0.42:
                    score += weights["child_top"]
                    reasons.append("segundo crop aparece al inicio de la siguiente columna")
            else:
                reasons.append("segundo crop aparece despues en la misma pagina")
            if not same_page_column_wrap_signal and -18.0 <= vertical_gap <= max_close_gap:
                score += weights["vertical_close"]
                reasons.append("separacion vertical compatible con corte de problema")
            elif not same_page_column_wrap_signal and vertical_gap > max_close_gap:
                warnings.append("separacion vertical amplia; revisar visualmente")
            elif not same_page_column_wrap_signal:
                warnings.append("crops se solapan verticalmente; revisar visualmente")
            if height_ratio <= 0.9:
                score += weights["child_not_taller"]
                reasons.append("segundo crop no excede el alto del anterior")
        else:
            score += weights["cross_page"]
            reasons.append("salto directo a pagina siguiente")
            if parent_bottom_ratio >= 0.72:
                score += weights["parent_bottom"]
                reasons.append("primer crop queda cerca del final de pagina")
            elif parent_page_size:
                warnings.append("primer crop no esta cerca del final de pagina")
            if child_top_ratio <= 0.38:
                score += weights["child_top"]
                reasons.append("segundo crop aparece al inicio de pagina")
            elif child_page_size:
                warnings.append("segundo crop no esta cerca del inicio de pagina")

        if has_child_option_signal:
            score += weights["child_options_strong"]
            reasons.append("segundo crop tiene bloque de alternativas detectado")

        if child_no_number_signal:
            score += weights["child_no_leading_number"]
            reasons.append("segundo crop no tiene marca de numeracion")
        else:
            score += weights["child_has_leading_number_penalty"]
            warnings.append("segundo crop tiene marca de numeracion")

        if parent_has_number_signal:
            score += weights["parent_has_leading_number"]
            reasons.append("primer crop tiene marca de numeracion")

        if parent_missing_options_signal:
            score += weights["parent_missing_options"]
            reasons.append("primer crop no tiene marca de alternativas")

        if split_multiple_choice_signal:
            score += weights["split_multiple_choice_rule"]
            reasons.append("regla fuerte: padre numerado sin alternativas + continuacion sin numero con alternativas")

        detector_confirms_split = (
            detector_available
            and parent_detector_has_number
            and not parent_detector_has_options
            and not child_detector_has_number
            and child_detector_has_options
        )
        aux_confirms_split = False
        geometry_confirms_split = (
            parent_has_number_signal
            and parent_missing_options_signal
            and child_no_number_signal
            and has_child_option_signal
            and (
                same_page_cut_signal
                or cross_page_cut_signal
                or same_page_column_wrap_signal
                or (
                    same_page
                    and child_after_parent
                    and order_gap == 1
                    and x_overlap >= 0.55
                )
            )
        )
        if detector_confirms_split and split_multiple_choice_signal:
            score += 0.18
            reasons.append("detector v3 confirma: padre con numero sin alternativas + continuacion con alternativas sin numero")
        elif geometry_confirms_split and split_multiple_choice_signal:
            score += 0.08
            reasons.append("confirmacion geometrica: padre numerado sin alternativas + continuacion sin numero con alternativas")

        confidence = max(0.0, min(0.99, score))
        if confidence < 0.2:
            return None
        recommendation = "merge" if (
            split_multiple_choice_signal
            and confidence >= 0.78
            and (detector_confirms_split or aux_confirms_split or geometry_confirms_split)
        ) else "review"
        return {
            "schema_version": "pdf_factory_continuation_candidate_v1",
            "index": index,
            "parent_record_id": parent.record_id,
            "continuation_record_id": child.record_id,
            "confidence": round(confidence, 3),
            "recommendation": recommendation,
            "reasons": reasons,
            "warnings": warnings,
            "features": {
                "parent_page": parent_page,
                "continuation_page": child_page,
                "page_gap": page_gap,
                "order_gap": order_gap,
                "source_order_gap": source_order_gap,
                "order_basis": str(order_basis or "source_order"),
                "x_overlap": round(x_overlap, 3),
                "vertical_gap": round(vertical_gap, 3),
                "height_ratio": round(height_ratio, 3),
                "parent_bottom_ratio": round(parent_bottom_ratio, 3),
                "continuation_top_ratio": round(child_top_ratio, 3),
                "parent_option_block_score": parent_options_visual,
                "continuation_option_block_score": child_options_visual,
                "parent_auxiliary_ocr": parent_aux_ocr,
                "continuation_auxiliary_ocr": child_aux_ocr,
                "parent_detector": parent_detector,
                "continuation_detector": child_detector,
                "detector_available": detector_available,
                "detector_pair_informative": detector_pair_informative,
                "detector_contradicts_split": detector_contradicts_split,
                "auxiliary_ocr_available": aux_ocr_available,
                "parent_complete_problem_signal": parent_complete_problem_signal,
                "continuation_complete_problem_signal": child_complete_problem_signal,
                "parent_leading_number_score": round(parent_number_score, 3),
                "continuation_leading_number_score": round(child_number_score, 3),
                "split_multiple_choice_signal": split_multiple_choice_signal,
                "detector_confirms_split": detector_confirms_split,
                "aux_confirms_split": aux_confirms_split,
                "visual_confirms_split": False,
                "geometry_confirms_split": geometry_confirms_split,
                "continuation_weights": dict(weights),
                "scoring_mode": "detector_boolean_no_visual_no_ocr",
            },
        }

    @staticmethod
    def _write_vertical_crop_merge(image_paths: list[Path], output_path: Path) -> dict[str, Any]:
        from PIL import Image

        opened = [Image.open(path).convert("RGB") for path in image_paths]
        try:
            padding = 18
            width = max(image.width for image in opened)
            height = sum(image.height for image in opened) + padding * max(0, len(opened) - 1)
            canvas = Image.new("RGB", (width, height), "white")
            y = 0
            parts: list[dict[str, Any]] = []
            for index, image in enumerate(opened):
                x = max(0, (width - image.width) // 2)
                canvas.paste(image, (x, y))
                parts.append(
                    {
                        "index": index,
                        "source_path": str(image_paths[index]),
                        "x": x,
                        "y": y,
                        "width": image.width,
                        "height": image.height,
                    }
                )
                y += image.height + padding
            output_path.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(output_path)
            return {
                "merged_crop_path": str(output_path),
                "parts_total": len(parts),
                "width": width,
                "height": height,
                "parts": parts,
            }
        finally:
            for image in opened:
                try:
                    image.close()
                except Exception:
                    pass

    def run_ocr_and_segmentation(
        self,
        *,
        provider: str = "hf",
        curso: str = "SIN_CURSO",
        tema: str = "SIN_TEMA",
        start_n: int = 1,
        limit: int | None = None,
        ocr_model: str = "",
        figure_model: str = "",
        force_figure_model: bool = True,
        record_id: str = "",
        record_ids: list[str] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        run_segmentation: bool = True,
        run_ocr: bool = True,
        manage_endpoint: bool = True,
    ) -> list[StagingProblemRecord]:
        all_records = self.staging.load_records()
        existing_by_identity = self.staging.identity_map_for_records(all_records)
        records = all_records
        selected_record_ids = [str(item or "").strip() for item in list(record_ids or []) if str(item or "").strip()]
        if selected_record_ids:
            by_id = {str(record.record_id or ""): record for record in records}
            missing = [item for item in selected_record_ids if item not in by_id]
            if missing:
                raise KeyError(missing[0])
            records = [by_id[item] for item in selected_record_ids]
        selected_record_id = str(record_id or "").strip()
        if selected_record_id and not selected_record_ids:
            records = [record for record in records if str(record.record_id or "") == selected_record_id]
            if not records:
                raise KeyError(selected_record_id)
        if not selected_record_ids and not selected_record_id:
            records = [record for record in records if not _record_excluded_from_ocr_work(record)]
        if limit is not None:
            records = records[: max(0, int(limit))]
        if not records:
            return []
        if not run_segmentation and not run_ocr:
            return records
        if run_segmentation and run_ocr:
            selected_ids = [str(record.record_id or "") for record in records if str(record.record_id or "")]
            if progress_callback is not None:
                try:
                    progress_callback(
                        {
                            "event": "phase_start",
                            "phase": "segmentation",
                            "message": f"Segmentando graficos localmente 0 de {len(selected_ids)}",
                            "total": len(selected_ids),
                        }
                    )
                except Exception:
                    pass
            self.run_ocr_and_segmentation(
                provider=provider,
                curso=curso,
                tema=tema,
                start_n=start_n,
                limit=None,
                ocr_model=ocr_model,
                figure_model=figure_model,
                force_figure_model=force_figure_model,
                record_ids=selected_ids,
                progress_callback=progress_callback,
                run_segmentation=True,
                run_ocr=False,
                manage_endpoint=manage_endpoint,
            )
            if progress_callback is not None:
                try:
                    progress_callback(
                        {
                            "event": "phase_start",
                            "phase": "ocr",
                            "message": f"Ejecutando OCR remoto 0 de {len(selected_ids)}",
                            "total": len(selected_ids),
                        }
                    )
                except Exception:
                    pass
            return self.run_ocr_and_segmentation(
                provider=provider,
                curso=curso,
                tema=tema,
                start_n=start_n,
                limit=None,
                ocr_model=ocr_model,
                figure_model=figure_model,
                force_figure_model=force_figure_model,
                record_ids=selected_ids,
                progress_callback=progress_callback,
                run_segmentation=False,
                run_ocr=True,
                manage_endpoint=manage_endpoint,
            )

        if run_ocr:
            from modulos.modulo0_transcriptor.scan_pipeline.pipeline import ScanPipeline
            from modulos.modulo0_transcriptor.scan_pipeline.extractor import TRAINED_OCR_VISION_MODEL
        else:
            ScanPipeline = None  # type: ignore[assignment]
            TRAINED_OCR_VISION_MODEL = ""
        if run_segmentation:
            from modulos.modulo0_transcriptor.segmentador_v2 import SegmentadorProblemasV2
        else:
            SegmentadorProblemasV2 = None  # type: ignore[assignment]

        active_ocr_model = str(ocr_model or self.models.ocr or "").strip()
        active_figure_model = self._server_model_reference("figure_segmenter", override=figure_model).strip()
        endpoint_state: dict[str, Any] = {}
        pipeline = None
        if run_ocr:
            resolved_ocr_model = active_ocr_model or str(os.getenv("HF_MODEL", TRAINED_OCR_VISION_MODEL) or TRAINED_OCR_VISION_MODEL).strip()
            self._validate_ocr_runtime(provider=provider, model=resolved_ocr_model, trained_model=TRAINED_OCR_VISION_MODEL)
            if manage_endpoint:
                endpoint_state = self._prepare_trained_ocr_endpoint(
                    provider=provider,
                    model=resolved_ocr_model,
                    trained_model=TRAINED_OCR_VISION_MODEL,
                )
            pipeline = ScanPipeline(
                provider=provider,
                model=active_ocr_model,
                debug_dir=str(self.staging.root / "ocr_debug"),
                strict_json=False,
            )
        segmenter = None
        if run_segmentation:
            segmenter = SegmentadorProblemasV2(
                self.staging.root / "segments",
                model_path=active_figure_model,
                force_model_default=bool(force_figure_model),
            )
        processed: list[StagingProblemRecord] = []
        next_n = max(1, int(start_n))
        phase_error_prefixes: list[str] = []
        if run_segmentation:
            phase_error_prefixes.append("segmentacion_grafica:")
        if run_ocr:
            phase_error_prefixes.extend(["ocr_crudo:", "ocr_estructura:"])
        phase_name = "OCR" if run_ocr else "segmentacion"
        for index, record in enumerate(records):
            crop_path = Path(record.crop_path)
            effective_crop_path = _effective_ocr_image_path(record)
            source = dict(record.source or {})
            if _record_excluded_from_ocr_work(record):
                replacement = source.get("replaced_by_record_id") or source.get("merged_into_record_id")
                record.set_step(
                    PipelineStep.OCR,
                    StageStatus.READY,
                    f"reemplazado para OCR en {replacement}",
                )
                record.set_step(
                    PipelineStep.SEGMENTATION,
                    StageStatus.READY,
                    f"reemplazado para segmentacion en {replacement}",
                )
                record.sync_status_from_steps()
                record.touch()
                record = self.staging.upsert_record(
                    record,
                    rewrite_manifest=False,
                    existing_by_identity=existing_by_identity,
                )
                processed.append(record)
                continue
            if phase_error_prefixes:
                record.errors = [
                    str(item)
                    for item in list(record.errors or [])
                    if not any(str(item).startswith(prefix) for prefix in phase_error_prefixes)
                ]
            if progress_callback is not None:
                try:
                    progress_callback(
                        {
                            "event": "record_start",
                            "phase": "ocr" if run_ocr else "segmentation",
                            "record_id": record.record_id,
                            "index": index + 1,
                            "total": len(records),
                            "message": f"{phase_name} {index + 1} de {len(records)}",
                        }
                    )
                except Exception:
                    pass
            if not crop_path.exists():
                record.status = StageStatus.ERROR
                record.errors.append(f"crop_missing:{crop_path}")
                record.set_step(PipelineStep.CROPS, StageStatus.ERROR, "crop no encontrado", crop_path=str(crop_path))
                if run_ocr:
                    record.set_step(PipelineStep.OCR, StageStatus.PENDING, "pendiente hasta recuperar crop")
                if run_segmentation:
                    record.set_step(PipelineStep.SEGMENTATION, StageStatus.PENDING, "pendiente hasta recuperar crop")
                record.set_step(PipelineStep.NORMALIZATION, StageStatus.PENDING, "pendiente hasta recuperar crop")
                record.sync_status_from_steps()
                record = self.staging.upsert_record(
                    record,
                    rewrite_manifest=False,
                    existing_by_identity=existing_by_identity,
                )
                processed.append(record)
                continue
            if not effective_crop_path.exists():
                record.status = StageStatus.ERROR
                record.errors.append(f"ocr_input_missing:{effective_crop_path}")
                record.set_step(
                    PipelineStep.OCR,
                    StageStatus.ERROR,
                    "imagen efectiva para OCR no encontrada",
                    crop_path=str(crop_path),
                    ocr_input_crop_path=str(effective_crop_path),
                )
                if run_segmentation:
                    record.set_step(
                        PipelineStep.SEGMENTATION,
                        StageStatus.ERROR,
                        "imagen efectiva para segmentacion no encontrada",
                        ocr_input_crop_path=str(effective_crop_path),
                    )
                record.set_step(PipelineStep.NORMALIZATION, StageStatus.PENDING, "pendiente hasta recuperar imagen fusionada")
                record.sync_status_from_steps()
                record = self.staging.upsert_record(
                    record,
                    rewrite_manifest=False,
                    existing_by_identity=existing_by_identity,
                )
                processed.append(record)
                continue
            record.status = StageStatus.PROCESSING
            record.set_step(
                PipelineStep.CROPS,
                StageStatus.READY,
                "crop disponible",
                crop_path=str(crop_path),
                ocr_input_crop_path=str(effective_crop_path),
            )
            if run_segmentation:
                record.set_step(PipelineStep.SEGMENTATION, StageStatus.PROCESSING, "segmentando graficos internos")
                try:
                    if segmenter is None:
                        raise RuntimeError("Segmentador no inicializado.")
                    segments = segmenter.segmentar(effective_crop_path, force_model=bool(force_figure_model))
                    detector_payload = dict(segmenter.last_detector_payload or {})
                    try:
                        figure_max_conf = float(detector_payload.get("max_conf", 0.0) or 0.0)
                    except Exception:
                        figure_max_conf = 0.0
                    try:
                        figure_avg_conf = float(detector_payload.get("avg_conf", 0.0) or 0.0)
                    except Exception:
                        figure_avg_conf = 0.0
                    record.confidence["figure_segmenter_max"] = max(0.0, min(1.0, figure_max_conf))
                    record.confidence["figure_segmenter_avg"] = max(0.0, min(1.0, figure_avg_conf))
                    record.models = {
                        **record.models,
                        **self._model_snapshot(
                            provider=provider,
                            confidence_overrides={"figure_segmenter": record.confidence["figure_segmenter_max"]},
                        ),
                    }
                    record.figure_segmentation = {
                        "status": StageStatus.NEEDS_REVIEW if segments else StageStatus.READY,
                        "segments_total": len(segments),
                        "segments": [
                            {
                                "idx": int(seg.idx),
                                "bbox_px": [int(v) for v in seg.bbox],
                                "image_path": str(seg.image_path),
                                "source_image_path": str(effective_crop_path),
                            }
                            for seg in segments
                        ],
                        "detector": detector_payload,
                    }
                    record.set_step(
                        PipelineStep.SEGMENTATION,
                        StageStatus.NEEDS_REVIEW if segments else StageStatus.READY,
                        "segmentos detectados para revisar" if segments else "sin graficos internos detectados",
                        segments_total=len(segments),
                    )
                except Exception as exc:
                    message = str(exc or "")
                    record.errors.append(f"segmentacion_grafica:{message}")
                    record.set_step(PipelineStep.SEGMENTATION, StageStatus.ERROR, f"segmentacion grafica: {message}")
            if run_ocr:
                record.set_step(PipelineStep.OCR, StageStatus.PROCESSING, "ejecutando OCR crudo remoto")
                try:
                    if endpoint_state:
                        record.trace = {
                            **dict(record.trace or {}),
                            "hf_ocr_endpoint_before_run": dict(endpoint_state),
                        }
                    if pipeline is None:
                        raise RuntimeError("Pipeline OCR no inicializado.")
                    _initial_items, raw_output = self._extract_with_cold_start_retry(
                        pipeline,
                        image_path=effective_crop_path,
                        curso=curso,
                        tema=tema,
                        start_n=next_n,
                        progress_callback=progress_callback,
                        gate_remote=(
                            str(provider or "").strip().lower() == "hf"
                            and str(resolved_ocr_model or "").strip() == str(TRAINED_OCR_VISION_MODEL or "").strip()
                        ),
                        gate_job_id=str(record.record_id or effective_crop_path.stem),
                        gate_label=effective_crop_path.name,
                    )
                    record.raw_ocr = raw_output
                    record.structured_ocr = {}
                    raw_has_text = bool(str(raw_output or "").strip())
                    record.set_step(
                        PipelineStep.OCR,
                        StageStatus.READY if raw_has_text else StageStatus.NEEDS_REVIEW,
                        "OCR crudo guardado" if raw_has_text else "OCR crudo vacio; requiere revision",
                        characters=len(str(raw_output or "")),
                    )
                    if raw_has_text:
                        record.confidence["ocr_raw_available"] = 1.0
                    record.models = {
                        **record.models,
                        **self._model_snapshot(provider=provider, confidence_overrides={"ocr": float(record.confidence.get("ocr_raw_available") or 0.0)} if raw_has_text else None),
                    }
                    record.normalized = {}
                    record.set_step(
                        PipelineStep.NORMALIZATION,
                        StageStatus.PENDING,
                        "pendiente de normalizacion desde OCR crudo revisado",
                        source="normalizer_pending_training",
                    )
                    record.set_step(
                        PipelineStep.REVIEW,
                        StageStatus.NEEDS_REVIEW,
                        "OCR crudo listo para revision humana; normalizacion IA pendiente",
                    )
                    self._mark_record_downstream_active(record, reason="ocr_segmentation_reran_after_source_change")
                    self._write_raw_artifacts(record)
                    next_n += 1
                except Exception as exc:
                    message = str(exc or "")
                    if str(record.raw_ocr or "").strip():
                        record.set_step(
                            PipelineStep.OCR,
                            StageStatus.READY,
                            "OCR crudo guardado; fallo posterior no bloquea revision",
                            characters=len(str(record.raw_ocr or "")),
                        )
                        record.set_step(
                            PipelineStep.NORMALIZATION,
                            StageStatus.PENDING,
                            "pendiente de normalizacion desde OCR crudo revisado",
                        )
                        try:
                            self._write_raw_artifacts(record)
                        except Exception:
                            pass
                    else:
                        record.errors.append(f"ocr_crudo:{message}")
                        record.set_step(PipelineStep.OCR, StageStatus.ERROR, f"OCR crudo remoto: {message}")
                        record.set_step(PipelineStep.NORMALIZATION, StageStatus.ERROR, "normalizacion no ejecutada por error de OCR crudo")
            confidence_overrides = {}
            if "figure_segmenter_max" in record.confidence:
                confidence_overrides["figure_segmenter"] = float(record.confidence.get("figure_segmenter_max") or 0.0)
            if "ocr_raw_available" in record.confidence:
                confidence_overrides["ocr"] = float(record.confidence.get("ocr_raw_available") or 0.0)
            record.models = {
                **record.models,
                **self._model_snapshot(provider=provider, confidence_overrides=confidence_overrides or None),
            }
            record.sync_status_from_steps()
            record.touch()
            record = self.staging.upsert_record(
                record,
                rewrite_manifest=False,
                existing_by_identity=existing_by_identity,
            )
            processed.append(record)
        self.staging.rewrite_manifest()
        return processed

    def _prepare_trained_ocr_endpoint(self, *, provider: str, model: str, trained_model: str) -> dict[str, Any]:
        if str(provider or "").strip().lower() != "hf":
            return {}
        if str(model or "").strip() != str(trained_model or "").strip():
            return {}
        try:
            from .hf_endpoint_manager import HfEndpointManager
        except Exception:
            return {}
        try:
            timeout_s = int(str(os.getenv("HF_ENDPOINT_START_TIMEOUT", "420") or "420").strip())
        except Exception:
            timeout_s = 420
        try:
            poll_s = int(str(os.getenv("HF_ENDPOINT_POLL_SECONDS", "8") or "8").strip())
        except Exception:
            poll_s = 8
        manager = HfEndpointManager()
        return manager.ensure_ready(timeout_s=max(1, min(1800, timeout_s)), poll_s=max(1, min(120, poll_s)))

    def _extract_with_cold_start_retry(
        self,
        pipeline: Any,
        *,
        image_path: Path,
        curso: str,
        tema: str,
        start_n: int,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        gate_remote: bool = False,
        gate_job_id: str = "",
        gate_label: str = "",
    ) -> Any:
        from .hf_endpoint_manager import cold_start_sleep_seconds, is_cold_start_runtime_error, ocr_endpoint_request_slot

        try:
            retries = int(str(os.getenv("HF_ENDPOINT_COLD_START_RETRIES", "8") or "8").strip())
        except Exception:
            retries = 8
        retries = max(0, min(12, retries))
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                extractor = pipeline.extractor
                extract_raw = getattr(extractor, "extract_raw_from_image", None)
                extract_fn = extract_raw if callable(extract_raw) else extractor.extract_from_image
                if gate_remote:
                    with ocr_endpoint_request_slot(
                        kind="trained_ocr_request",
                        job_id=str(gate_job_id or image_path.stem),
                        label=str(gate_label or image_path.name),
                        status_callback=progress_callback,
                    ):
                        return extract_fn(
                            image_path=image_path,
                            curso=curso,
                            tema=tema,
                            start_n=start_n,
                        )
                return extract_fn(
                    image_path=image_path,
                    curso=curso,
                    tema=tema,
                    start_n=start_n,
                )
            except Exception as exc:
                last_exc = exc
                if attempt >= retries or not is_cold_start_runtime_error(exc):
                    raise
                delay_s = cold_start_sleep_seconds(attempt)
                if progress_callback is not None:
                    try:
                        progress_callback(
                            {
                                "event": "ocr_cold_start_retry",
                                "attempt": attempt + 1,
                                "retries": retries,
                                "delay_s": delay_s,
                                "message": (
                                    "despertando endpoint OCR; todavia no acepta la solicitud "
                                    f"(espera {attempt + 1}/{retries}, reintento en {int(delay_s)}s)."
                                ),
                                "error": str(exc or ""),
                            }
                        )
                    except Exception:
                        pass
                time.sleep(delay_s)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("No se pudo ejecutar OCR.")

    def _validate_ocr_runtime(self, *, provider: str, model: str, trained_model: str) -> None:
        runtime = str(provider or "hf").strip().lower()
        if runtime == "ocr":
            return
        try:
            from importlib.util import find_spec

            has_openai = find_spec("openai") is not None
        except Exception:
            has_openai = False
        if not has_openai:
            raise RuntimeError("Falta instalar la libreria openai para ejecutar OCR remoto compatible.")
        if runtime == "openai":
            if not str(os.getenv("OPENAI_API_KEY", "") or "").strip():
                raise RuntimeError("Falta OPENAI_API_KEY para ejecutar OCR con OpenAI.")
            return
        token = str(os.getenv("HF_TOKEN", "") or os.getenv("HUGGINGFACEHUB_API_TOKEN", "") or "").strip()
        if not token:
            raise RuntimeError("Falta HF_TOKEN para ejecutar el OCR entrenado.")
        if str(model or "").strip() == str(trained_model or "").strip():
            endpoint = str(os.getenv("HF_TRAINED_OCR_BASE_URL", "") or "").strip()
            if not endpoint:
                raise RuntimeError(
                    "Falta HF_TRAINED_OCR_BASE_URL. Configura la URL /v1 del endpoint dedicado "
                    "del modelo OCR entrenado o usa temporalmente HF_BASE_URL con esa misma URL."
                )
            if "router.huggingface.co" in endpoint.lower():
                raise RuntimeError(
                    "HF_TRAINED_OCR_BASE_URL esta apuntando al router de Hugging Face Inference Providers. "
                    "Para el modelo OCR entrenado usa la URL /v1 del endpoint dedicado "
                    "(por ejemplo https://...endpoints.huggingface.cloud/v1) o genera un HF_TOKEN "
                    "fine-grained con permiso 'Make calls to Inference Providers' si decides usar el router."
                )

    def normalize_existing_ocr(
        self,
        *,
        record_id: str = "",
        record_ids: list[str] | None = None,
    ) -> list[StagingProblemRecord]:
        self.staging.repair_detected_continuation_links()
        all_records = self.staging.load_records()
        records = list(all_records)
        selected_record_ids = [str(item or "").strip() for item in list(record_ids or []) if str(item or "").strip()]
        if selected_record_ids:
            by_id = {str(record.record_id or ""): record for record in all_records}
            missing = [item for item in selected_record_ids if item not in by_id]
            if missing:
                raise KeyError(missing[0])
            records = [by_id[item] for item in selected_record_ids]
        selected_record_id = str(record_id or "").strip()
        if selected_record_id and not selected_record_ids:
            records = [record for record in records if str(record.record_id or "") == selected_record_id]
            if not records:
                raise KeyError(selected_record_id)
        single_record_requested = bool(selected_record_id and not selected_record_ids) or len(selected_record_ids) == 1
        out: list[StagingProblemRecord] = []
        for record in records:
            invalidated_reason = self._invalidated_downstream_reason(record)
            if invalidated_reason:
                message = f"Regenera staging antes de preparar revision: {invalidated_reason}."
                if single_record_requested:
                    raise ValueError(message)
                record.errors = [
                    str(item)
                    for item in list(record.errors or [])
                    if not str(item).startswith("normalizacion:")
                ]
                record.errors.append(f"normalizacion:{message}")
                record.set_step(PipelineStep.NORMALIZATION, StageStatus.ERROR, f"normalizacion: {message}")
                record.sync_status_from_steps()
                record.touch()
                out.append(record)
                continue
            record.errors = [
                str(item)
                for item in list(record.errors or [])
                if not str(item).startswith("normalizacion:")
            ]
            try:
                record.models = {**record.models, **self._model_snapshot()}
                if str(record.raw_ocr or "").strip():
                    record.set_step(PipelineStep.OCR, StageStatus.READY, "OCR crudo disponible para preparar revision")
                    continuations = self._continuation_records_for_parent(record, all_records)
                    record.normalized = self._draft_normalized_from_raw_ocr(record, continuations=continuations)
                else:
                    record.normalized = {}
                if record.normalized:
                    record.set_step(PipelineStep.NORMALIZATION, StageStatus.NEEDS_REVIEW, "borrador desde OCR crudo pendiente de formato final")
                    record.set_step(PipelineStep.REVIEW, StageStatus.NEEDS_REVIEW, "pendiente de revision humana")
                else:
                    record.set_step(PipelineStep.NORMALIZATION, StageStatus.PENDING, "sin OCR crudo para preparar revision")
                self._write_raw_artifacts(record)
                record.sync_status_from_steps()
            except Exception as exc:
                message = str(exc or "")
                record.errors.append(f"normalizacion:{message}")
                record.set_step(PipelineStep.NORMALIZATION, StageStatus.ERROR, f"normalizacion: {message}")
                record.sync_status_from_steps()
            record.touch()
            out.append(record)
        self.staging.upsert_many(out)
        return out

    def normalize_with_ai(self, record_id: str) -> StagingProblemRecord:
        record_id = str(record_id or "").strip()
        if not record_id:
            raise ValueError("record_id requerido para normalizar con IA.")
        record = self.staging.get_record(record_id)
        if record is None:
            raise KeyError(record_id)

        all_records = self.staging.load_records()
        current = next((row for row in all_records if str(row.record_id or "") == record_id), None)
        if current is not None:
            record = current
        continuation_ids = self._continuation_record_ids(all_records)
        if self._is_continuation_record(record) or record_id in continuation_ids or str(record.crop_id or "").strip() in continuation_ids:
            raise ValueError("Este registro es una continuacion fusionada; normaliza el problema principal.")
        reason = self._invalidated_downstream_reason(record)
        if reason:
            raise ValueError(f"Regenera staging antes de normalizar este registro: {reason}.")
        if not str(record.raw_ocr or "").strip():
            raise ValueError("Este registro no tiene OCR crudo para normalizar con IA.")
        continuations = self._continuation_records_for_parent(record, all_records)
        base_normalized = dict(record.normalized or {})
        if not base_normalized:
            base_normalized = self._draft_normalized_from_raw_ocr(record, continuations=continuations)
        input_payload = normalizer_input_from_record(self.context, record, continuations=continuations)
        client = HfOcrNormalizerClient(model=str(self.models.normalizer or ""))
        prediction = client.generate_final_latex(input_payload)
        final_latex = repair_final_latex_with_normalizer_input(
            str(prediction.get("final_latex") or "").strip(),
            input_payload,
        )
        if not final_latex:
            raise RuntimeError("El normalizador IA no devolvio formato final.")

        record.models = {**record.models, **self._model_snapshot()}
        metadata = dict(base_normalized.get("metadata_tecnica") or {})
        metadata.update(
            {
                "normalizer_input_schema": str(input_payload.get("schema_version") or ""),
                "normalizer_model": str(prediction.get("model") or self.models.normalizer or ""),
                "normalizer_base_url": str(prediction.get("base_url") or ""),
                "ai_generated_requires_human_review": True,
            }
        )
        record.normalized = {
            **base_normalized,
            "schema_version": "normalized_problem_staging_v1",
            "normalizer": str(prediction.get("model") or self.models.normalizer or ""),
            "status": StageStatus.NEEDS_REVIEW,
            "updated_at": utc_now_text(),
            "source_record_id": record.record_id,
            "latex_rendered_item": final_latex,
            "metadata_tecnica": metadata,
        }
        record.errors = [
            str(item)
            for item in list(record.errors or [])
            if not str(item).startswith("normalizacion:") and not str(item).startswith("normalizacion_ia:")
        ]
        record.trace = {
            **dict(record.trace or {}),
            "last_ai_normalizer": {
                "updated_at": utc_now_text(),
                "model": str(prediction.get("model") or self.models.normalizer or ""),
                "source": "hf_ocr_normalizer",
                "requires_human_review": True,
                "input_schema": str(input_payload.get("schema_version") or ""),
            },
        }
        record.set_step(PipelineStep.OCR, StageStatus.READY, "OCR crudo disponible para normalizador IA")
        record.set_step(
            PipelineStep.NORMALIZATION,
            StageStatus.NEEDS_REVIEW,
            "borrador IA del formato final pendiente de revision humana",
            source="hf_ocr_normalizer",
        )
        record.set_step(PipelineStep.REVIEW, StageStatus.NEEDS_REVIEW, "formato final IA pendiente de revision humana")
        self._write_raw_artifacts(record)
        record.sync_status_from_steps()
        record.touch()
        self.staging.upsert_record(record)
        return record

    def update_raw_ocr(self, record_id: str, raw_ocr: str, *, force_review: bool = False) -> StagingProblemRecord:
        record = self.staging.get_record(record_id)
        if record is None:
            raise KeyError(record_id)
        invalidated_reason = self._invalidated_downstream_reason(record)
        if invalidated_reason:
            raise ValueError(f"Regenera staging antes de editar OCR crudo: {invalidated_reason}.")
        previous_raw_ocr = str(record.raw_ocr or "")
        next_raw_ocr = str(raw_ocr or "")
        same_text = _canonical_human_review_text(previous_raw_ocr) == _canonical_human_review_text(next_raw_ocr)
        previous_review = dict(dict(record.trace or {}).get("last_raw_ocr_review") or {})
        if same_text and previous_review:
            self._write_raw_artifacts(record)
            record.touch()
            self.staging.upsert_record(record)
            return record
        if same_text and not force_review:
            self._write_raw_artifacts(record)
            record.touch()
            self.staging.upsert_record(record)
            return record
        record.raw_ocr = next_raw_ocr
        record.structured_ocr = {}
        record.errors = []
        if not same_text:
            self._reset_normalization_and_review(record, reason="raw_ocr_changed")
        if record.raw_ocr.strip():
            ocr_status = StageStatus.READY
            ocr_detail = "OCR crudo revisado" if not same_text else "OCR crudo aceptado por revision humana"
        else:
            ocr_status = StageStatus.PENDING
            ocr_detail = "OCR crudo vacio; pendiente"
        review_source = "human_raw_ocr_batch_acceptance" if same_text and force_review else "human_raw_ocr_editor"
        record.set_step(
            PipelineStep.OCR,
            ocr_status,
            ocr_detail,
            source=review_source,
            characters=len(record.raw_ocr),
        )
        record.trace = {
            **dict(record.trace or {}),
            "last_raw_ocr_review": {
                "updated_at": utc_now_text(),
                "source": review_source,
                "characters": len(record.raw_ocr),
                "structured_items_total": int((record.structured_ocr or {}).get("items_total") or 0),
                "accepted_without_text_change": bool(same_text and force_review),
            },
        }
        self._mark_record_downstream_active(record, reason="raw_ocr_reviewed_after_source_change")
        try:
            training = persist_raw_ocr_correction(
                self.context,
                record,
                corrected_text=record.raw_ocr,
                previous_text=previous_raw_ocr,
            )
            record.artifacts = {
                **dict(record.artifacts or {}),
                "ocr_training_bank_record": str(training.get("record_path") or ""),
                "ocr_training_bank_manifest": str(training.get("manifest_path") or ""),
                "ocr_training_records_corrected": int(training.get("records_corrected") or 0),
                "ocr_training_revision_count": int(training.get("revision_count") or 0),
            }
        except Exception as exc:
            record.artifacts = {
                **dict(record.artifacts or {}),
                "ocr_training_bank_error": str(exc),
            }
        self._write_raw_artifacts(record)
        record.sync_status_from_steps()
        record.touch()
        self.staging.upsert_record(record)
        return record

    def update_figure_segments(self, record_id: str, boxes: list[Any]) -> StagingProblemRecord:
        from modulos.modulo0_transcriptor.segmentador_v2 import SegmentadorProblemasV2

        record = self.staging.get_record(record_id)
        if record is None:
            raise KeyError(record_id)
        invalidated_reason = self._invalidated_downstream_reason(record)
        if invalidated_reason:
            raise ValueError(f"Regenera staging antes de guardar segmentos graficos: {invalidated_reason}.")
        crop_path = _effective_ocr_image_path(record)
        if not crop_path.exists():
            raise FileNotFoundError(f"No se encontro el crop efectivo: {crop_path}")
        clean_boxes = self._coerce_boxes(boxes)
        detector_payload = dict((record.figure_segmentation or {}).get("detector") or {})
        detector_payload.setdefault("predicted_boxes", detector_payload.get("predicted_boxes") or [])
        segmenter = SegmentadorProblemasV2(
            self.staging.root / "segments",
            model_path=self._server_model_reference("figure_segmenter"),
            force_model_default=False,
        )
        segments = segmenter.save_reviewed_segments(crop_path, clean_boxes, detector_payload=detector_payload)
        detector_payload = dict(segmenter.last_detector_payload or detector_payload or {})
        if not detector_payload:
            detector_payload = {
                "detector_source": "human_reviewed_segments",
                "review_status": "corrected",
                "final_boxes": [{"bbox_px": [int(v) for v in box[:4]], "conf": 1.0} for box in clean_boxes],
            }
        figure_review_status = str(detector_payload.get("review_status") or "reviewed").strip().lower() or "reviewed"
        record.figure_segmentation = {
            "status": StageStatus.READY,
            "segments_total": len(segments),
            "segments": [
                {
                    "idx": int(seg.idx),
                    "bbox_px": [int(v) for v in seg.bbox],
                    "image_path": str(seg.image_path),
                    "source_image_path": str(crop_path),
                    "reviewed": True,
                }
                for seg in segments
            ],
            "detector": detector_payload,
            "review": {
                "review_status": figure_review_status,
                "updated_at": utc_now_text(),
                "source": "human_canvas_editor",
                "boxes_total": len(segments),
            },
        }
        record.models = {
            **record.models,
            **self._model_snapshot(confidence_overrides={"figure_segmenter": 1.0}),
        }
        record.confidence["figure_segmenter_reviewed"] = 1.0
        record.errors = [
            str(item)
            for item in list(record.errors or [])
            if not str(item).startswith("segmentacion_grafica:")
        ]
        record.set_step(
            PipelineStep.SEGMENTATION,
            StageStatus.READY,
            "segmentos graficos revisados por humano",
            segments_total=len(segments),
        )
        self._reset_normalization_and_review(record, reason="figure_segments_changed")
        record.trace = {
            **dict(record.trace or {}),
            "last_figure_segment_review": {
                "updated_at": utc_now_text(),
                "source": "human_canvas_editor",
                "boxes": [[int(v) for v in box[:4]] for box in clean_boxes],
            },
        }
        try:
            training = persist_figure_segment_correction(
                self.context,
                record,
                boxes=clean_boxes,
                detector_payload=detector_payload,
            )
            record.artifacts = {
                **dict(record.artifacts or {}),
                "figure_training_bank_record": str(training.get("record_path") or ""),
                "figure_training_bank_manifest": str(training.get("manifest_path") or ""),
                "figure_training_samples_total": int(training.get("samples_total") or 0),
                "figure_training_corrected_images": int(training.get("corrected_images") or 0),
                "figure_training_revision_count": int(training.get("revision_count") or 0),
            }
        except Exception as exc:
            record.artifacts = {
                **dict(record.artifacts or {}),
                "figure_training_bank_error": str(exc),
            }
        self._write_raw_artifacts(record)
        record.sync_status_from_steps()
        record.touch()
        self.staging.upsert_record(record)
        return record

    def _structure_raw_ocr_for_normalization(self, record: StagingProblemRecord):
        from modulos.modulo0_transcriptor.scan_pipeline.pipeline import ScanPipeline

        crop_path = _effective_ocr_image_path(record)
        source = dict(record.source or {})
        try:
            start_n = max(1, int(record.normalized.get("numero") or source.get("problem_number") or source.get("n") or 1))
        except Exception:
            start_n = 1
        pipeline = ScanPipeline(
            provider="ocr",
            debug_dir=str(self.staging.root / "ocr_debug"),
            strict_json=False,
            max_retries=0,
            parse_max_retries=0,
        )
        return pipeline.process_raw_output(
            raw_output=str(record.raw_ocr or ""),
            image_path=crop_path,
            start_n=start_n,
            curso=str(record.normalized.get("curso") or "SIN_CURSO"),
            tema=str(record.normalized.get("tema") or "SIN_TEMA"),
            has_figure_hint=bool(record.figure_segmentation.get("segments_total")),
            initial_items=None,
        )

    @staticmethod
    def _is_continuation_record(record: StagingProblemRecord | None) -> bool:
        if record is None:
            return False
        normalized = dict(record.normalized or {})
        continuation = normalized.get("continuacion") if isinstance(normalized.get("continuacion"), dict) else {}
        return bool(
            continuation_flags_enabled(continuation)
            or has_continuation_marker(record.raw_ocr)
            or has_continuation_marker(normalized.get("latex_rendered_item"))
            or has_continuation_marker(normalized.get("enunciado_latex"))
        )

    @staticmethod
    def _has_continuation_marker(value: Any) -> bool:
        return has_continuation_marker(value)

    @staticmethod
    def _invalidated_downstream_reason(record: StagingProblemRecord | None) -> str:
        if record is None:
            return ""
        downstream_state = dict(dict(record.audit or {}).get("downstream_state") or {})
        if downstream_state.get("status") != "invalidated":
            return ""
        return str(downstream_state.get("reason") or "source_changed").strip() or "source_changed"

    @staticmethod
    def _continuation_record_ids(rows: list[StagingProblemRecord]) -> set[str]:
        ids: set[str] = set()
        for row in rows:
            normalized = dict(row.normalized or {})
            fused = normalized.get("continuaciones_fusionadas") if isinstance(normalized.get("continuaciones_fusionadas"), list) else []
            for item in fused:
                if not isinstance(item, dict):
                    continue
                for key in ("record_id", "crop_id"):
                    value = str(item.get(key) or "").strip()
                    if value:
                        ids.add(value)
        return ids

    @staticmethod
    def _strip_continuation_marker(value: str) -> str:
        return re.sub(r"^\s*(?:\[CONT\.?\]|<\s*CONT\.?\s*>)\s*", "", str(value or ""), flags=re.IGNORECASE).strip()

    def _continuation_text_for_normalization(self, record: StagingProblemRecord) -> str:
        normalized = dict(record.normalized or {})
        candidates = [
            record.raw_ocr,
            normalized.get("enunciado_latex"),
            normalized.get("latex_rendered_item"),
        ]
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                return self._strip_continuation_marker(text)
        return ""

    def _continuation_records_for_parent(
        self,
        parent: StagingProblemRecord,
        all_records: list[StagingProblemRecord],
    ) -> list[StagingProblemRecord]:
        parent_id = str(parent.record_id or "").strip()
        if not parent_id:
            return []
        by_id = {str(row.record_id or ""): row for row in all_records if str(row.record_id or "")}
        rows: list[StagingProblemRecord] = []
        seen: set[str] = set()

        def add(row: StagingProblemRecord | None, *, allow_unmarked: bool = False) -> None:
            if row is None:
                return
            row_id = str(row.record_id or "").strip()
            if not row_id or row_id == parent_id or row_id in seen:
                return
            if not allow_unmarked and not self._is_continuation_record(row):
                return
            rows.append(row)
            seen.add(row_id)

        normalized = dict(parent.normalized or {})
        fused = normalized.get("continuaciones_fusionadas") if isinstance(normalized.get("continuaciones_fusionadas"), list) else []
        for item in fused:
            if isinstance(item, dict):
                add(by_id.get(str(item.get("record_id") or "").strip()), allow_unmarked=True)

        for row in all_records:
            continuation = dict(row.normalized.get("continuacion") or {}) if isinstance(row.normalized.get("continuacion"), dict) else {}
            if str(continuation.get("parent_record_id") or "").strip() == parent_id:
                add(row, allow_unmarked=True)

        try:
            parent_index = next(
                index for index, row in enumerate(all_records) if str(row.record_id or "") == parent_id
            )
        except StopIteration:
            parent_index = -1
        if parent_index >= 0:
            for row in all_records[parent_index + 1 :]:
                if not self._is_continuation_record(row):
                    break
                add(row)
        return rows

    def _merged_raw_ocr_for_normalization(
        self,
        record: StagingProblemRecord,
        continuations: list[StagingProblemRecord] | None = None,
    ) -> str:
        parts = [str(record.raw_ocr or "").strip()]
        for row in list(continuations or []):
            text = self._continuation_text_for_normalization(row)
            if text:
                parts.append(text)
        return "\n".join(part for part in parts if part).strip()

    def _draft_normalized_from_raw_ocr(
        self,
        record: StagingProblemRecord,
        *,
        continuations: list[StagingProblemRecord] | None = None,
    ) -> dict[str, Any]:
        continuation_rows = list(continuations or [])
        raw = self._merged_raw_ocr_for_normalization(record, continuation_rows)
        if not raw:
            return {}
        source = dict(record.source or {})
        base_normalized = dict(record.normalized or {})
        try:
            number = str(int(base_normalized.get("numero") or source.get("problem_number") or source.get("n") or "")).strip()
        except Exception:
            number = str(base_normalized.get("numero") or source.get("problem_number") or source.get("n") or "").strip()
        has_figure = bool((record.figure_segmentation or {}).get("segments_total")) or any(
            bool((row.figure_segmentation or {}).get("segments_total")) for row in continuation_rows
        )
        draft = {
            "schema_version": "normalized_problem_staging_v1",
            "normalizer": "manual_raw_ocr_review",
            "status": StageStatus.NEEDS_REVIEW,
            "updated_at": utc_now_text(),
            "source_record_id": record.record_id,
            "numero": number,
            "curso": str(base_normalized.get("curso") or "SIN_CURSO"),
            "tema": str(base_normalized.get("tema") or "SIN_TEMA"),
            "enunciado_latex": raw,
            "alternativas": {"A": "", "B": "", "C": "", "D": "", "E": ""},
            "respuesta_correcta": "",
            "respuesta_final": "",
            "tiene_grafico": has_figure,
            "figure_tag": f"img-{number or record.record_id}" if has_figure else "",
            "latex_rendered_item": "",
            "metadata_tecnica": {
                "crop_path": record.crop_path,
                "source": source,
                "models": dict(record.models),
                "confidence": dict(record.confidence),
                "raw_ocr_source": "raw_ocr_plus_continuations" if continuation_rows else "raw_ocr_only",
                "continuation_record_ids": [str(row.record_id or "") for row in continuation_rows],
                "continuations_total": len(continuation_rows),
            },
        }
        fused_rows: list[dict[str, Any]] = []
        seen_fused: set[str] = set()
        for item in list(base_normalized.get("continuaciones_fusionadas") or []):
            if not isinstance(item, dict):
                continue
            row_id = str(item.get("record_id") or "").strip()
            if not row_id or row_id in seen_fused:
                continue
            fused_rows.append(dict(item))
            seen_fused.add(row_id)
        for row in continuation_rows:
            row_id = str(row.record_id or "").strip()
            if not row_id or row_id in seen_fused:
                continue
            row_source = dict(row.source or {})
            row_figure = row.figure_segmentation if isinstance(row.figure_segmentation, dict) else {}
            try:
                row_segments_total = int(row_figure.get("segments_total") or 0)
            except Exception:
                row_segments_total = 0
            fused_rows.append(
                {
                    "record_id": row_id,
                    "crop_id": str(row.crop_id or ""),
                    "crop_name": Path(str(row.crop_path or "")).name,
                    "page_number": row_source.get("page_number", row_source.get("source_page_number")),
                    "bbox_px": row_source.get("bbox_px") if isinstance(row_source.get("bbox_px"), list) else None,
                    "has_figure": row_segments_total > 0,
                    "segments_total": row_segments_total,
                    "texto_fusionado": self._continuation_text_for_normalization(row),
                }
            )
            seen_fused.add(row_id)
        if fused_rows:
            draft["continuaciones_fusionadas"] = fused_rows
        return draft

    def _normalize_from_pipeline_record(self, record: StagingProblemRecord, report: dict[str, Any]) -> dict[str, Any]:
        items = list(report.get("items") or []) if isinstance(report, dict) else []
        item_payload: dict[str, Any] = {}
        rendered = ""
        if items and isinstance(items[0], dict):
            item_payload = dict(items[0].get("item") or {})
            rendered = str(items[0].get("rendered") or "")
        if not item_payload:
            return {}
        options = dict(item_payload.get("options") or {})
        return {
            "schema_version": "normalized_problem_staging_v1",
            "normalizer": self.models.normalizer,
            "status": StageStatus.NEEDS_REVIEW,
            "updated_at": utc_now_text(),
            "source_record_id": record.record_id,
            "numero": item_payload.get("n") or "",
            "curso": item_payload.get("curso") or "",
            "tema": item_payload.get("tema") or "",
            "enunciado_latex": item_payload.get("statement") or "",
            "alternativas": {label: str(options.get(label, "") or "") for label in ("A", "B", "C", "D", "E")},
            "respuesta_correcta": item_payload.get("answer_key") or "",
            "tiene_grafico": bool(item_payload.get("has_figure")) or bool(record.figure_segmentation.get("segments_total")),
            "figure_tag": item_payload.get("figure_tag") or "",
            "latex_rendered_item": rendered,
            "metadata_tecnica": {
                "crop_path": record.crop_path,
                "source": dict(record.source),
                "models": dict(record.models),
                "confidence": dict(record.confidence),
            },
        }

    def _write_raw_artifacts(self, record: StagingProblemRecord) -> None:
        artifacts_dir = self.staging.artifact_dir("raw_outputs", record.record_id, probe_file="figure_segmentation.json")
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        raw_path = artifacts_dir / "raw_ocr.txt"
        structured_path = artifacts_dir / "structured_ocr.json"
        normalized_path = artifacts_dir / "normalized.json"
        figure_path = artifacts_dir / "figure_segmentation.json"
        trace_path = artifacts_dir / "traceability.json"
        raw_path.write_text(str(record.raw_ocr or ""), encoding="utf-8")
        structured_path.write_text(json.dumps(record.structured_ocr, ensure_ascii=False, indent=2), encoding="utf-8")
        normalized_path.write_text(json.dumps(record.normalized, ensure_ascii=False, indent=2), encoding="utf-8")
        figure_path.write_text(json.dumps(record.figure_segmentation, ensure_ascii=False, indent=2), encoding="utf-8")
        trace_path.write_text(
            json.dumps(
                {
                    "schema_version": "pdf_factory_raw_traceability_v1",
                    "updated_at": utc_now_text(),
                    "record_id": record.record_id,
                    "crop_id": record.crop_id,
                    "source": dict(record.source or {}),
                    "models": dict(record.models or {}),
                    "confidence": dict(record.confidence or {}),
                    "trace": dict(record.trace or {}),
                    "steps": dict(record.steps or {}),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        record.artifacts = {
            **dict(record.artifacts or {}),
            "raw_outputs_schema": "pdf_factory_raw_outputs_v1",
            "updated_at": utc_now_text(),
            "raw_ocr": str(raw_path),
            "structured_ocr": str(structured_path),
            "normalized": str(normalized_path),
            "figure_segmentation": str(figure_path),
            "traceability": str(trace_path),
        }

    @staticmethod
    def _status_from_counts(*, total: int, ready: int = 0, needs_review: int = 0, errors: int = 0, processing: int = 0) -> str:
        if total <= 0:
            return StageStatus.PENDING
        if errors:
            return StageStatus.ERROR
        if processing:
            return StageStatus.PROCESSING
        if needs_review:
            return StageStatus.NEEDS_REVIEW
        if ready >= total:
            return StageStatus.READY
        return StageStatus.PENDING

    @classmethod
    def _aggregate_record_status(cls, records: list[StagingProblemRecord]) -> str:
        if not records:
            return StageStatus.PENDING
        statuses = [StageStatus.normalize(record.status) for record in records]
        return cls._status_from_counts(
            total=len(statuses),
            ready=sum(1 for status in statuses if status == StageStatus.READY),
            needs_review=sum(1 for status in statuses if status == StageStatus.NEEDS_REVIEW),
            errors=sum(1 for status in statuses if status == StageStatus.ERROR),
            processing=sum(1 for status in statuses if status == StageStatus.PROCESSING),
        )

    @classmethod
    def _aggregate_step_status(cls, records: list[StagingProblemRecord], step: str) -> str:
        if not records:
            return StageStatus.PENDING
        statuses = [record.step_status(step) for record in records]
        return cls._status_from_counts(
            total=len(statuses),
            ready=sum(1 for status in statuses if status == StageStatus.READY),
            needs_review=sum(1 for status in statuses if status == StageStatus.NEEDS_REVIEW),
            errors=sum(1 for status in statuses if status == StageStatus.ERROR),
            processing=sum(1 for status in statuses if status == StageStatus.PROCESSING),
        )

    @classmethod
    def _aggregate_group_status(cls, records: list[StagingProblemRecord], steps: list[str]) -> str:
        if not records:
            return StageStatus.PENDING
        statuses = [record.step_status(step) for record in records for step in steps]
        return cls._status_from_counts(
            total=len(statuses),
            ready=sum(1 for status in statuses if status == StageStatus.READY),
            needs_review=sum(1 for status in statuses if status == StageStatus.NEEDS_REVIEW),
            errors=sum(1 for status in statuses if status == StageStatus.ERROR),
            processing=sum(1 for status in statuses if status == StageStatus.PROCESSING),
        )
