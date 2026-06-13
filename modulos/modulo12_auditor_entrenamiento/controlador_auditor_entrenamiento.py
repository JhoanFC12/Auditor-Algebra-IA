from __future__ import annotations

import base64
import csv
from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
from io import BytesIO
import json
import mimetypes
import os
from pathlib import Path
import random
import re
import shutil
import time
import unicodedata
from typing import Any, Iterable

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - dependencia opcional en entornos de revision.
    OpenAI = None  # type: ignore[assignment]

from modulos.modulo0_transcriptor.domain.model_output_parser import (
    extract_chat_text as parse_chat_text,
    extract_formatted_item as parse_formatted_item,
)
from modulos.modulo0_transcriptor.latex_normalizer import normalize_scan_item_text
from modulos.modulo0_transcriptor.scan_pipeline.extractor import canonicalize_faithful_ocr_text
from modulos.modulo0_transcriptor.scan_pipeline.prompts import build_faithful_ocr_prompt, build_prompt_profile_instructions


_MOJIBAKE_RE = re.compile(r"(?:Ã.|Â.|â..|�)")


def _repair_mojibake_text(text: str) -> str:
    """Repair UTF-8 text that was decoded as Latin-1/Windows-1252."""
    raw = str(text or "")
    if not raw or not _MOJIBAKE_RE.search(raw):
        return raw
    current = raw
    for _ in range(2):
        if not _MOJIBAKE_RE.search(current):
            break
        try:
            repaired = current.encode("latin-1", errors="strict").decode("utf-8", errors="strict")
        except Exception:
            try:
                repaired = current.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
            except Exception:
                break
        if repaired == current:
            break
        current = repaired
    return current


@dataclass(slots=True)
class TrainingIssue:
    level: str
    category: str
    message: str


@dataclass(slots=True)
class SessionTrainingAudit:
    session_path: str
    project: str = ""
    book_code: str = ""
    instance_type: str = ""
    source_images: int = 0
    missing_source_images: int = 0
    items: int = 0
    corrected_items: int = 0
    image_bindings_confirmed: int = 0
    image_bindings_review: int = 0
    segment_boxes: int = 0
    segment_sources: int = 0
    manifest_segments: int = 0
    ocr_raw_blocks: int = 0
    ocr_structured_blocks: int = 0
    training_pairs: int = 0
    training_pairs_ready: int = 0
    training_pairs_missing_image: int = 0
    segmentation_score: int = 0
    ocr_score: int = 0
    global_score: int = 0
    issues: list[TrainingIssue] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.global_score >= 80:
            return "listo"
        if self.global_score >= 55:
            return "revisar"
        return "bloqueado"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status
        return data


@dataclass(slots=True)
class SegmentGoldenRecord:
    record_id: str
    split: str = ""
    book_code: str = ""
    instance_type: str = ""
    source_stem: str = ""
    segment_idx: int | None = None
    segment_bbox_px: list[float] = field(default_factory=list)
    source_path: str = ""
    item_num: int | None = None
    slot: str = ""
    marker_name: str = ""
    binding_confirmed: bool = False
    binding_status: str = ""
    segment_image_path: str = ""
    copied_image_path: str = ""
    session_json: str = ""
    curso: str = ""
    tema: str = ""
    debug_statement: str = ""
    item_text: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.binding_confirmed:
            return "confirmado"
        return self.binding_status or "sin_vinculo"

    @property
    def display_image_path(self) -> str:
        return self.copied_image_path or self.segment_image_path


@dataclass(slots=True)
class OcrGoldenRecord:
    record_id: str
    status: str = "pending"
    book_code: str = ""
    instance_type: str = ""
    session_json: str = ""
    source_label: str = ""
    image_path: str = ""
    copied_image_path: str = ""
    ocr_text: str = ""
    corrected_text: str = ""
    notes: str = ""
    updated_at: str = ""
    training_section: str = ""
    training_section_confidence: str = ""
    training_section_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OcrNormalizationGoldenRecord:
    record_id: str
    status: str = "pending"
    source_ocr_record_id: str = ""
    source_label: str = ""
    book_code: str = ""
    instance_type: str = ""
    raw_ocr: str = ""
    normalized_text: str = ""
    notes: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class ProblemCropTrainingRecord:
    crop_id: str
    image_path: str = ""
    source_pdf_path: str = ""
    source_page_number: int = 0
    source_record_id: str = ""
    bbox_px: list[int] = field(default_factory=list)
    layout_mode: str = ""
    ocr_status: str = "pending_ocr"
    figure_segmentation_status: str = "pending_figure_segmentation"
    ocr_text: str = ""
    corrected_text: str = ""
    notes: str = ""
    figure_boxes_px: list[list[int]] = field(default_factory=list)
    updated_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class TrainingAuditController:
    """Audita sesiones del Transcriptor IA antes de usarlas para entrenamiento."""

    SESSION_SUFFIXES = (".session.json", ".json")
    DEFAULT_GOLDEN_ROOT = Path("E:/Github/Auditor-IA/.cache/transcriptor_runs/datasets")
    DEFAULT_OCR_GOLDEN_DIR = DEFAULT_GOLDEN_ROOT / "ocr_golden_live"
    TRAINED_OCR_ENDPOINT_NAME = "math-ocr-geometry-rules-v4"
    OCR_TRAINING_FIELDS = {
        "General": (Path("E:/Banco de Preguntas"), DEFAULT_GOLDEN_ROOT / "ocr_golden_live"),
        "Geometria": (Path("E:/Banco de Preguntas/2. GEOMETRIA"), DEFAULT_GOLDEN_ROOT / "ocr_geometry_golden_live"),
        "Algebra": (Path("E:/Banco de Preguntas/1. ALGEBRA"), DEFAULT_GOLDEN_ROOT / "ocr_algebra_golden_live"),
        "Aritmetica": (Path("E:/Banco de Preguntas/5. ARITMETICA"), DEFAULT_GOLDEN_ROOT / "ocr_arithmetic_golden_live"),
        "Trigonometria": (Path("E:/Banco de Preguntas/4. TRIGONOMETRIA"), DEFAULT_GOLDEN_ROOT / "ocr_trigonometry_golden_live"),
        "Geometria analitica": (
            Path("E:/Banco de Preguntas/3. GEOMETRIA ANALITICA"),
            DEFAULT_GOLDEN_ROOT / "ocr_analytic_geometry_golden_live",
        ),
    }
    DEFAULT_GEOMETRY_ROOT, DEFAULT_GEOMETRY_OCR_GOLDEN_DIR = OCR_TRAINING_FIELDS["Geometria"]
    DEFAULT_OCR_NORMALIZATION_GOLDEN_DIR = DEFAULT_GOLDEN_ROOT / "ocr_normalization_golden_live"
    DEFAULT_PROBLEM_CROPS_LIVE_DIR = DEFAULT_GOLDEN_ROOT / "problem_crops_live"
    DEFAULT_SEGMENT_LIVE_DIR = DEFAULT_GOLDEN_ROOT / "segment_training_live"
    TRAINED_OCR_VISION_MODEL = "Jhoan12/math-ocr-qwen2.5-vl-3b-geometry-rules-merged-v4"
    DEFAULT_OPENAI_FORMAT_MODEL = TRAINED_OCR_VISION_MODEL
    DEFAULT_HF_FORMAT_MODEL = TRAINED_OCR_VISION_MODEL

    SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
        "Geometria": (
            "geometria",
            "geometria plana",
            "circunferencia",
            "triangulo",
            "triangulos",
            "angulo",
            "angulos",
            "poligono",
            "poligonos",
            "cuadrilatero",
            "cuadrilateros",
            "semejanza",
            "congruencia",
            "relaciones metricas",
            "areas de regiones",
            "puntos notables",
            "segmentos",
            "rectas",
        ),
        "Geometria analitica": (
            "geometria analitica",
            "plano cartesiano",
            "recta analitica",
            "circunferencia analitica",
            "parabola",
            "elipse",
            "hiperbola",
        ),
        "Algebra": (
            "algebra",
            "polinomio",
            "polinomios",
            "factorizacion",
            "ecuacion",
            "ecuaciones",
            "inecuacion",
            "inecuaciones",
            "radicacion",
            "radicales",
            "exponentes",
            "binomio",
            "matrices",
            "determinantes",
            "sistemas",
            "funciones",
            "logaritmos",
        ),
        "Aritmetica": (
            "aritmetica",
            "numeracion",
            "divisibilidad",
            "mcd",
            "mcm",
            "fracciones",
            "porcentajes",
            "razones",
            "proporciones",
            "promedios",
            "conjuntos",
            "probabilidades",
            "conteo",
        ),
        "Trigonometria": (
            "trigonometria",
            "trigonometrica",
            "trigonometricas",
            "seno",
            "coseno",
            "tangente",
            "cotangente",
            "secante",
            "cosecante",
            "identidades",
            "arcos trigonometricos",
        ),
    }

    SECTION_DISPLAY = {
        "Geometria": "Geometría",
        "Geometria analitica": "Geometría analítica",
        "Algebra": "Álgebra",
        "Aritmetica": "Aritmética",
        "Trigonometria": "Trigonometría",
        "General": "General",
    }

    @staticmethod
    def normalize_training_text(value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        text = unicodedata.normalize("NFD", text)
        text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
        text = re.sub(r"[_\\/\-.,;:()\[\]{}]+", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @classmethod
    def classify_training_section_from_fields(
        cls,
        *,
        curso: str = "",
        tema: str = "",
        book_code: str = "",
        instance_type: str = "",
        source_label: str = "",
        text: str = "",
    ) -> dict[str, str]:
        fields = {
            "curso": curso,
            "tema": tema,
            "book_code": book_code,
            "instance_type": instance_type,
            "source_label": source_label,
            "text": text,
        }
        normalized_fields = {key: cls.normalize_training_text(value) for key, value in fields.items()}
        course_value = normalized_fields.get("curso", "")
        if course_value:
            for section, keywords in cls.SECTION_KEYWORDS.items():
                if any(keyword in course_value for keyword in keywords):
                    return {
                        "section": section,
                        "confidence": "alta",
                        "reason": f"curso contiene '{course_value}'",
                    }

        weighted_sources = (
            ("book_code", 4),
            ("instance_type", 3),
            ("source_label", 2),
            ("tema", 2),
            ("text", 1),
        )
        scores: dict[str, int] = {}
        reasons: dict[str, list[str]] = {}
        for field_name, weight in weighted_sources:
            haystack = normalized_fields.get(field_name, "")
            if not haystack:
                continue
            for section, keywords in cls.SECTION_KEYWORDS.items():
                for keyword in keywords:
                    keyword_norm = cls.normalize_training_text(keyword)
                    if keyword_norm and keyword_norm in haystack:
                        scores[section] = scores.get(section, 0) + weight
                        reasons.setdefault(section, []).append(f"{field_name} contiene '{keyword}'")
                        break

        if not scores:
            return {"section": "General", "confidence": "baja", "reason": "sin palabras clave confiables"}
        section, score = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0]
        confidence = "alta" if score >= 4 else "media" if score >= 2 else "baja"
        return {
            "section": section,
            "confidence": confidence,
            "reason": "; ".join(reasons.get(section, [])[:3]),
        }

    def scale_trained_ocr_endpoint_to_zero(self) -> str:
        """Detiene la GPU dedicada sin bloquear su reactivacion en la siguiente llamada."""
        try:
            from huggingface_hub import HfApi
        except Exception as exc:
            raise RuntimeError("Falta huggingface_hub para apagar el endpoint OCR.") from exc
        token = os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGINGFACEHUB_API_TOKEN", "").strip()
        if not token:
            raise RuntimeError("No se encontro HF_TOKEN para apagar el endpoint OCR.")
        endpoint_name = os.getenv("HF_TRAINED_OCR_ENDPOINT_NAME", "").strip() or self.TRAINED_OCR_ENDPOINT_NAME
        api = HfApi(token=token)
        endpoint = next((row for row in api.list_inference_endpoints() if row.name == endpoint_name), None)
        if endpoint is None:
            raise RuntimeError(f"No se encontro el endpoint OCR dedicado: {endpoint_name}")
        endpoint.scale_to_zero()
        return str(endpoint.status or "scaledToZero")

    def load_problem_crops_live(
        self,
        root: Path | None = None,
        *,
        crop_ids: Iterable[str] | None = None,
    ) -> list[ProblemCropTrainingRecord]:
        target = Path(root or self.DEFAULT_PROBLEM_CROPS_LIVE_DIR).expanduser().resolve()
        records_dir = target / "records"
        if not records_dir.exists():
            return []
        crop_id_filter = [str(value or "").strip() for value in (crop_ids or []) if str(value or "").strip()]
        if crop_id_filter:
            record_paths = [records_dir / f"{crop_id}.json" for crop_id in crop_id_filter]
        else:
            record_paths = sorted(records_dir.glob("*.json"), key=lambda item: item.name.lower())
        records: list[ProblemCropTrainingRecord] = []
        for path in record_paths:
            if not path.exists():
                continue
            raw = self._load_json(path)
            if not raw:
                continue
            image_rel = str(raw.get("crop_image_rel") or "")
            image_path = (target / image_rel).resolve() if image_rel else Path("")
            records.append(
                ProblemCropTrainingRecord(
                    crop_id=str(raw.get("crop_id") or path.stem),
                    image_path=str(image_path),
                    source_pdf_path=str(raw.get("source_pdf_path") or ""),
                    source_page_number=int(raw.get("source_page_number") or 0),
                    source_record_id=str(raw.get("source_record_id") or ""),
                    bbox_px=[int(value) for value in (raw.get("bbox_px") or [])],
                    layout_mode=str(raw.get("layout_mode") or ""),
                    ocr_status=str(raw.get("ocr_status") or "pending_ocr"),
                    figure_segmentation_status=str(raw.get("figure_segmentation_status") or "pending_figure_segmentation"),
                    ocr_text=str(raw.get("ocr_text") or ""),
                    corrected_text=str(raw.get("corrected_text") or ""),
                    notes=str(raw.get("notes") or ""),
                    figure_boxes_px=[list(map(int, box)) for box in (raw.get("figure_boxes_px") or [])],
                    updated_at=str(raw.get("updated_at") or ""),
                    raw=raw,
                )
            )
        return records

    def save_problem_crop_review(
        self,
        record: ProblemCropTrainingRecord,
        *,
        ocr_text: str | None = None,
        corrected_text: str | None = None,
        notes: str | None = None,
        ocr_status: str | None = None,
        figure_segmentation_status: str | None = None,
        figure_boxes_px: list[list[int]] | None = None,
        root: Path | None = None,
    ) -> ProblemCropTrainingRecord:
        target = Path(root or self.DEFAULT_PROBLEM_CROPS_LIVE_DIR).expanduser().resolve()
        record_path = target / "records" / f"{record.crop_id}.json"
        raw = self._load_json(record_path) if record_path.exists() else dict(record.raw)
        raw.update(
            {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "ocr_text": record.ocr_text if ocr_text is None else ocr_text,
                "corrected_text": record.corrected_text if corrected_text is None else corrected_text,
                "notes": record.notes if notes is None else notes,
                "ocr_status": record.ocr_status if ocr_status is None else ocr_status,
                "figure_segmentation_status": (
                    record.figure_segmentation_status
                    if figure_segmentation_status is None
                    else figure_segmentation_status
                ),
                "figure_boxes_px": record.figure_boxes_px if figure_boxes_px is None else figure_boxes_px,
            }
        )
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        return next(item for item in self.load_problem_crops_live(target) if item.crop_id == record.crop_id)

    def find_session_files(self, root: Path) -> list[Path]:
        root = Path(root)
        if root.is_file():
            return [root] if self._looks_like_session(root) else []
        if not root.exists():
            return []
        candidates: list[Path] = []
        for path in root.rglob("*.json"):
            name = path.name.lower()
            if not any(name.endswith(suffix) for suffix in self.SESSION_SUFFIXES):
                continue
            if self._looks_like_session(path):
                candidates.append(path)
        return sorted(candidates, key=lambda p: str(p).lower())

    def audit_root(self, root: Path) -> list[SessionTrainingAudit]:
        return [self.audit_session(path) for path in self.find_session_files(root)]

    def audit_session(self, session_path: Path) -> SessionTrainingAudit:
        payload = self._load_json(session_path)
        audit = SessionTrainingAudit(session_path=str(session_path))
        ui = payload.get("ui") if isinstance(payload.get("ui"), dict) else {}
        ui_settings = payload.get("ui_settings") if isinstance(payload.get("ui_settings"), dict) else {}
        audit.project = str(payload.get("project_name") or ui.get("project_name") or ui_settings.get("project_name") or "").strip()
        audit.book_code = str(ui.get("book_code") or ui_settings.get("book_code") or payload.get("book_code") or "").strip()
        audit.instance_type = str(
            ui.get("instance_type") or ui_settings.get("instance_type") or payload.get("instance_type") or ""
        ).strip()

        sources = self._collect_sources(payload)
        audit.source_images = len(sources)
        audit.missing_source_images = sum(1 for raw_path in sources if not self._path_exists(raw_path, session_path))

        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        audit.items = len(items)
        audit.corrected_items = self._count_corrected_items(payload, items)
        audit.image_bindings_confirmed, audit.image_bindings_review = self._count_image_bindings(items)

        audit.segment_boxes, audit.segment_sources = self._count_segmentation_boxes(payload)
        audit.manifest_segments = self._count_manifest_segments(session_path, payload)
        audit.ocr_raw_blocks = self._count_dict(payload.get("ocr_raw_first_by_label"))
        audit.ocr_structured_blocks = self._count_dict(payload.get("ocr_structured_by_label"))

        audit.training_pairs, audit.training_pairs_ready, audit.training_pairs_missing_image = self._count_training_pairs(
            payload, session_path
        )

        self._add_issues(audit)
        audit.segmentation_score = self._score_segmentation(audit)
        audit.ocr_score = self._score_ocr(audit)
        audit.global_score = max(0, min(100, round((audit.segmentation_score + audit.ocr_score) / 2)))
        return audit

    def export_json(self, audits: Iterable[SessionTrainingAudit], output_path: Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "sessions": [audit.to_dict() for audit in audits],
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    def export_csv(self, audits: Iterable[SessionTrainingAudit], output_path: Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [audit.to_dict() for audit in audits]
        fieldnames = [
            "status",
            "global_score",
            "segmentation_score",
            "ocr_score",
            "project",
            "book_code",
            "instance_type",
            "source_images",
            "missing_source_images",
            "items",
            "corrected_items",
            "image_bindings_confirmed",
            "image_bindings_review",
            "segment_boxes",
            "segment_sources",
            "manifest_segments",
            "ocr_raw_blocks",
            "ocr_structured_blocks",
            "training_pairs",
            "training_pairs_ready",
            "training_pairs_missing_image",
            "session_path",
        ]
        with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in fieldnames})
        return output_path

    def normalize_ocr_with_format_model(
        self,
        text: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        timeout_s: int = 180,
        retries: int = 2,
    ) -> tuple[str, dict[str, Any]]:
        """Normaliza una muestra OCR usando el mismo modelo de formateo del Transcriptor IA."""
        raw_text = str(text or "").strip()
        if not raw_text:
            raise ValueError("No hay texto OCR para normalizar.")
        provider_norm = self._resolve_format_provider(provider)
        model_name = self._resolve_format_model(provider_norm, model)
        prompt = self._build_ocr_format_prompt(raw_text)
        meta = {"provider": provider_norm, "model": model_name, "used_model": False}
        if provider_norm == "openai":
            output = self._call_openai_format_model(
                model=model_name,
                system_prompt=self._build_format_system_prompt(),
                prompt=prompt,
                timeout_s=timeout_s,
                retries=retries,
            )
        elif provider_norm in {"hf", "huggingface"}:
            output = self._call_hf_format_model(
                model=model_name,
                system_prompt=self._build_format_system_prompt(),
                prompt=prompt,
                timeout_s=timeout_s,
                retries=retries,
            )
        else:
            output = ""
        normalized = self._extract_formatted_item(output)
        if normalized:
            meta["used_model"] = True
            return normalized, meta
        local = str(normalize_scan_item_text(raw_text).text or raw_text).strip()
        meta["fallback"] = "latex_normalizer"
        return self._normalize_one_line(local), meta

    def scan_image_ocr_and_normalize(
        self,
        image_path: Path,
        *,
        provider: str | None = None,
        model: str | None = None,
        book_code: str = "",
        instance_type: str = "",
        timeout_s: int = 180,
        retries: int = 8,
    ) -> tuple[str, str, dict[str, Any]]:
        """Vuelve a escanear la imagen completa y devuelve OCR puro sin normalizacion."""
        path = Path(image_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"No existe la imagen OCR: {path}")
        provider_norm = self._resolve_format_provider(provider)
        model_name = self._resolve_format_model(provider_norm, model)
        prompt = self._build_full_image_raw_ocr_prompt(book_code=book_code, instance_type=instance_type)
        meta = {"provider": provider_norm, "model": model_name, "used_model": False}
        if provider_norm == "openai":
            output = self._call_openai_image_ocr_model(
                path=path,
                model=model_name,
                prompt=prompt,
                timeout_s=timeout_s,
                retries=retries,
            )
        elif provider_norm in {"hf", "huggingface"}:
            output = self._call_hf_image_ocr_model(
                path=path,
                model=model_name,
                prompt=prompt,
                timeout_s=timeout_s,
                retries=retries,
            )
        else:
            output = ""
        raw_model_text = _repair_mojibake_text(str(output or "").strip())
        raw_ocr = str(canonicalize_faithful_ocr_text(raw_model_text) or raw_model_text).strip()
        raw_ocr = _repair_mojibake_text(raw_ocr)
        if not raw_ocr:
            raise RuntimeError("El modelo no devolvio OCR util para la imagen.")
        meta["used_model"] = True
        meta["raw_model_text"] = raw_model_text
        return raw_ocr, raw_ocr, meta

    def build_segment_golden_base(
        self,
        roots: Iterable[Path],
        out_root: Path | None = None,
        *,
        seed: int = 42,
    ) -> Path:
        """Crea una base global de recortes segmentados independiente de sesiones."""
        from tools.build_segment_training_base import build_segment_training_base

        clean_roots = [Path(root).expanduser().resolve() for root in roots if str(root or "").strip()]
        if not clean_roots:
            raise ValueError("No se indico ninguna raiz para construir la golden base.")
        destination = Path(out_root or self.DEFAULT_GOLDEN_ROOT).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        return build_segment_training_base(roots=clean_roots, out_root=destination, seed=seed, copy_mode="copy")

    def find_latest_golden_base(self, root: Path | None = None) -> Path | None:
        base_root = Path(root or self.DEFAULT_GOLDEN_ROOT).expanduser()
        if not base_root.exists():
            return None
        if root is None and self._golden_base_has_records(self.DEFAULT_SEGMENT_LIVE_DIR):
            return self.DEFAULT_SEGMENT_LIVE_DIR
        if self._golden_base_has_records(base_root):
            return base_root
        candidates = [path for path in base_root.glob("segment_training_base_*") if self._golden_base_has_records(path)]
        if not candidates:
            return None
        return max(candidates, key=lambda path: path.stat().st_mtime)

    def load_segment_golden_base(self, golden_dir: Path) -> list[SegmentGoldenRecord]:
        golden_dir = Path(golden_dir).expanduser().resolve()
        records_path = golden_dir / "records_all.jsonl"
        if not records_path.exists():
            raise FileNotFoundError(f"No existe records_all.jsonl en: {golden_dir}")
        records: list[SegmentGoldenRecord] = []
        with records_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except Exception:
                    continue
                if isinstance(raw, dict):
                    records.append(self._record_from_golden_row(raw, golden_dir))
        source_records_path = golden_dir / "source_records_all.jsonl"
        if source_records_path.exists():
            represented = {str(Path(record.source_path)).lower() for record in records if record.source_path}
            with source_records_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        raw = json.loads(line)
                    except Exception:
                        continue
                    source_path = str(raw.get("source_path") or "")
                    if not source_path or source_path.lower() in represented:
                        continue
                    detector = raw.get("detector_review") if isinstance(raw.get("detector_review"), dict) else {}
                    records.append(
                        SegmentGoldenRecord(
                            record_id=str(raw.get("record_id") or ""),
                            source_stem=str(raw.get("source_stem") or Path(source_path).stem),
                            source_path=source_path,
                            copied_image_path=str((golden_dir / str(raw.get("source_image_rel") or "")).resolve()),
                            binding_status=str(detector.get("review_status") or "pending"),
                            raw=raw,
                        )
                    )
        return records

    def _resolve_format_provider(self, provider: str | None = None) -> str:
        raw = str(provider or os.getenv("SCAN_PROVIDER", "") or "openai").strip().lower()
        if raw in {"huggingface", "hf", "hfh", "hf_api"}:
            return "hf"
        return "hf"

    def _resolve_format_model(self, provider: str, model: str | None = None) -> str:
        explicit = str(model or "").strip()
        if explicit:
            return explicit
        if provider == "hf":
            return self.TRAINED_OCR_VISION_MODEL
        return self.TRAINED_OCR_VISION_MODEL

    @staticmethod
    def _trained_ocr_endpoint_configured() -> bool:
        return bool((os.getenv("HF_TRAINED_OCR_BASE_URL", "") or "").strip())

    def _build_format_system_prompt(self) -> str:
        return (
            "Eres un formateador matematico conservador para items de examen.\n"
            "Devuelve SOLO JSON valido exacto con forma {\"item\":\"...\"}.\n"
            "No markdown, no explicaciones, no razonamiento visible.\n"
            "No inventes contenido, no resuelvas, no agregues datos.\n"
            "Conserva el texto visible y solo normaliza estructura, delimitadores matematicos y LaTeX."
        )

    def _build_ocr_format_prompt(self, raw_text: str) -> str:
        return (
            "Convierte esta transcripcion OCR a nuestro formato scan LaTeX.\n"
            "Reglas obligatorias:\n"
            "- Devuelve un solo JSON: {\"item\":\"\\\\item[\\\\textbf{n.}] ...\"}\n"
            "- Si detectas numero de problema, usalo; si no, usa n=1.\n"
            "- Preserva el contenido visible del OCR sin resolver ni completar datos no visibles.\n"
            "- Envuelve con $...$ solo expresiones matematicas, variables, medidas, funciones y opciones.\n"
            "- Las alternativas deben quedar como A) $...$, B) $...$, C) $...$, D) $...$, E) $...$ si existen.\n"
            "- Usa separadores del sistema: £ antes del bloque A-D/E y æ entre opciones.\n"
            "- No agregues etiquetas [[curso=...]], [[tema=...]], [[clave=...]] ni [[Estado=...]].\n"
            "- Si hay marcador de imagen visible en la entrada, conservalo; si no, no inventes imagen.\n"
            "- Repara solo errores obvios de LaTeX o delimitadores rotos.\n"
            "OCR RAW:\n"
            f"{raw_text}\n"
        )

    def _build_image_ocr_normalize_prompt(self) -> str:
        return (
            "Escanea visualmente esta imagen de un problema matematico y normalizala.\n"
            "Devuelve SOLO JSON valido con esta forma exacta:\n"
            "{\"ocr\":\"transcripcion fiel visible\",\"item\":\"\\\\item[\\\\textbf{n.}] ...\"}\n"
            "Reglas para ocr:\n"
            "- Transcribe fielmente lo visible, sin resolver y sin inventar contenido.\n"
            "- Conserva numero de problema y alternativas si aparecen.\n"
            "- Si hay partes ilegibles usa ... solo en esa parte.\n"
            "Reglas para item:\n"
            "- Usa formato scan LaTeX en una linea.\n"
            "- Si detectas numero de problema, usalo; si no, usa n=1.\n"
            "- Envuelve con $...$ expresiones matematicas, variables, medidas y alternativas.\n"
            "- Alternativas como A) $...$, B) $...$, C) $...$, D) $...$, E) $...$ si existen.\n"
            "- Usa separadores del sistema: £ antes del bloque de opciones y æ entre opciones.\n"
            "- No agregues [[curso=...]], [[tema=...]], [[clave=...]] ni [[Estado=...]].\n"
            "- No inventes imagen ni marcador [[Imagen=...]].\n"
            "- No expliques nada fuera del JSON."
        )

    def _build_full_image_raw_ocr_prompt(self, *, book_code: str = "", instance_type: str = "") -> str:
        return (
            "Transcribe absolutamente TODO el texto visible de la imagen completa en orden de lectura.\n"
            "Devuelve SOLO texto plano, sin JSON, sin markdown y sin explicaciones.\n"
            "No resumas, no resuelvas, no completes por inferencia y no decidas que contenido sirve o no sirve.\n"
            "No conviertas el contenido al formato scan LaTeX.\n"
            "No reemplaces valores por puntos suspensivos salvo que sean realmente ilegibles.\n"
            "No selecciones un solo problema: transcribe toda la pagina/recorte completo en orden de lectura.\n"
            "Conserva encabezados, numeros, alternativas, formulas, textos laterales, marcas visibles y continuaciones.\n"
            "Usa LaTeX SOLO para elementos matematicos visibles: fracciones, potencias, raices, subindices, simbolos, grados, angulos, arcos, expresiones algebraicas y variables.\n"
            "Conserva el texto comun en espanol normal; no conviertas todo el enunciado a LaTeX.\n"
            "Si un problema tiene numero visible, aunque este dentro de un circulo, sello o adorno, escribelo exactamente como '<numero.>' al inicio de una linea nueva.\n"
            "Si aparece encabezado tipo 'PROBLEMA N° 12', 'PROBLEMA Nº 12' o 'PREGUNTA N° 12', normalizalo como '<12.>' al inicio de linea.\n"
            "Si la imagen empieza con texto, formula o alternativas que continuan el problema anterior y no aparece un numero nuevo claro, inicia con '[CONT.]'.\n"
            "Si la imagen empieza directamente con alternativas A), B), C), D) o E), transcribelas completas despues de '[CONT.]'.\n"
            "Escribe cada alternativa en su propia linea como A), B), C), D), E), aunque en la imagen aparezca a), b), c), d), e).\n"
            "Si varias alternativas estan en una misma linea, separalas en lineas individuales sin perder sus valores.\n"
            "No inventes encabezados nuevos; solo usa '<n.>' cuando el numero sea visible o el encabezado sea claro.\n"
            "No agregues digitos fantasma: si el visible es 3, escribe '<3.>'; nunca '<93.>' ni '<103.>'.\n"
            "Si una linea solo continua una formula, fraccion o enunciado del problema anterior, no la conviertas en problema nuevo.\n"
            "Mantén saltos de linea razonables segun el orden visual: de arriba hacia abajo y de izquierda a derecha.\n"
            "Si hay varias columnas, respeta el orden de lectura natural de la imagen.\n"
            "Si hay un dibujo, grafico o diagrama matematico visible asociado al problema <n.>, escribe exactamente [[Imagen=img-n]] al final del enunciado y antes de sus alternativas.\n"
            "Cuenta como grafico: figura geometrica, triangulo, circunferencia, poligono, recta, angulo, arco, plano, funcion, relacion, conjunto, region sombreada o ilustracion matematica. Las tablas no cuentan como grafico.\n"
            "No hace falta que el enunciado diga 'en el grafico' o 'en la figura'; si el dibujo esta visible y pertenece al problema, debes colocar la etiqueta de imagen.\n"
            "Si el bloque es una continuacion sin numero propio y contiene una figura, escribe exactamente [[Imagen=img-continuacion]] en su posicion visual.\n"
            "Si hay una figura visible pero no puedes asociarla con seguridad a un numero, escribe [[Imagen=img-pendiente]] en su posicion visual.\n"
            "No describas ni reconstruyas la figura y no copies sus letras internas dentro del enunciado.\n"
            f"{build_prompt_profile_instructions(book_code=book_code, instance_type=instance_type, stage='ocr')}"
            "Salida final: solamente la transcripcion fiel completa de toda la imagen."
        )

    def _encode_image_data_url(self, path: Path, *, max_side_px: int | None = None) -> str:
        image_path = Path(path)
        data = image_path.read_bytes()
        mime, _ = mimetypes.guess_type(image_path.name)
        mime = mime or "image/png"
        if max_side_px:
            try:
                from PIL import Image

                with Image.open(image_path) as source:
                    image = source.convert("RGB")
                if max(image.size) > max_side_px:
                    image.thumbnail((max_side_px, max_side_px))
                output = BytesIO()
                image.save(output, format="JPEG", quality=92, optimize=True)
                data = output.getvalue()
                mime = "image/jpeg"
            except Exception:
                pass
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"

    def _resolve_trained_ocr_max_tokens(self, *, model: str, requested: int) -> int:
        requested = max(256, int(requested or 0))
        if str(model or "").strip() != self.TRAINED_OCR_VISION_MODEL:
            return requested
        try:
            cap = int(str(os.getenv("HF_TRAINED_OCR_MAX_TOKENS", "700") or "700").strip())
        except Exception:
            cap = 700
        return max(256, min(requested, cap))

    def _resolve_trained_ocr_image_max_side(self, *, model: str) -> int:
        if str(model or "").strip() != self.TRAINED_OCR_VISION_MODEL:
            return 1100
        try:
            max_side = int(str(os.getenv("HF_TRAINED_OCR_IMAGE_MAX_SIDE", "560") or "560").strip())
        except Exception:
            max_side = 560
        return max(480, min(1600, max_side))

    def _resolve_trained_ocr_context_fallback_image_max_side(self, *, model: str) -> int | None:
        if str(model or "").strip() != self.TRAINED_OCR_VISION_MODEL:
            return None
        try:
            max_side = int(str(os.getenv("HF_TRAINED_OCR_CONTEXT_FALLBACK_IMAGE_MAX_SIDE", "384") or "384").strip())
        except Exception:
            max_side = 384
        return max(320, min(640, max_side))

    def _is_context_length_model_error(self, exc: Exception) -> bool:
        text = str(exc or "").lower()
        return (
            ("max_tokens" in text or "max_completion_tokens" in text)
            and (
                "maximum context length" in text
                or "input tokens" in text
                or "too large" in text
                or "context length" in text
            )
        )

    def _call_openai_image_ocr_model(
        self,
        *,
        path: Path,
        model: str,
        prompt: str,
        timeout_s: int,
        retries: int,
    ) -> str:
        if OpenAI is None:
            raise RuntimeError("La libreria openai no esta disponible.")
        client = OpenAI(timeout=timeout_s)
        img_url = self._encode_image_data_url(path, max_side_px=1100)
        last_exc: Exception | None = None
        for attempt in range(max(0, retries) + 1):
            try:
                kwargs = {
                    "model": model,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": prompt},
                                {"type": "input_image", "image_url": img_url},
                            ],
                        }
                    ],
                }
                try:
                    resp = client.responses.create(**kwargs, timeout=timeout_s)
                except TypeError:
                    resp = client.responses.create(**kwargs)
                return str(getattr(resp, "output_text", "") or "").strip()
            except Exception as exc:
                last_exc = exc
                if attempt < retries and self._is_retryable_model_error(exc):
                    time.sleep(self._retry_delay_seconds(attempt))
                    continue
                break
        if last_exc is not None:
            raise last_exc
        return ""

    def _call_hf_image_ocr_model(
        self,
        *,
        path: Path,
        model: str,
        prompt: str,
        timeout_s: int,
        retries: int,
    ) -> str:
        token = os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGINGFACEHUB_API_TOKEN", "").strip()
        if OpenAI is None:
            raise RuntimeError("La libreria openai no esta disponible para el endpoint HF compatible.")
        if not token:
            raise RuntimeError("No se encontro HF_TOKEN para usar el modelo OCR visual en Hugging Face.")
        base_url = self._resolve_hf_image_ocr_base_url(model)
        client = OpenAI(base_url=base_url, api_key=token, timeout=timeout_s)
        max_tokens = self._resolve_trained_ocr_max_tokens(model=model, requested=1100)
        initial_side = self._resolve_trained_ocr_image_max_side(model=model)
        fallback_side = self._resolve_trained_ocr_context_fallback_image_max_side(model=model)
        last_exc: Exception | None = None
        used_context_fallback = False
        for attempt in range(max(0, retries) + 1):
            try:
                img_url = self._encode_image_data_url(
                    path,
                    max_side_px=fallback_side if used_context_fallback and fallback_side else initial_side,
                )
                resp = client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": img_url}},
                            ],
                        }
                    ],
                    temperature=0,
                    max_tokens=int(max_tokens),
                )
                message = resp.choices[0].message if resp and resp.choices else None
                return parse_chat_text(message.content if message is not None else "")
            except Exception as exc:
                last_exc = exc
                if (
                    (not used_context_fallback)
                    and fallback_side is not None
                    and self._is_context_length_model_error(exc)
                ):
                    used_context_fallback = True
                    continue
                if attempt < retries and self._is_retryable_model_error(exc):
                    time.sleep(self._retry_delay_seconds(attempt))
                    continue
                break
        if last_exc is not None:
            raise last_exc
        return ""

    def _resolve_hf_image_ocr_base_url(self, model: str) -> str:
        if str(model or "").strip() == self.TRAINED_OCR_VISION_MODEL:
            endpoint = os.getenv("HF_TRAINED_OCR_BASE_URL", "").strip()
            if not endpoint:
                raise RuntimeError(
                    "El modelo OCR entrenado requiere configurar HF_TRAINED_OCR_BASE_URL "
                    "con la URL OpenAI-compatible de su endpoint dedicado."
                )
            return endpoint.rstrip("/")
        return os.getenv("HF_BASE_URL", "").strip() or "https://router.huggingface.co/v1"

    def _call_openai_format_model(
        self,
        *,
        model: str,
        system_prompt: str,
        prompt: str,
        timeout_s: int,
        retries: int,
    ) -> str:
        if OpenAI is None:
            raise RuntimeError("La libreria openai no esta disponible.")
        client = OpenAI(timeout=timeout_s)
        last_exc: Exception | None = None
        for attempt in range(max(0, retries) + 1):
            try:
                kwargs = {
                    "model": model,
                    "input": [
                        {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                        {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
                    ],
                }
                try:
                    resp = client.responses.create(**kwargs, timeout=timeout_s)
                except TypeError:
                    resp = client.responses.create(**kwargs)
                return str(getattr(resp, "output_text", "") or "").strip()
            except Exception as exc:
                last_exc = exc
                if attempt < retries and self._is_retryable_model_error(exc):
                    time.sleep(min(8, 2**attempt))
                    continue
                break
        if last_exc is not None:
            raise last_exc
        return ""

    def _call_hf_format_model(
        self,
        *,
        model: str,
        system_prompt: str,
        prompt: str,
        timeout_s: int,
        retries: int,
    ) -> str:
        token = os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGINGFACEHUB_API_TOKEN", "").strip()
        if OpenAI is None:
            raise RuntimeError("La libreria openai no esta disponible para el endpoint HF compatible.")
        if not token:
            raise RuntimeError("No se encontro HF_TOKEN para usar el modelo de formateo en Hugging Face.")
        base_url = os.getenv("HF_BASE_URL", "").strip() or "https://router.huggingface.co/v1"
        client = OpenAI(base_url=base_url, api_key=token, timeout=timeout_s)
        last_exc: Exception | None = None
        for attempt in range(max(0, retries) + 1):
            try:
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0,
                        max_tokens=900,
                        response_format={"type": "json_object"},
                    )
                except Exception as json_exc:
                    msg = str(json_exc).lower()
                    if "response_format" not in msg and "json_object" not in msg:
                        raise
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0,
                        max_tokens=900,
                    )
                message = resp.choices[0].message if resp and resp.choices else None
                return parse_chat_text(message.content if message is not None else "")
            except Exception as exc:
                last_exc = exc
                if attempt < retries and self._is_retryable_model_error(exc):
                    time.sleep(min(8, 2**attempt))
                    continue
                break
        if last_exc is not None:
            raise last_exc
        return ""

    def _extract_formatted_item(self, text: str) -> str:
        return parse_formatted_item(
            text,
            normalize_text=self._normalize_one_line,
            extract_first_item=self._extract_first_item,
        )

    def _extract_json_object(self, text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.IGNORECASE | re.DOTALL)
        if fenced:
            raw = fenced.group(1).strip()
        candidates: list[str] = []
        depth = 0
        start = -1
        in_str = False
        esc = False
        for i, ch in enumerate(raw):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start >= 0:
                        candidates.append(raw[start : i + 1])
                        start = -1
        for candidate in reversed(candidates or [raw]):
            try:
                parsed = json.loads(candidate)
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
        return {}

    def _extract_first_item(self, text: str) -> str:
        raw = self._normalize_one_line(text)
        if not raw:
            return ""
        match = re.search(r"(\\item\[\s*\\textbf\{\d+\.\}\s*\].*)", raw)
        if match:
            return self._normalize_one_line(match.group(1))
        return raw if raw.startswith("\\item") else ""

    def _normalize_one_line(self, text: str) -> str:
        raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        return re.sub(r"\s+", " ", " ".join(part.strip() for part in raw.split("\n") if part.strip())).strip()

    def _is_retryable_model_error(self, exc: Exception | str) -> bool:
        msg = str(exc or "").lower()
        return any(
            token in msg
            for token in (
                "timeout",
                "timed out",
                "rate",
                "429",
                "500",
                "502",
                "503",
                "504",
                "service_unavailable",
                "service unavailable",
                "temporarily",
                "overloaded",
            )
        )

    @staticmethod
    def _retry_delay_seconds(attempt: int) -> int:
        return min(60, 2 ** (max(0, int(attempt)) + 1))

    def collect_ocr_samples_from_sessions(
        self,
        roots: Iterable[Path],
        *,
        limit: int = 200,
        out_dir: Path | None = None,
        randomize: bool = True,
        seed: int = 42,
    ) -> Path:
        destination = Path(out_dir or self.DEFAULT_OCR_GOLDEN_DIR).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "records").mkdir(parents=True, exist_ok=True)
        (destination / "images").mkdir(parents=True, exist_ok=True)
        existing = self._load_ocr_record_files(destination)
        added = 0
        session_paths = list(self._iter_session_files([Path(root).expanduser().resolve() for root in roots]))
        if randomize:
            random.Random(seed).shuffle(session_paths)
        for session_path in session_paths:
            payload = self._load_json(session_path)
            raw_map = payload.get("ocr_raw_first_by_label")
            if not isinstance(raw_map, dict):
                continue
            source_map = self._resolve_session_source_map(session_path, payload)
            raw_items = list(raw_map.items())
            if randomize:
                random.Random(f"{seed}|{session_path}").shuffle(raw_items)
            else:
                raw_items = sorted(raw_items, key=lambda row: str(row[0]).lower())
            for label, raw_text in raw_items:
                if limit > 0 and added >= limit:
                    break
                source_label = str(label or "").strip()
                image_path = source_map.get(source_label.lower()) or source_map.get(Path(source_label).stem.lower())
                if image_path is None or not image_path.exists():
                    continue
                record_id = self._ocr_record_id(session_path=session_path, source_label=source_label, image_path=image_path)
                if record_id in existing:
                    continue
                image_name = f"{record_id}_{self._safe_file_stem(image_path.stem)}{image_path.suffix.lower() or '.png'}"
                copied_image = destination / "images" / image_name
                if not copied_image.exists():
                    shutil.copy2(image_path, copied_image)
                now = datetime.now().isoformat(timespec="seconds")
                record = {
                    "schema_version": "ocr_golden_live_v1",
                    "record_id": record_id,
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                    "book_code": str(payload.get("book_code") or ""),
                    "instance_type": str(payload.get("instance_type") or ""),
                    "session_json": str(session_path),
                    "source_label": source_label,
                    "source_path": str(image_path),
                    "copied_image_rel": str(copied_image.relative_to(destination)).replace("\\", "/"),
                    "ocr_text": str(raw_text or ""),
                    "corrected_text": "",
                    "notes": "",
                }
                (destination / "records" / f"{record_id}.json").write_text(
                    json.dumps(record, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                existing[record_id] = record
                added += 1
            if limit > 0 and added >= limit:
                break
        self._rewrite_ocr_golden_indexes(destination)
        return destination

    def import_problem_crops_into_ocr_golden(
        self,
        *,
        crops_root: Path | None = None,
        out_dir: Path | None = None,
        crop_ids: Iterable[str] | None = None,
        session_json: str = "",
        book_code: str = "",
        instance_type: str = "",
        project_name: str = "",
    ) -> tuple[Path, int]:
        destination = Path(out_dir or self.DEFAULT_OCR_GOLDEN_DIR).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "records").mkdir(parents=True, exist_ok=True)
        (destination / "images").mkdir(parents=True, exist_ok=True)
        added = 0
        for crop in self.load_problem_crops_live(crops_root, crop_ids=crop_ids):
            source_path = Path(crop.image_path)
            if not source_path.exists():
                continue
            record_id = hashlib.sha1(f"problem_crop|{crop.crop_id}".encode("utf-8")).hexdigest()[:16]
            now = datetime.now().isoformat(timespec="seconds")
            record_path = destination / "records" / f"{record_id}.json"
            crop_raw = crop.raw if isinstance(crop.raw, dict) else {}
            linked_session_json = str(crop_raw.get("session_json") or session_json or "").strip()
            linked_source_label = str(crop_raw.get("session_source_label") or crop.crop_id or "").strip()
            linked_book_code = str(crop_raw.get("book_code") or book_code or "").strip()
            linked_instance_type = str(crop_raw.get("instance_type") or instance_type or crop_raw.get("source_instance_full") or "").strip()
            linked_project_name = str(crop_raw.get("project_name") or project_name or "").strip()
            origin = {
                "type": "pdf_problem_crop",
                "crop_id": crop.crop_id,
                "source_pdf_path": crop.source_pdf_path,
                "source_page_number": crop.source_page_number,
                "source_record_id": crop.source_record_id,
                "bbox_px": crop.bbox_px,
                "layout_mode": crop.layout_mode,
                "session_json": linked_session_json,
                "session_source_label": linked_source_label,
                "book_code": linked_book_code,
                "instance_type": linked_instance_type,
                "project_name": linked_project_name,
            }
            if record_path.exists():
                record = self._load_json(record_path)
                if not isinstance(record, dict):
                    record = {}
                record.update(
                    {
                        "schema_version": "ocr_golden_live_v1",
                        "record_id": record_id,
                        "updated_at": now,
                        "book_code": linked_book_code or str(record.get("book_code") or ""),
                        "instance_type": linked_instance_type or str(record.get("instance_type") or ""),
                        "session_json": linked_session_json or str(record.get("session_json") or ""),
                        "source_label": linked_source_label or crop.crop_id,
                        "source_path": str(source_path),
                        "ocr_text": crop.ocr_text,
                        "corrected_text": crop.corrected_text,
                        "origin": origin,
                    }
                )
                if crop.corrected_text.strip():
                    record["status"] = "corrected"
                elif not record.get("status"):
                    record["status"] = "pending"
                record_path.write_text(
                    json.dumps(record, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self._sync_ocr_golden_row_to_session(record)
                continue
            copied_image = destination / "images" / f"{record_id}_{self._safe_file_stem(source_path.stem)}.png"
            if not copied_image.exists():
                shutil.copy2(source_path, copied_image)
            record = {
                "schema_version": "ocr_golden_live_v1",
                "record_id": record_id,
                "status": "pending",
                "created_at": now,
                "updated_at": now,
                "book_code": linked_book_code,
                "instance_type": linked_instance_type,
                "session_json": linked_session_json,
                "source_label": linked_source_label or crop.crop_id,
                "source_path": str(source_path),
                "copied_image_rel": str(copied_image.relative_to(destination)).replace("\\", "/"),
                "ocr_text": crop.ocr_text,
                "corrected_text": crop.corrected_text,
                "notes": "Importado desde boxes de problemas completos del Modulo 13.",
                "origin": origin,
            }
            record_path.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._sync_ocr_golden_row_to_session(record)
            added += 1
        self._rewrite_ocr_golden_indexes(destination)
        return destination, added

    def import_problem_crops_into_segment_golden(
        self,
        *,
        crops_root: Path | None = None,
        out_root: Path | None = None,
        crop_ids: Iterable[str] | None = None,
        progress_callback=None,
    ) -> tuple[Path, int, int, int]:
        from modulos.modulo0_transcriptor.segmentador_v2 import (
            DEFAULT_LIVE_GOLDEN_BASE_DIR,
            SegmentadorProblemasV2,
        )

        segment_root = Path(out_root or (self.DEFAULT_GOLDEN_ROOT / "problem_crop_figure_segments")).expanduser().resolve()
        segmentador = SegmentadorProblemasV2(segment_root)
        live_dir = Path(DEFAULT_LIVE_GOLDEN_BASE_DIR).expanduser().resolve()
        processed = 0
        positives = 0
        boxes_total = 0
        crops = self.load_problem_crops_live(crops_root, crop_ids=crop_ids)
        previous_defer = os.environ.get("SEGMENT_LIVE_GOLDEN_DEFER_INDEX")
        os.environ["SEGMENT_LIVE_GOLDEN_DEFER_INDEX"] = "1"
        try:
            for index, crop in enumerate(crops, start=1):
                image_path = Path(crop.image_path)
                if not image_path.exists():
                    continue
                record_id = segmentador._live_record_id(image_path)
                if (live_dir / "records" / f"{record_id}.json").exists():
                    continue
                segmentador.register_source_pending(image_path)
                processed += 1
                if progress_callback is not None:
                    progress_callback(index, len(crops), processed, positives, boxes_total)
        finally:
            if previous_defer is None:
                os.environ.pop("SEGMENT_LIVE_GOLDEN_DEFER_INDEX", None)
            else:
                os.environ["SEGMENT_LIVE_GOLDEN_DEFER_INDEX"] = previous_defer
            segmentador.rebuild_live_golden_indexes()
        return live_dir, processed, positives, boxes_total

    def run_segment_model_on_sources(
        self,
        source_paths: Iterable[Path | str],
        *,
        golden_dir: Path | None = None,
        out_root: Path | None = None,
        progress_callback=None,
    ) -> tuple[Path, int, int, int]:
        from modulos.modulo0_transcriptor.segmentador_v2 import (
            DEFAULT_LIVE_GOLDEN_BASE_DIR,
            SegmentadorProblemasV2,
        )

        unique_sources: list[Path] = []
        seen: set[str] = set()
        for raw_path in source_paths:
            try:
                path = Path(raw_path).expanduser().resolve()
            except Exception:
                continue
            key = str(path).lower()
            if key in seen or not path.exists() or not path.is_file():
                continue
            seen.add(key)
            unique_sources.append(path)

        segment_root = Path(out_root or (self.DEFAULT_GOLDEN_ROOT / "problem_crop_figure_segments")).expanduser().resolve()
        target_golden = Path(golden_dir or DEFAULT_LIVE_GOLDEN_BASE_DIR).expanduser().resolve()
        processed = 0
        positives = 0
        boxes_total = 0
        previous_defer = os.environ.get("SEGMENT_LIVE_GOLDEN_DEFER_INDEX")
        previous_live = os.environ.get("SEGMENT_LIVE_GOLDEN_BASE")
        os.environ["SEGMENT_LIVE_GOLDEN_DEFER_INDEX"] = "1"
        os.environ["SEGMENT_LIVE_GOLDEN_BASE"] = str(target_golden)
        segmentador = None
        try:
            segmentador = SegmentadorProblemasV2(segment_root)
            total = len(unique_sources)
            for index, image_path in enumerate(unique_sources, start=1):
                segments = segmentador.segmentar_con_modelo(image_path)
                processed += 1
                if segments:
                    positives += 1
                    boxes_total += len(segments)
                if progress_callback is not None:
                    progress_callback(index, total, processed, positives, boxes_total)
        finally:
            if previous_defer is None:
                os.environ.pop("SEGMENT_LIVE_GOLDEN_DEFER_INDEX", None)
            else:
                os.environ["SEGMENT_LIVE_GOLDEN_DEFER_INDEX"] = previous_defer
            if previous_live is None:
                os.environ.pop("SEGMENT_LIVE_GOLDEN_BASE", None)
            else:
                os.environ["SEGMENT_LIVE_GOLDEN_BASE"] = previous_live
            if segmentador is not None:
                os.environ["SEGMENT_LIVE_GOLDEN_BASE"] = str(target_golden)
                try:
                    target_golden = segmentador.rebuild_live_golden_indexes()
                finally:
                    if previous_live is None:
                        os.environ.pop("SEGMENT_LIVE_GOLDEN_BASE", None)
                    else:
                        os.environ["SEGMENT_LIVE_GOLDEN_BASE"] = previous_live
        return target_golden, processed, positives, boxes_total

    def load_ocr_golden_base(self, golden_dir: Path | None = None) -> list[OcrGoldenRecord]:
        target = Path(golden_dir or self.DEFAULT_OCR_GOLDEN_DIR).expanduser().resolve()
        rows = list(self._load_ocr_record_files(target).values())
        rows.sort(key=self._ocr_golden_sort_key)
        return [self._ocr_record_from_row(row, target) for row in rows]

    @staticmethod
    def _ocr_golden_sort_key(row: dict[str, Any]) -> tuple[int, int, str, str, str]:
        has_text = bool(str(row.get("corrected_text") or row.get("ocr_text") or "").strip())
        has_session = bool(str(row.get("session_json") or "").strip())
        return (
            0 if has_text else 1,
            0 if has_session else 1,
            str(row.get("book_code") or "~").lower(),
            str(row.get("instance_type") or "~").lower(),
            str(row.get("source_label") or "").lower(),
        )

    def classify_ocr_golden_base(self, golden_dir: Path | None = None) -> dict[str, int]:
        target = Path(golden_dir or self.DEFAULT_OCR_GOLDEN_DIR).expanduser().resolve()
        records_dir = target / "records"
        if not records_dir.exists():
            raise FileNotFoundError(f"No existe la carpeta de registros OCR: {records_dir}")
        counts: dict[str, int] = {}
        changed = 0
        for record_path in records_dir.glob("*.json"):
            try:
                row = json.loads(record_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            result = self.classify_training_section_from_fields(
                curso=str(row.get("curso") or ""),
                tema=str(row.get("tema") or ""),
                book_code=str(row.get("book_code") or ""),
                instance_type=str(row.get("instance_type") or ""),
                source_label=str(row.get("source_label") or ""),
                text=str(row.get("corrected_text") or row.get("ocr_text") or ""),
            )
            section = result["section"]
            counts[section] = counts.get(section, 0) + 1
            if (
                row.get("training_section") != section
                or row.get("training_section_confidence") != result["confidence"]
                or row.get("training_section_reason") != result["reason"]
            ):
                row["training_section"] = section
                row["training_section_confidence"] = result["confidence"]
                row["training_section_reason"] = result["reason"]
                row["updated_at"] = datetime.now().isoformat(timespec="seconds")
                record_path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
                changed += 1
        self._rewrite_ocr_golden_indexes(target)
        counts["__changed__"] = changed
        counts["__total__"] = sum(value for key, value in counts.items() if not key.startswith("__"))
        return counts

    def link_ocr_golden_records_to_sessions(self, golden_dir: Path | None = None) -> dict[str, int]:
        target = Path(golden_dir or self.DEFAULT_OCR_GOLDEN_DIR).expanduser().resolve()
        records = list(self._load_ocr_record_files(target).values())
        grouped: dict[str, list[dict[str, Any]]] = {}
        skipped = 0
        for row in records:
            session_path = self._resolve_ocr_session_json_from_row(row)
            if session_path is None:
                skipped += 1
                continue
            grouped.setdefault(str(session_path), []).append(row)
        linked = 0
        for raw_session_path, rows in grouped.items():
            linked += self._sync_ocr_golden_rows_to_session(Path(raw_session_path), rows)
        return {"linked": linked, "skipped": skipped, "total": len(records)}

    def pdf_instance_names_from_ocr_golden(
        self,
        golden_dir: Path | None = None,
        *,
        crops_root: Path | None = None,
    ) -> set[str]:
        """Devuelve las instancias PDF IA referenciadas por la Golden OCR actual."""
        target = Path(golden_dir or self.DEFAULT_OCR_GOLDEN_DIR).expanduser().resolve()
        crop_ids: set[str] = set()
        for row in self._load_ocr_record_files(target).values():
            origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
            if str(origin.get("type") or "").strip() != "pdf_problem_crop":
                continue
            crop_id = str(origin.get("crop_id") or "").strip()
            if crop_id:
                crop_ids.add(crop_id)
        if not crop_ids:
            return set()
        names: set[str] = set()
        for crop in self.load_problem_crops_live(crops_root, crop_ids=crop_ids):
            raw = crop.raw if isinstance(crop.raw, dict) else {}
            name = str(raw.get("source_instance_full") or raw.get("source_instance") or "").strip()
            if name:
                names.add(name)
        return names

    def save_ocr_correction(
        self,
        *,
        record_id: str,
        corrected_text: str,
        status: str = "corrected",
        notes: str = "",
        ocr_text: str | None = None,
        ocr_model_text: str | None = None,
        golden_dir: Path | None = None,
    ) -> None:
        target = Path(golden_dir or self.DEFAULT_OCR_GOLDEN_DIR).expanduser().resolve()
        record_path = target / "records" / f"{record_id}.json"
        if not record_path.exists():
            raise FileNotFoundError(f"No existe el registro OCR: {record_path}")
        row = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(row, dict):
            raise ValueError(f"Registro OCR invalido: {record_path}")
        if ocr_text is not None:
            row["ocr_text"] = _repair_mojibake_text(str(ocr_text or ""))
        if ocr_model_text is not None:
            row["ocr_model_text"] = _repair_mojibake_text(str(ocr_model_text or ""))
        row["corrected_text"] = _repair_mojibake_text(str(corrected_text or ""))
        row["status"] = str(status or "corrected")
        row["notes"] = _repair_mojibake_text(str(notes or ""))
        row["updated_at"] = datetime.now().isoformat(timespec="seconds")
        record_path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        self._sync_ocr_golden_row_to_session(row)
        self._rewrite_ocr_golden_indexes(target)

    def _sync_ocr_golden_row_to_session(self, row: dict[str, Any]) -> None:
        session_path = self._resolve_ocr_session_json_from_row(row)
        if session_path is None:
            return
        self._sync_ocr_golden_rows_to_session(session_path, [row])

    def _sync_ocr_golden_rows_to_session(self, session_path: Path, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        session_path = Path(session_path)
        if not session_path.exists():
            return 0
        try:
            payload = self._load_json(session_path)
        except Exception:
            return 0
        if not payload:
            return 0
        now = datetime.now().isoformat(timespec="seconds")
        ocr_links = payload.get("ocr_golden_by_label")
        if not isinstance(ocr_links, dict):
            ocr_links = {}
        raw_map = payload.get("ocr_raw_first_by_label")
        if not isinstance(raw_map, dict):
            raw_map = {}
        corrected_map = payload.get("ocr_corrected_by_label")
        if not isinstance(corrected_map, dict):
            corrected_map = {}
        linked = 0
        for row in rows:
            source_label = str(row.get("source_label") or "").strip()
            if not source_label:
                origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
                source_label = str(origin.get("session_source_label") or origin.get("crop_id") or "").strip()
            if not source_label:
                continue
            ocr_links[source_label] = {
                "record_id": str(row.get("record_id") or ""),
                "source_label": source_label,
                "status": str(row.get("status") or ""),
                "ocr_text": str(row.get("ocr_text") or ""),
                "corrected_text": str(row.get("corrected_text") or ""),
                "updated_at": str(row.get("updated_at") or now),
                "origin": row.get("origin") if isinstance(row.get("origin"), dict) else {},
            }
            if str(row.get("ocr_text") or "").strip():
                raw_map[source_label] = str(row.get("ocr_text") or "")
            if str(row.get("corrected_text") or "").strip():
                corrected_map[source_label] = str(row.get("corrected_text") or "")
            linked += 1
        if linked <= 0:
            return 0
        payload["ocr_golden_by_label"] = ocr_links
        payload["ocr_raw_first_by_label"] = raw_map
        if corrected_map:
            payload["ocr_corrected_by_label"] = corrected_map
        payload["updated_at"] = now
        try:
            self._write_json_atomic(session_path, payload)
        except Exception:
            return 0
        return linked

    def _sync_ocr_golden_row_to_session_legacy(self, row: dict[str, Any]) -> None:
        session_path = self._resolve_ocr_session_json_from_row(row)
        if session_path is None:
            return
        source_label = str(row.get("source_label") or "").strip()
        if not source_label:
            origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
            source_label = str(origin.get("session_source_label") or origin.get("crop_id") or "").strip()
        if not source_label:
            return
        try:
            payload = self._load_json(session_path)
        except Exception:
            return
        if not payload:
            return
        now = datetime.now().isoformat(timespec="seconds")
        ocr_links = payload.get("ocr_golden_by_label")
        if not isinstance(ocr_links, dict):
            ocr_links = {}
        ocr_links[source_label] = {
            "record_id": str(row.get("record_id") or ""),
            "source_label": source_label,
            "status": str(row.get("status") or ""),
            "ocr_text": str(row.get("ocr_text") or ""),
            "corrected_text": str(row.get("corrected_text") or ""),
            "updated_at": str(row.get("updated_at") or now),
            "origin": row.get("origin") if isinstance(row.get("origin"), dict) else {},
        }
        payload["ocr_golden_by_label"] = ocr_links
        raw_map = payload.get("ocr_raw_first_by_label")
        if not isinstance(raw_map, dict):
            raw_map = {}
        if str(row.get("ocr_text") or "").strip():
            raw_map[source_label] = str(row.get("ocr_text") or "")
            payload["ocr_raw_first_by_label"] = raw_map
        corrected_map = payload.get("ocr_corrected_by_label")
        if not isinstance(corrected_map, dict):
            corrected_map = {}
        if str(row.get("corrected_text") or "").strip():
            corrected_map[source_label] = str(row.get("corrected_text") or "")
            payload["ocr_corrected_by_label"] = corrected_map
        payload["updated_at"] = now
        try:
            self._write_json_atomic(session_path, payload)
        except Exception:
            return

    def _resolve_ocr_session_json_from_row(self, row: dict[str, Any]) -> Path | None:
        candidates: list[str] = []
        candidates.append(str(row.get("session_json") or ""))
        origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
        candidates.append(str(origin.get("session_json") or ""))
        for raw in candidates:
            text = str(raw or "").strip()
            if not text:
                continue
            path = Path(text).expanduser()
            if path.exists():
                return path.resolve()
        return None

    def upsert_ocr_golden_from_session_image(
        self,
        *,
        image_path: Path,
        source_label: str,
        ocr_text: str = "",
        corrected_text: str = "",
        book_code: str = "",
        instance_type: str = "",
        session_json: str = "",
        status: str = "",
        notes: str = "",
        golden_dir: Path | None = None,
    ) -> tuple[Path, str]:
        source_path = Path(image_path).expanduser().resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"No existe la imagen OCR: {source_path}")
        target = Path(golden_dir or self.DEFAULT_OCR_GOLDEN_DIR).expanduser().resolve()
        (target / "records").mkdir(parents=True, exist_ok=True)
        (target / "images").mkdir(parents=True, exist_ok=True)
        seed = f"session_image|{source_path}|{source_label}"
        record_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
        copied_image = target / "images" / f"{record_id}_{self._safe_file_stem(source_path.stem)}.png"
        if not copied_image.exists():
            shutil.copy2(source_path, copied_image)
        record_path = target / "records" / f"{record_id}.json"
        current = self._load_json(record_path) if record_path.exists() else {}
        now = datetime.now().isoformat(timespec="seconds")
        final_ocr = str(ocr_text if ocr_text is not None else current.get("ocr_text", "") or "")
        final_corrected = str(corrected_text if corrected_text is not None else current.get("corrected_text", "") or "")
        final_status = str(status or current.get("status") or ("corrected" if final_corrected.strip() else "pending"))
        record = {
            **current,
            "schema_version": "ocr_golden_live_v1",
            "record_id": record_id,
            "status": final_status,
            "created_at": str(current.get("created_at") or now),
            "updated_at": now,
            "book_code": str(book_code or current.get("book_code", "") or ""),
            "instance_type": str(instance_type or current.get("instance_type", "") or ""),
            "session_json": str(session_json or current.get("session_json", "") or ""),
            "source_label": str(source_label or current.get("source_label", "") or source_path.stem),
            "source_path": str(source_path),
            "copied_image_rel": str(copied_image.relative_to(target)).replace("\\", "/"),
            "ocr_text": final_ocr,
            "ocr_model_text": str(current.get("ocr_model_text") or final_ocr),
            "corrected_text": final_corrected,
            "notes": str(notes or current.get("notes", "") or "Registrado automaticamente desde Modulo 0."),
            "origin": {
                "type": "modulo0_session_image",
                "source_label": str(source_label or ""),
                "source_path": str(source_path),
            },
        }
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        self._sync_ocr_golden_row_to_session(record)
        self._rewrite_ocr_golden_indexes(target)
        return target, record_id

    def build_ocr_normalization_golden_base(
        self,
        *,
        ocr_golden_dir: Path | None = None,
        out_dir: Path | None = None,
    ) -> Path:
        source_dir = Path(ocr_golden_dir or self.DEFAULT_OCR_GOLDEN_DIR).expanduser().resolve()
        target = Path(out_dir or self.DEFAULT_OCR_NORMALIZATION_GOLDEN_DIR).expanduser().resolve()
        records_dir = target / "records"
        records_dir.mkdir(parents=True, exist_ok=True)
        existing = self._load_normalization_record_files(target)
        for source in self.load_ocr_golden_base(source_dir):
            raw_ocr = str(source.raw.get("ocr_model_text") or source.ocr_text or "").strip()
            normalized = str(source.corrected_text or "").strip()
            if not raw_ocr or not normalized:
                continue
            current = existing.get(source.record_id, {})
            row = {
                "schema_version": "ocr_normalization_golden_v1",
                "record_id": source.record_id,
                "source_ocr_record_id": source.record_id,
                "source_label": source.source_label,
                "book_code": source.book_code,
                "instance_type": source.instance_type,
                "raw_ocr": raw_ocr,
                "normalized_text": str(current.get("normalized_text") or normalized),
                "status": str(current.get("status") or "pending"),
                "notes": str(current.get("notes") or ""),
                "updated_at": str(current.get("updated_at") or datetime.now().isoformat(timespec="seconds")),
            }
            (records_dir / f"{source.record_id}.json").write_text(
                json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        self._rewrite_normalization_golden_indexes(target)
        return target

    def load_ocr_normalization_golden_base(
        self, golden_dir: Path | None = None
    ) -> list[OcrNormalizationGoldenRecord]:
        target = Path(golden_dir or self.DEFAULT_OCR_NORMALIZATION_GOLDEN_DIR).expanduser().resolve()
        rows = list(self._load_normalization_record_files(target).values())
        rows.sort(key=lambda row: (str(row.get("status", "")), str(row.get("source_label", ""))))
        return [
            OcrNormalizationGoldenRecord(
                record_id=str(row.get("record_id") or ""),
                status=str(row.get("status") or "pending"),
                source_ocr_record_id=str(row.get("source_ocr_record_id") or ""),
                source_label=str(row.get("source_label") or ""),
                book_code=str(row.get("book_code") or ""),
                instance_type=str(row.get("instance_type") or ""),
                raw_ocr=str(row.get("raw_ocr") or ""),
                normalized_text=str(row.get("normalized_text") or ""),
                notes=str(row.get("notes") or ""),
                updated_at=str(row.get("updated_at") or ""),
            )
            for row in rows
        ]

    def save_ocr_normalization_review(
        self,
        *,
        record_id: str,
        normalized_text: str,
        status: str,
        notes: str = "",
        golden_dir: Path | None = None,
    ) -> None:
        target = Path(golden_dir or self.DEFAULT_OCR_NORMALIZATION_GOLDEN_DIR).expanduser().resolve()
        record_path = target / "records" / f"{record_id}.json"
        if not record_path.exists():
            raise FileNotFoundError(f"No existe el registro de normalizacion: {record_path}")
        row = json.loads(record_path.read_text(encoding="utf-8"))
        row["normalized_text"] = str(normalized_text or "")
        row["status"] = str(status or "pending")
        row["notes"] = str(notes or "")
        row["updated_at"] = datetime.now().isoformat(timespec="seconds")
        record_path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        self._rewrite_normalization_golden_indexes(target)

    def prepare_ocr_normalization_dataset(
        self,
        *,
        golden_dir: Path | None = None,
        out_root: Path | None = None,
    ) -> Path:
        from tools.prepare_ocr_normalization_dataset import export_confirmed_records

        source = Path(golden_dir or self.DEFAULT_OCR_NORMALIZATION_GOLDEN_DIR).expanduser().resolve()
        root = Path(out_root or self.DEFAULT_GOLDEN_ROOT).expanduser().resolve()
        out_dir = root / f"ocr_normalization_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        export_confirmed_records(source, out_dir)
        return out_dir

    def _load_normalization_record_files(self, golden_dir: Path) -> dict[str, dict[str, Any]]:
        records_dir = Path(golden_dir) / "records"
        rows: dict[str, dict[str, Any]] = {}
        if not records_dir.exists():
            return rows
        for path in sorted(records_dir.glob("*.json")):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            record_id = str(row.get("record_id") or path.stem).strip()
            if isinstance(row, dict) and record_id:
                rows[record_id] = row
        return rows

    def _rewrite_normalization_golden_indexes(self, golden_dir: Path) -> None:
        golden_dir.mkdir(parents=True, exist_ok=True)
        rows = list(self._load_normalization_record_files(golden_dir).values())
        rows.sort(key=lambda row: (str(row.get("status", "")), str(row.get("source_label", ""))))
        confirmed = [row for row in rows if str(row.get("status") or "") == "confirmed"]
        (golden_dir / "records_all.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8"
        )
        (golden_dir / "records_confirmed.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in confirmed) + ("\n" if confirmed else ""),
            encoding="utf-8",
        )
        manifest = {
            "schema_version": "ocr_normalization_golden_index_v1",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "records_total": len(rows),
            "records_confirmed": len(confirmed),
            "records_pending": sum(1 for row in rows if str(row.get("status") or "pending") == "pending"),
            "records_excluded": sum(1 for row in rows if str(row.get("status") or "") == "excluded"),
        }
        (golden_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def summarize_segment_records(self, records: Iterable[SegmentGoldenRecord]) -> dict[str, int]:
        summary = {
            "total": 0,
            "confirmados": 0,
            "sin_vinculo": 0,
            "imagenes_faltantes": 0,
            "libros": 0,
        }
        books: set[str] = set()
        for record in records:
            summary["total"] += 1
            if record.binding_confirmed:
                summary["confirmados"] += 1
            else:
                summary["sin_vinculo"] += 1
            if record.book_code:
                books.add(record.book_code)
            image_path = Path(record.display_image_path) if record.display_image_path else Path("")
            if not record.display_image_path or not image_path.exists():
                summary["imagenes_faltantes"] += 1
        summary["libros"] = len(books)
        return summary

    def prepare_huggingface_yolo_dataset(
        self,
        *,
        golden_dir: Path,
        out_root: Path | None = None,
    ) -> Path:
        """Convierte una golden base revisada a dataset YOLO listo para HF/Ultralytics."""
        golden_dir = Path(golden_dir).expanduser().resolve()
        live_source_records = self._load_live_source_records(golden_dir)
        if live_source_records:
            return self._prepare_hf_yolo_from_source_records(
                golden_dir=golden_dir,
                source_records=live_source_records,
                out_root=out_root,
            )
        records = self.load_segment_golden_base(golden_dir)
        if not records:
            raise ValueError("La golden base no tiene segmentos para exportar.")
        destination = Path(out_root or self.DEFAULT_GOLDEN_ROOT).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        out_dir = destination / f"hf_yolo_segmentacion_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        images_root = out_dir / "images"
        labels_root = out_dir / "labels"
        for split in ("train", "val", "test"):
            (images_root / split).mkdir(parents=True, exist_ok=True)
            (labels_root / split).mkdir(parents=True, exist_ok=True)

        grouped: dict[str, list[SegmentGoldenRecord]] = {}
        for record in records:
            if not record.source_path or not record.segment_bbox_px:
                continue
            source = Path(record.source_path)
            if not source.exists():
                continue
            grouped.setdefault(str(source.resolve()), []).append(record)
        if not grouped:
            raise ValueError("No hay imagenes fuente con boxes validos en esta golden base.")

        rows: list[dict[str, Any]] = []
        split_counts = {key: {"images": 0, "boxes": 0} for key in ("train", "val", "test")}
        for source_key, source_records in sorted(grouped.items()):
            source_path = Path(source_key)
            split = self._stable_split(source_key)
            image_name = self._safe_file_stem(source_path.stem) + source_path.suffix.lower()
            dst_image = images_root / split / image_name
            dst_label = labels_root / split / f"{Path(image_name).stem}.txt"
            if not dst_image.exists():
                shutil.copy2(source_path, dst_image)
            width, height = self._image_size(source_path)
            label_lines: list[str] = []
            source_boxes: list[list[int]] = []
            if width > 0 and height > 0:
                for record in source_records:
                    line, box = self._yolo_label_line(record.segment_bbox_px, width=width, height=height)
                    if line:
                        label_lines.append(line)
                        source_boxes.append(box)
            dst_label.write_text("\n".join(label_lines), encoding="utf-8")
            split_counts[split]["images"] += 1
            split_counts[split]["boxes"] += len(label_lines)
            rows.append(
                {
                    "image": str(dst_image.relative_to(out_dir)).replace("\\", "/"),
                    "label": str(dst_label.relative_to(out_dir)).replace("\\", "/"),
                    "split": split,
                    "width": width,
                    "height": height,
                    "boxes_total": len(label_lines),
                    "boxes_px": source_boxes,
                    "source_path": str(source_path),
                    "source_stem": source_path.stem,
                    "golden_base": str(golden_dir),
                }
            )

        (out_dir / "dataset.yaml").write_text(
            "\n".join(
                [
                    "path: .",
                    "train: images/train",
                    "val: images/val",
                    "test: images/test",
                    "names:",
                    "  0: problema_segmentado",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (out_dir / "classes.txt").write_text("problema_segmentado\n", encoding="utf-8")
        (out_dir / "samples.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
            encoding="utf-8",
        )
        manifest = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "dataset_kind": "hf_yolo_problem_segmentation",
            "golden_base": str(golden_dir),
            "class_name": "problema_segmentado",
            "images_total": len(rows),
            "boxes_total": sum(row["boxes_total"] for row in rows),
            "splits": split_counts,
            "files": {
                "dataset_yaml": "dataset.yaml",
                "classes_txt": "classes.txt",
                "samples_jsonl": "samples.jsonl",
            },
            "train_command": "yolo detect train data=dataset.yaml model=yolov8n.pt imgsz=1024 epochs=100 batch=8",
        }
        (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "README.md").write_text(
            "\n".join(
                [
                    "# Dataset YOLO para segmentacion de problemas",
                    "",
                    "Este dataset fue preparado desde la golden base revisada del Modulo 12.",
                    "",
                    "## Estructura",
                    "- `images/train|val|test`: imagenes fuente completas.",
                    "- `labels/train|val|test`: boxes YOLO normalizados.",
                    "- `dataset.yaml`: configuracion para Ultralytics.",
                    "",
                    "## Entrenamiento sugerido",
                    "```bash",
                    "yolo detect train data=dataset.yaml model=yolov8n.pt imgsz=1024 epochs=100 batch=8",
                    "```",
                ]
            ),
            encoding="utf-8",
        )
        return out_dir

    def _load_live_source_records(self, golden_dir: Path) -> list[dict[str, Any]]:
        records_path = Path(golden_dir) / "source_records_all.jsonl"
        if not records_path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with records_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(payload, dict):
                        rows.append(payload)
        except Exception:
            return []
        return rows

    def _prepare_hf_yolo_from_source_records(
        self,
        *,
        golden_dir: Path,
        source_records: list[dict[str, Any]],
        out_root: Path | None = None,
    ) -> Path:
        destination = Path(out_root or self.DEFAULT_GOLDEN_ROOT).expanduser().resolve()
        destination.mkdir(parents=True, exist_ok=True)
        out_dir = destination / f"hf_yolo_segmentacion_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        images_root = out_dir / "images"
        labels_root = out_dir / "labels"
        for split in ("train", "val", "test"):
            (images_root / split).mkdir(parents=True, exist_ok=True)
            (labels_root / split).mkdir(parents=True, exist_ok=True)

        rows: list[dict[str, Any]] = []
        split_counts = {key: {"images": 0, "boxes": 0} for key in ("train", "val", "test")}
        for record in source_records:
            source_path = self._resolve_live_source_path(golden_dir, record)
            if not source_path.exists():
                continue
            record_id = str(record.get("record_id") or source_path.stem).strip() or source_path.stem
            split = self._stable_split(record_id)
            image_name = self._safe_file_stem(f"{record_id}_{source_path.stem}") + source_path.suffix.lower()
            dst_image = images_root / split / image_name
            dst_label = labels_root / split / f"{Path(image_name).stem}.txt"
            if not dst_image.exists():
                shutil.copy2(source_path, dst_image)

            width, height = self._image_size(source_path)
            label_lines: list[str] = []
            source_boxes: list[list[int]] = []
            raw_boxes = record.get("boxes_px", [])
            if isinstance(raw_boxes, list) and width > 0 and height > 0:
                for raw_box in raw_boxes:
                    line, box = self._yolo_label_line(raw_box, width=width, height=height)
                    if line:
                        label_lines.append(line)
                        source_boxes.append(box)
            dst_label.write_text("\n".join(label_lines), encoding="utf-8")
            split_counts[split]["images"] += 1
            split_counts[split]["boxes"] += len(label_lines)
            rows.append(
                {
                    "image": str(dst_image.relative_to(out_dir)).replace("\\", "/"),
                    "label": str(dst_label.relative_to(out_dir)).replace("\\", "/"),
                    "split": split,
                    "width": width,
                    "height": height,
                    "boxes_total": len(label_lines),
                    "boxes_px": source_boxes,
                    "source_path": str(source_path),
                    "source_stem": source_path.stem,
                    "record_id": record_id,
                    "golden_base": str(golden_dir),
                    "source_record_kind": str(record.get("schema_version") or "segment_training_live_source_v1"),
                }
            )
        if not rows:
            raise ValueError("La base viva no tiene imagenes fuente disponibles para exportar.")

        (out_dir / "dataset.yaml").write_text(
            "\n".join(
                [
                    "path: .",
                    "train: images/train",
                    "val: images/val",
                    "test: images/test",
                    "names:",
                    "  0: problema_segmentado",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (out_dir / "classes.txt").write_text("problema_segmentado\n", encoding="utf-8")
        (out_dir / "samples.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
        manifest = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "dataset_kind": "hf_yolo_problem_segmentation_live_sources",
            "golden_base": str(golden_dir),
            "class_name": "problema_segmentado",
            "images_total": len(rows),
            "images_positive": sum(1 for row in rows if int(row.get("boxes_total", 0) or 0) > 0),
            "images_negative": sum(1 for row in rows if int(row.get("boxes_total", 0) or 0) == 0),
            "boxes_total": sum(int(row["boxes_total"]) for row in rows),
            "splits": split_counts,
            "files": {
                "dataset_yaml": "dataset.yaml",
                "classes_txt": "classes.txt",
                "samples_jsonl": "samples.jsonl",
            },
            "notes": [
                "Exportado desde source_records_all.jsonl.",
                "Las imagenes sin detecciones se exportan con archivo label vacio para entrenamiento YOLO.",
            ],
        }
        (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (out_dir / "README.md").write_text(
            "\n".join(
                [
                    "# Dataset YOLO para segmentacion de problemas",
                    "",
                    "Este dataset fue preparado desde la golden base viva incremental.",
                    "",
                    "Incluye imagenes positivas con boxes e imagenes negativas con labels vacios.",
                ]
            ),
            encoding="utf-8",
        )
        return out_dir

    def _resolve_live_source_path(self, golden_dir: Path, record: dict[str, Any]) -> Path:
        copied_rel = str(record.get("source_image_rel") or "").strip()
        if copied_rel:
            candidate = (golden_dir / copied_rel).resolve()
            if candidate.exists():
                return candidate
        raw_source = str(record.get("source_path") or "").strip()
        if raw_source:
            return Path(raw_source).expanduser().resolve()
        return Path("")

    def _iter_session_files(self, roots: Iterable[Path]) -> Iterable[Path]:
        seen: set[str] = set()
        for root in roots:
            root = Path(root)
            if root.is_file() and root.name.endswith(".session.json"):
                candidates = [root]
            elif root.exists():
                candidates = sorted(root.rglob("*.session.json"))
            else:
                candidates = []
            for path in candidates:
                if path.name.endswith(".session__images.json"):
                    continue
                key = str(path.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                yield path

    def _resolve_session_source_map(self, session_path: Path, payload: dict[str, Any]) -> dict[str, Path]:
        out: dict[str, Path] = {}
        bundle = payload.get("session_bundle") if isinstance(payload.get("session_bundle"), dict) else {}
        roots: list[Path] = [session_path.parent]
        for key in ("sources_dir", "root"):
            raw = str(bundle.get(key) or "").strip()
            if raw:
                candidate = Path(raw)
                roots.append(candidate if candidate.is_absolute() else (session_path.parent / candidate).resolve())
        for source in payload.get("source_images", []) if isinstance(payload.get("source_images"), list) else []:
            if not isinstance(source, dict):
                continue
            raw_path = str(source.get("path") or source.get("source_path") or "").strip()
            if raw_path:
                path = Path(raw_path)
                if not path.is_absolute():
                    path = (session_path.parent / path).resolve()
                if path.exists():
                    out[path.name.lower()] = path
                    out[path.stem.lower()] = path
        for root in roots:
            try:
                if not root.exists() or not root.is_dir():
                    continue
                for path in root.iterdir():
                    if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                        out[path.name.lower()] = path
                        out[path.stem.lower()] = path
            except Exception:
                continue
        return out

    def _ocr_record_id(self, *, session_path: Path, source_label: str, image_path: Path) -> str:
        key = f"{session_path.resolve()}|{source_label}|{image_path.resolve()}".lower()
        return hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _load_ocr_record_files(self, golden_dir: Path) -> dict[str, dict[str, Any]]:
        records_dir = Path(golden_dir) / "records"
        rows: dict[str, dict[str, Any]] = {}
        if not records_dir.exists():
            return rows
        for path in sorted(records_dir.glob("*.json")):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            record_id = str(row.get("record_id") or path.stem).strip()
            if record_id:
                rows[record_id] = row
        return rows

    def _rewrite_ocr_golden_indexes(self, golden_dir: Path) -> None:
        golden_dir.mkdir(parents=True, exist_ok=True)
        rows = list(self._load_ocr_record_files(golden_dir).values())
        rows.sort(key=lambda row: (str(row.get("status", "")), str(row.get("book_code", "")), str(row.get("source_label", ""))))
        corrected_rows = [row for row in rows if str(row.get("corrected_text") or "").strip()]
        (golden_dir / "records_all.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
            encoding="utf-8",
        )
        (golden_dir / "records_corrected.jsonl").write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in corrected_rows) + ("\n" if corrected_rows else ""),
            encoding="utf-8",
        )
        manifest = {
            "schema_version": "ocr_golden_live_index_v1",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "records_total": len(rows),
            "records_corrected": len(corrected_rows),
            "records_pending": len(rows) - len(corrected_rows),
            "files": {
                "records_all": "records_all.jsonl",
                "records_corrected": "records_corrected.jsonl",
                "records_dir": "records",
                "images_dir": "images",
            },
        }
        (golden_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def _ocr_record_from_row(self, raw: dict[str, Any], golden_dir: Path) -> OcrGoldenRecord:
        copied_rel = str(raw.get("copied_image_rel") or "").strip()
        copied_abs = str((golden_dir / copied_rel).resolve()) if copied_rel else ""
        return OcrGoldenRecord(
            record_id=str(raw.get("record_id") or ""),
            status=str(raw.get("status") or "pending"),
            book_code=str(raw.get("book_code") or ""),
            instance_type=str(raw.get("instance_type") or ""),
            session_json=str(raw.get("session_json") or ""),
            source_label=str(raw.get("source_label") or ""),
            image_path=str(raw.get("source_path") or ""),
            copied_image_path=copied_abs,
            ocr_text=str(raw.get("ocr_text") or ""),
            corrected_text=str(raw.get("corrected_text") or ""),
            notes=str(raw.get("notes") or ""),
            updated_at=str(raw.get("updated_at") or ""),
            training_section=str(raw.get("training_section") or ""),
            training_section_confidence=str(raw.get("training_section_confidence") or ""),
            training_section_reason=str(raw.get("training_section_reason") or ""),
            raw=raw,
        )

    def _looks_like_session(self, path: Path) -> bool:
        try:
            payload = self._load_json(path)
        except Exception:
            return False
        if not isinstance(payload, dict):
            return False
        if "session_schema_version" in payload:
            return True
        return any(key in payload for key in ("training_pairs_by_item", "source_images", "ocr_structured_by_label"))

    def _load_json(self, path: Path) -> dict[str, Any]:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8-sig")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            raw, _end = json.JSONDecoder().raw_decode(text.lstrip())
        return raw if isinstance(raw, dict) else {}

    def _write_json_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target.with_name(f"{target.name}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(target)

    def _golden_base_has_records(self, golden_dir: Path) -> bool:
        for file_name in ("records_all.jsonl", "source_records_all.jsonl"):
            records_path = Path(golden_dir) / file_name
            if not records_path.exists():
                continue
            try:
                with records_path.open("r", encoding="utf-8") as handle:
                    if any(bool(line.strip()) for line in handle):
                        return True
            except Exception:
                continue
        return False

    def _record_from_golden_row(self, raw: dict[str, Any], golden_dir: Path) -> SegmentGoldenRecord:
        copied_rel = str(raw.get("copied_image_rel") or "").strip()
        copied_abs = str((golden_dir / copied_rel).resolve()) if copied_rel else ""
        segment_image = str(raw.get("segment_image_path") or "").strip()
        try:
            segment_idx = int(raw.get("segment_idx")) if raw.get("segment_idx") not in (None, "") else None
        except Exception:
            segment_idx = None
        try:
            item_num = int(raw.get("item_num")) if raw.get("item_num") not in (None, "") else None
        except Exception:
            item_num = None
        raw_bbox = raw.get("segment_bbox_px")
        bbox: list[float] = []
        if isinstance(raw_bbox, list) and len(raw_bbox) >= 4:
            try:
                bbox = [float(value) for value in raw_bbox[:4]]
            except Exception:
                bbox = []
        return SegmentGoldenRecord(
            record_id=str(raw.get("record_id") or ""),
            split=str(raw.get("split") or ""),
            book_code=str(raw.get("book_code") or ""),
            instance_type=str(raw.get("instance_type") or ""),
            source_stem=str(raw.get("source_stem") or ""),
            segment_idx=segment_idx,
            segment_bbox_px=bbox,
            source_path=str(raw.get("source_path") or ""),
            item_num=item_num,
            slot=str(raw.get("slot") or ""),
            marker_name=str(raw.get("marker_name") or ""),
            binding_confirmed=bool(raw.get("binding_confirmed")),
            binding_status=str(raw.get("binding_status") or ""),
            segment_image_path=segment_image,
            copied_image_path=copied_abs,
            session_json=str(raw.get("session_json") or ""),
            curso=str(raw.get("curso") or ""),
            tema=str(raw.get("tema") or ""),
            debug_statement=str(raw.get("debug_statement") or ""),
            item_text=str(raw.get("item_text") or ""),
            raw=raw,
        )

    def _stable_split(self, key: str) -> str:
        import hashlib

        raw = int(hashlib.sha1(str(key).encode("utf-8")).hexdigest()[:8], 16) % 100
        if raw < 80:
            return "train"
        if raw < 90:
            return "val"
        return "test"

    def _safe_file_stem(self, value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in str(value or "source"))
        return cleaned.strip("._") or "source"

    def _image_size(self, path: Path) -> tuple[int, int]:
        try:
            from PIL import Image

            with Image.open(path) as img:
                return int(img.size[0]), int(img.size[1])
        except Exception:
            return 0, 0

    def _yolo_label_line(self, bbox: list[float], *, width: int, height: int) -> tuple[str, list[int]]:
        if len(bbox) < 4 or width <= 0 or height <= 0:
            return "", []
        x1, y1, x2, y2 = [int(round(float(v))) for v in bbox[:4]]
        x1 = max(0, min(width - 1, min(x1, x2)))
        y1 = max(0, min(height - 1, min(y1, y2)))
        x2 = max(x1 + 1, min(width, max(x1, x2)))
        y2 = max(y1 + 1, min(height, max(y1, y2)))
        bw = (x2 - x1) / float(width)
        bh = (y2 - y1) / float(height)
        cx = (x1 + x2) / 2.0 / float(width)
        cy = (y1 + y2) / 2.0 / float(height)
        return f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}", [x1, y1, x2, y2]

    def _collect_sources(self, payload: dict[str, Any]) -> list[str]:
        sources: list[str] = []
        seen: set[str] = set()

        def add(raw: object) -> None:
            value = str(raw or "").strip()
            if not value:
                return
            key = value.lower()
            if key in seen:
                return
            seen.add(key)
            sources.append(value)

        for container_key in ("source_images", "files"):
            raw_list = payload.get(container_key)
            if not isinstance(raw_list, list):
                continue
            for entry in raw_list:
                if isinstance(entry, dict):
                    add(entry.get("path") or entry.get("source_path"))

        for raw_map_key in ("ocr_raw_first_by_label", "ocr_structured_by_label"):
            raw_map = payload.get(raw_map_key)
            if isinstance(raw_map, dict):
                for value in raw_map.keys():
                    if str(value or "").lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
                        add(value)
        return sources

    def _count_corrected_items(self, payload: dict[str, Any], items: list[Any]) -> int:
        corrected = 0
        for item in items:
            if isinstance(item, dict) and bool(item.get("corrected")):
                corrected += 1
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        raw_corrected = metadata.get("corrected_items", [])
        if isinstance(raw_corrected, list):
            corrected = max(corrected, len({str(v) for v in raw_corrected if str(v).strip()}))
        return corrected

    def _count_image_bindings(self, items: list[Any]) -> tuple[int, int]:
        confirmed = 0
        review = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            binding = item.get("image_binding") if isinstance(item.get("image_binding"), dict) else {}
            status = str(binding.get("status") or "").strip().lower()
            if "confirm" in status or "manual" in status:
                confirmed += 1
            if bool(binding.get("needs_review")) or "review" in status:
                review += 1
        return confirmed, review

    def _count_segmentation_boxes(self, payload: dict[str, Any]) -> tuple[int, int]:
        sources = 0
        boxes = 0
        segmentation = payload.get("segmentation") if isinstance(payload.get("segmentation"), dict) else {}
        overrides = segmentation.get("overrides") if isinstance(segmentation.get("overrides"), dict) else {}
        for raw_boxes in overrides.values():
            if not isinstance(raw_boxes, list):
                continue
            valid = sum(1 for raw_box in raw_boxes if self._is_box(raw_box))
            if valid:
                sources += 1
                boxes += valid

        for source in payload.get("source_images", []) if isinstance(payload.get("source_images"), list) else []:
            if not isinstance(source, dict):
                continue
            segments = source.get("segments")
            if not isinstance(segments, list):
                continue
            valid = sum(1 for seg in segments if isinstance(seg, dict) and self._is_box(seg.get("bbox_px")))
            if valid:
                sources += 1
                boxes += valid
        return boxes, sources

    def _count_manifest_segments(self, session_path: Path, payload: dict[str, Any]) -> int:
        roots: list[Path] = []
        session_dir = Path(session_path).parent
        roots.append(session_dir)
        ui = payload.get("ui") if isinstance(payload.get("ui"), dict) else {}
        for key in ("segments_dir", "workspace_dir"):
            value = str(ui.get(key) or payload.get(key) or "").strip()
            if value:
                roots.append(Path(value))

        total = 0
        seen: set[str] = set()
        for root in roots:
            try:
                root = root.expanduser()
                if not root.exists():
                    continue
                for manifest_path in root.rglob("segments_manifest.json"):
                    key = str(manifest_path.resolve()).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    segments = manifest.get("segments")
                    if isinstance(segments, list):
                        total += len(segments)
            except Exception:
                continue
        return total

    def _count_training_pairs(self, payload: dict[str, Any], session_path: Path) -> tuple[int, int, int]:
        pairs = payload.get("training_pairs_by_item")
        if not isinstance(pairs, dict):
            return (0, 0, 0)
        total = 0
        ready = 0
        missing_image = 0
        for pair in pairs.values():
            if not isinstance(pair, dict):
                continue
            total += 1
            completion = str(pair.get("human_final_output") or "").strip()
            metadata = pair.get("metadata") if isinstance(pair.get("metadata"), dict) else {}
            image_paths = metadata.get("human_image_paths")
            first_image = ""
            if isinstance(image_paths, list):
                first_image = next((str(v or "").strip() for v in image_paths if str(v or "").strip()), "")
            first_image = first_image or str(metadata.get("source_path") or "").strip()
            image_ok = bool(first_image) and self._path_exists(first_image, session_path)
            if completion and image_ok:
                ready += 1
            if completion and not image_ok:
                missing_image += 1
        return (total, ready, missing_image)

    def _count_dict(self, value: object) -> int:
        return len(value) if isinstance(value, dict) else 0

    def _path_exists(self, raw_path: str, session_path: Path) -> bool:
        raw_text = str(raw_path or "").strip()
        if not raw_text:
            return False
        candidate = Path(raw_text)
        candidates = [candidate]
        if not candidate.is_absolute():
            candidates.append(session_path.parent / candidate)
        return any(path.exists() for path in candidates)

    def _is_box(self, raw: object) -> bool:
        if not isinstance(raw, (list, tuple)) or len(raw) < 4:
            return False
        try:
            x1, y1, x2, y2 = [float(v) for v in raw[:4]]
        except Exception:
            return False
        return x2 > x1 and y2 > y1

    def _add_issues(self, audit: SessionTrainingAudit) -> None:
        def issue(level: str, category: str, message: str) -> None:
            audit.issues.append(TrainingIssue(level=level, category=category, message=message))

        if audit.source_images == 0:
            issue("error", "fuentes", "La sesion no registra imagenes fuente.")
        elif audit.missing_source_images:
            issue("error", "fuentes", f"Hay {audit.missing_source_images} imagen(es) fuente faltantes.")

        if audit.items == 0:
            issue("error", "ocr", "No hay items finales guardados.")
        if audit.training_pairs_ready == 0:
            issue("warning", "ocr", "No hay pares OCR humanos listos para entrenamiento.")
        if audit.training_pairs_missing_image:
            issue("error", "ocr", f"{audit.training_pairs_missing_image} par(es) OCR tienen texto humano pero imagen faltante.")
        if audit.ocr_raw_blocks and audit.ocr_structured_blocks == 0:
            issue("warning", "ocr", "Hay OCR bruto, pero no salida JSON estructurada.")

        if audit.segment_boxes == 0 and audit.manifest_segments == 0:
            issue("warning", "segmentacion", "No se encontraron segmentos/cajas para entrenar segmentacion.")
        if audit.image_bindings_review:
            issue("warning", "segmentacion", f"{audit.image_bindings_review} item(s) tienen vinculacion de imagen pendiente.")
        if audit.segment_boxes and audit.image_bindings_confirmed == 0:
            issue("warning", "segmentacion", "Hay segmentos, pero no hay vinculaciones confirmadas con items.")

    def _score_segmentation(self, audit: SessionTrainingAudit) -> int:
        score = 100
        if audit.source_images == 0:
            score -= 45
        score -= min(35, audit.missing_source_images * 12)
        if audit.segment_boxes == 0 and audit.manifest_segments == 0:
            score -= 35
        if audit.image_bindings_confirmed == 0:
            score -= 20
        score -= min(25, audit.image_bindings_review * 5)
        return max(0, min(100, score))

    def _score_ocr(self, audit: SessionTrainingAudit) -> int:
        score = 100
        if audit.items == 0:
            score -= 45
        if audit.training_pairs_ready == 0:
            score -= 35
        elif audit.training_pairs_ready < max(3, audit.items // 3):
            score -= 15
        score -= min(35, audit.training_pairs_missing_image * 12)
        if audit.ocr_raw_blocks and audit.ocr_structured_blocks == 0:
            score -= 15
        return max(0, min(100, score))
