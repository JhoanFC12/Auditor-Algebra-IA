from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..services.vision.transcription_service import TranscriptionService


@dataclass
class ItemProcessResult:
    final_item: str = ""
    reasoning_payload: Dict[str, Any] = field(default_factory=dict)
    geometry_pass_text: str = ""
    format_pass_text: str = ""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    provider_used: str = ""
    model_used: str = ""


class ItemProcessingWorkflow:
    def __init__(self, *, transcription_service: TranscriptionService | None = None) -> None:
        self.transcription_service = transcription_service or TranscriptionService()

    def process_item(self, raw_item: str, context: Dict[str, Any]) -> ItemProcessResult:
        provider = str(context.get("provider", "") or "")
        model = str(context.get("model", "") or "")
        errors: List[str] = []
        warnings: List[str] = []
        reason_result = self.transcription_service.reason_item(raw_item, context)
        format_result = self.transcription_service.format_item(
            raw_item,
            context,
            reasoning_payload=reason_result.payload,
        )
        final_item = str(format_result.formatted_item or "").strip()
        if not final_item:
            final_item = str(raw_item or "")
            warnings.append("formatted_item_empty_fallback_raw")
        return ItemProcessResult(
            final_item=final_item,
            reasoning_payload=dict(format_result.reasoning_payload or reason_result.payload or {}),
            geometry_pass_text=str(format_result.geometry_pass_text or ""),
            format_pass_text=str(format_result.format_pass_text or ""),
            errors=errors,
            warnings=warnings,
            provider_used=provider,
            model_used=model,
        )
