from __future__ import annotations

from typing import Any


__all__ = [
    "SCAN_SCHEMA",
    "ScanItem",
    "ImageEvidence",
    "ImageRoleDecision",
    "extract_numbered_headers",
    "resolve_image_role",
    "DEFAULT_HF_OCR_ENSEMBLE_MODELS",
    "OCRCandidateAnalysis",
    "analyze_ocr_candidate",
    "resolve_hf_ocr_ensemble_models",
    "select_best_ocr_candidate",
    "ScanPipeline",
    "PipelineRunResult",
    "PipelineItemResult",
]


def __getattr__(name: str) -> Any:
    if name in {"SCAN_SCHEMA", "ScanItem"}:
        from .schema import SCAN_SCHEMA, ScanItem

        return {"SCAN_SCHEMA": SCAN_SCHEMA, "ScanItem": ScanItem}[name]
    if name in {"ImageEvidence", "ImageRoleDecision", "extract_numbered_headers", "resolve_image_role"}:
        from .image_role import ImageEvidence, ImageRoleDecision, extract_numbered_headers, resolve_image_role

        return {
            "ImageEvidence": ImageEvidence,
            "ImageRoleDecision": ImageRoleDecision,
            "extract_numbered_headers": extract_numbered_headers,
            "resolve_image_role": resolve_image_role,
        }[name]
    if name in {
        "DEFAULT_HF_OCR_ENSEMBLE_MODELS",
        "OCRCandidateAnalysis",
        "analyze_ocr_candidate",
        "resolve_hf_ocr_ensemble_models",
        "select_best_ocr_candidate",
    }:
        from .ocr_ensemble import (
            DEFAULT_HF_OCR_ENSEMBLE_MODELS,
            OCRCandidateAnalysis,
            analyze_ocr_candidate,
            resolve_hf_ocr_ensemble_models,
            select_best_ocr_candidate,
        )

        return {
            "DEFAULT_HF_OCR_ENSEMBLE_MODELS": DEFAULT_HF_OCR_ENSEMBLE_MODELS,
            "OCRCandidateAnalysis": OCRCandidateAnalysis,
            "analyze_ocr_candidate": analyze_ocr_candidate,
            "resolve_hf_ocr_ensemble_models": resolve_hf_ocr_ensemble_models,
            "select_best_ocr_candidate": select_best_ocr_candidate,
        }[name]
    if name in {"ScanPipeline", "PipelineRunResult", "PipelineItemResult"}:
        from .pipeline import PipelineItemResult, PipelineRunResult, ScanPipeline

        return {
            "ScanPipeline": ScanPipeline,
            "PipelineRunResult": PipelineRunResult,
            "PipelineItemResult": PipelineItemResult,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
