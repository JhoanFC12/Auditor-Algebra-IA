from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

try:
    from modulos.modulo0_transcriptor.scan_pipeline.extractor import TRAINED_OCR_VISION_MODEL
except Exception:
    TRAINED_OCR_VISION_MODEL = "Jhoan12/math-ocr-qwen2.5-vl-3b-geometry-rules-merged-v4"

try:
    from modulos.modulo0_transcriptor.segmentador_v2 import (
        DEFAULT_YOLO_FIGURE_SEGMENT_MODEL_LOCAL,
        DEFAULT_YOLO_FIGURE_SEGMENT_MODEL_REPO,
    )
except Exception:
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    DEFAULT_YOLO_FIGURE_SEGMENT_MODEL_LOCAL = (
        _REPO_ROOT / "models" / "problem_segmentation_yolov8n_golden_v1" / "weights" / "best.pt"
    )
    DEFAULT_YOLO_FIGURE_SEGMENT_MODEL_REPO = "Jhoan12/problem-segmentation-yolov8n-golden-v1"

try:
    from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import (
        DEFAULT_LOCAL_MODEL_PATH,
        DEFAULT_MODEL_REPO_ID,
    )
except Exception:
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    DEFAULT_LOCAL_MODEL_PATH = _REPO_ROOT / "models" / "pdf_problem_detector_yolov8n_v4" / "weights" / "best.pt"
    DEFAULT_MODEL_REPO_ID = "Jhoan12/pdf-problem-detector-yolov8n-v4"

from .models import ModelDefaults, ModelStageTrace


NORMALIZER_PASSTHROUGH = "normalizer_v0_passthrough"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _clean_env(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


def _is_hf_repo(value: str) -> bool:
    raw = str(value or "").strip()
    return bool(raw and "/" in raw and "\\" not in raw and raw.count("/") == 1)


def _infer_version(value: str) -> str:
    raw = str(value or "").replace("\\", "/")
    candidates = [Path(raw).stem, Path(raw).parent.name, raw.split("/")[-1]]
    for candidate in candidates:
        match = re.search(r"(?:^|[-_])v(\d+)(?:\b|[-_])", str(candidate), flags=re.IGNORECASE)
        if match:
            return f"v{match.group(1)}"
    return "unversioned"


def _trace(
    *,
    stage: str,
    model_id: str,
    provider: str,
    source: str,
    fallback: str,
    resolved_path: str = "",
    confidence: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> ModelStageTrace:
    return ModelStageTrace(
        stage=stage,
        model_id=str(model_id or ""),
        provider=str(provider or ""),
        version=_infer_version(model_id or resolved_path),
        source=str(source or ""),
        resolved_path=str(resolved_path or ""),
        fallback=str(fallback or ""),
        confidence=confidence,
        metadata=dict(metadata or {}),
    )


def _resolve_local_or_repo(
    *,
    stage: str,
    env_model_names: tuple[str, ...],
    env_repo_names: tuple[str, ...],
    default_local: Path,
    default_repo: str,
    fallback: str,
    default_confidence: float | None = None,
) -> tuple[str, ModelStageTrace]:
    for env_name in env_model_names:
        raw = _clean_env(env_name)
        if not raw:
            continue
        path = Path(raw).expanduser().resolve()
        if path.exists():
            value = str(path)
            return value, _trace(
                stage=stage,
                model_id=value,
                provider="local",
                source=f"env:{env_name}",
                resolved_path=value,
                fallback=fallback,
                confidence=default_confidence,
            )
        if _is_hf_repo(raw):
            return raw, _trace(
                stage=stage,
                model_id=raw,
                provider="huggingface",
                source=f"env:{env_name}",
                fallback=fallback,
                confidence=default_confidence,
                metadata={"note": "env model value looked like a Hugging Face repo"},
            )

    for env_name in env_repo_names:
        raw = _clean_env(env_name)
        if raw:
            return raw, _trace(
                stage=stage,
                model_id=raw,
                provider="huggingface" if _is_hf_repo(raw) else "configured",
                source=f"env:{env_name}",
                fallback=fallback,
                confidence=default_confidence,
            )

    local = Path(default_local).expanduser().resolve()
    if local.exists():
        value = str(local)
        return value, _trace(
            stage=stage,
            model_id=value,
            provider="local",
            source="default_local",
            resolved_path=value,
            fallback=fallback,
            confidence=default_confidence,
        )

    return default_repo, _trace(
        stage=stage,
        model_id=default_repo,
        provider="huggingface",
        source="default_repo",
        fallback=fallback,
        confidence=default_confidence,
    )


def _load_normalizer_candidate() -> dict[str, Any]:
    path = _REPO_ROOT / "config" / "hf_ocr_normalizer_job.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_job_config(filename: str) -> dict[str, Any]:
    path = _REPO_ROOT / "config" / filename
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        "config_path": str(path),
        "dataset_repo_id": str(payload.get("dataset_repo_id") or ""),
        "dataset_local_path": str(payload.get("dataset_local_path") or ""),
        "model_repo_id": str(payload.get("model_repo_id") or ""),
        "merged_model_repo_id": str(payload.get("merged_model_repo_id") or ""),
        "base_model": str(payload.get("base_model") or ""),
        "job_name": str(payload.get("job_name") or ""),
        "last_job_id": str(payload.get("last_job_id") or ""),
        "last_job_status": str(payload.get("last_job_status") or ""),
        "last_metrics": dict(payload.get("last_metrics") or {}) if isinstance(payload.get("last_metrics"), dict) else {},
    }


def _candidate(stage: str, filename: str, *, active_field: str = "model_repo_id") -> dict[str, Any]:
    payload = _load_job_config(filename)
    if not payload:
        return {}
    model_id = str(payload.get(active_field) or payload.get("model_repo_id") or payload.get("merged_model_repo_id") or "")
    return {
        "stage": stage,
        "model_id": model_id,
        "version": _infer_version(model_id),
        "source": f"config:{filename}",
        "dataset_repo_id": payload.get("dataset_repo_id", ""),
        "dataset_local_path": payload.get("dataset_local_path", ""),
        "base_model": payload.get("base_model", ""),
        "job_name": payload.get("job_name", ""),
        "last_job_id": payload.get("last_job_id", ""),
        "last_job_status": payload.get("last_job_status", ""),
        "last_metrics": payload.get("last_metrics", {}),
    }


def build_model_inventory_manifest(defaults: ModelDefaults | None = None) -> dict[str, Any]:
    defaults = defaults or resolve_model_defaults()
    candidates = [
        _candidate("pdf_detector", "hf_pdf_problem_detector_job_v4.json"),
        _candidate("pdf_detector", "hf_pdf_problem_detector_job_v3.json"),
        _candidate("pdf_detector", "hf_pdf_problem_detector_job_v2.json"),
        _candidate("ocr", "hf_ocr_geometry_rules_v4_reasoning_job.json", active_field="merged_model_repo_id"),
        _candidate("ocr", "hf_ocr_geometry_reviewed_v3_graphaware_reasoning_job.json", active_field="merged_model_repo_id"),
        _candidate("ocr", "hf_ocr_reviewed_v3_reasoning_job.json", active_field="merged_model_repo_id"),
        _candidate("figure_segmenter", "hf_graph_detector_job.json"),
        _candidate("normalizer", "hf_ocr_normalizer_job.json"),
    ]
    return {
        "schema_version": "pdf_factory_model_inventory_manifest_v1",
        "current_defaults": defaults.to_dict(),
        "candidates_from_config": [row for row in candidates if row.get("model_id")],
        "required_trace_fields": [
            "stage",
            "model_id",
            "provider",
            "version",
            "source",
            "fallback",
            "confidence",
        ],
        "golden_sources": {
            "pdf_detector": "pdf_problem_boxes_yolo_golden",
            "ocr": "ocr_golden_live",
            "figure_segmenter": "segment_training_live",
            "normalizer": "ocr_normalization_golden_live",
        },
        "policy": {
            "automatic_outputs_target": "staging",
            "human_corrections_feed_training": True,
            "problemas_write_enabled": False,
        },
    }


def resolve_model_defaults() -> ModelDefaults:
    pdf_detector, pdf_trace = _resolve_local_or_repo(
        stage="pdf_detector",
        env_model_names=("PDF_PROBLEM_MODEL",),
        env_repo_names=("PDF_PROBLEM_MODEL_REPO",),
        default_local=DEFAULT_LOCAL_MODEL_PATH,
        default_repo=DEFAULT_MODEL_REPO_ID,
        fallback="manual_pdf_box_review_in_modulo13",
        default_confidence=0.25,
    )

    figure_segmenter, figure_trace = _resolve_local_or_repo(
        stage="figure_segmenter",
        env_model_names=(
            "YOLO_FIGURE_SEGMENT_MODEL",
            "YOLO_FIGURE_MODEL",
            "FIGURE_DETECTOR_MODEL",
            "YOLO_SEGMENT_MODEL",
            "YOLO_DETECT_MODEL",
        ),
        env_repo_names=("YOLO_FIGURE_SEGMENT_MODEL_REPO",),
        default_local=DEFAULT_YOLO_FIGURE_SEGMENT_MODEL_LOCAL,
        default_repo=DEFAULT_YOLO_FIGURE_SEGMENT_MODEL_REPO,
        fallback="manual_segment_review_or_empty_segments",
        default_confidence=0.12,
    )

    ocr_model = _clean_env("HF_MODEL") or TRAINED_OCR_VISION_MODEL
    ocr_provider = "huggingface" if _is_hf_repo(ocr_model) else "configured"
    ocr_trace = _trace(
        stage="ocr",
        model_id=ocr_model,
        provider=ocr_provider,
        source="env:HF_MODEL" if _clean_env("HF_MODEL") else "code_default",
        fallback="local_tesseract_ocr_and_rule_parser",
        metadata={
            "runtime_provider_can_override": True,
            "default_runtime_provider": "hf",
        },
    )

    normalizer_candidate = _load_normalizer_candidate()
    configured_normalizer = _clean_env("HF_OCR_NORMALIZER_MODEL")
    normalizer = configured_normalizer or NORMALIZER_PASSTHROUGH
    normalizer_trace = _trace(
        stage="normalizer",
        model_id=normalizer,
        provider="huggingface" if configured_normalizer else "local_passthrough",
        source="env:HF_OCR_NORMALIZER_MODEL" if configured_normalizer else "pipeline_passthrough",
        fallback="pipeline_structured_item_passthrough",
        metadata={
            "candidate_model_repo_id": str(normalizer_candidate.get("model_repo_id") or ""),
            "candidate_dataset_repo_id": str(normalizer_candidate.get("dataset_repo_id") or ""),
            "active_in_pipeline": bool(configured_normalizer),
        },
    )

    fallbacks = {
        "pdf_detector": "manual_pdf_box_review_in_modulo13",
        "ocr": "local_tesseract_ocr_and_rule_parser",
        "figure_segmenter": "manual_segment_review_or_empty_segments",
        "normalizer": "pipeline_structured_item_passthrough",
        "staging": "json_files_per_instance",
    }
    return ModelDefaults(
        pdf_detector=pdf_detector,
        ocr=ocr_model,
        figure_segmenter=figure_segmenter,
        normalizer=normalizer,
        fallbacks=fallbacks,
        stages={
            "pdf_detector": pdf_trace,
            "ocr": ocr_trace,
            "figure_segmenter": figure_trace,
            "normalizer": normalizer_trace,
        },
    )


def build_retraining_evaluation_matrix() -> dict[str, Any]:
    return {
        "schema_version": "pdf_factory_retraining_matrix_v1",
        "decision_policy": "retrain_or_recalibrate_when_any_stage_fails_threshold_on_reviewed_holdout",
        "holdout_minimums": {
            "pdf_detector_pages": 50,
            "ocr_problem_crops": 100,
            "figure_segmenter_crops": 80,
            "normalizer_pairs": 100,
        },
        "stages": {
            "pdf_detector": {
                "metrics": {
                    "map50_min": 0.85,
                    "page_recall_min": 0.90,
                    "false_split_merge_rate_max": 0.08,
                },
                "golden_source": "pdf_problem_boxes_live",
            },
            "ocr": {
                "metrics": {
                    "format_pass_rate_min": 0.90,
                    "item_exact_match_min": 0.65,
                    "character_error_rate_max": 0.12,
                    "needs_review_rate_max": 0.25,
                },
                "golden_source": "ocr_golden_live",
            },
            "figure_segmenter": {
                "metrics": {
                    "map50_min": 0.80,
                    "diagram_presence_f1_min": 0.88,
                    "false_positive_rate_max": 0.12,
                },
                "golden_source": "segment_training_live",
            },
            "normalizer": {
                "metrics": {
                    "normalized_exact_match_min": 0.75,
                    "latex_render_pass_rate_min": 0.92,
                    "option_label_accuracy_min": 0.95,
                },
                "golden_source": "ocr_normalization_golden_live",
            },
        },
    }
