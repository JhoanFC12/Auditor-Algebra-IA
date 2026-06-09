from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

from .base import (
    FormatItemRequest,
    FormatItemResult,
    RawExtractionResult,
    ReasonItemRequest,
    ReasonItemResult,
)


class TranscriptionService:
    def extract_text(self, image_path: Path, context: Dict[str, Any]) -> RawExtractionResult:
        provider = str(context.get("provider", "") or "").strip().lower()
        model = str(context.get("model", "") or "").strip()
        if provider == "openai":
            fn = context.get("openai_extract_impl")
        elif provider == "hf":
            fn = context.get("hf_extract_impl")
        else:
            fn = context.get("local_ocr_impl")
        if not callable(fn):
            raise ValueError(f"No OCR extractor configured for provider '{provider}'.")
        raw_text = str(fn(image_path=image_path, context=context) or "")
        return RawExtractionResult(raw_text=raw_text, parsed_items=[], provider=provider, model=model)

    def reason_item(self, raw_item: str, context: Dict[str, Any]) -> ReasonItemResult:
        provider = str(context.get("provider", "") or "").strip().lower()
        model = str(context.get("model", "") or "").strip()
        if provider == "openai":
            fn = context.get("openai_reason_impl")
        elif provider == "hf":
            fn = context.get("hf_reason_impl")
        else:
            fn = context.get("local_reason_impl")
        if not callable(fn):
            return ReasonItemResult(payload={}, provider=provider, model=model)
        out = fn(raw_item=raw_item, context=context)
        if isinstance(out, dict):
            payload = dict(out.get("payload", out if "razonamiento_es" in out else {}))
            return ReasonItemResult(
                payload=payload,
                raw_model_text=str(out.get("raw_model_text", "") or ""),
                raw_retry_text=str(out.get("raw_retry_text", "") or ""),
                retry_json_count=int(out.get("retry_json_count", 0) or 0),
                provider=provider,
                model=model,
            )
        return ReasonItemResult(payload={}, provider=provider, model=model)

    def format_item(
        self,
        raw_item: str,
        context: Dict[str, Any],
        reasoning_payload: Dict[str, Any] | None = None,
    ) -> FormatItemResult:
        provider = str(context.get("provider", "") or "").strip().lower()
        model = str(context.get("model", "") or "").strip()
        if provider == "openai":
            fn = context.get("openai_format_impl")
        elif provider == "hf":
            fn = context.get("hf_format_impl")
        else:
            fn = context.get("local_format_impl")
        if not callable(fn):
            return FormatItemResult(
                formatted_item=str(raw_item or ""),
                reasoning_payload=dict(reasoning_payload or {}),
                provider=provider,
                model=model,
            )
        out = fn(raw_item=raw_item, context=context, reasoning_payload=dict(reasoning_payload or {}))
        if isinstance(out, dict):
            return FormatItemResult(
                formatted_item=str(out.get("formatted_item", "") or ""),
                geometry_pass_text=str(out.get("geometry_pass_text", "") or ""),
                format_pass_text=str(out.get("format_pass_text", "") or ""),
                reasoning_payload=dict(out.get("reasoning_payload", reasoning_payload or {}) or {}),
                provider=provider,
                model=model,
            )
        return FormatItemResult(
            formatted_item=str(out or ""),
            reasoning_payload=dict(reasoning_payload or {}),
            provider=provider,
            model=model,
        )
