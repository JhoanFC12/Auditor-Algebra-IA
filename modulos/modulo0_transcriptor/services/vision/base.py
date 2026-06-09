from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class RawExtractionResult:
    raw_text: str = ""
    parsed_items: List[Dict[str, Any]] = field(default_factory=list)
    provider: str = ""
    model: str = ""


@dataclass
class ReasonItemRequest:
    raw_item: str
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReasonItemResult:
    payload: Dict[str, Any] = field(default_factory=dict)
    raw_model_text: str = ""
    raw_retry_text: str = ""
    retry_json_count: int = 0
    provider: str = ""
    model: str = ""


@dataclass
class FormatItemRequest:
    raw_item: str
    context: Dict[str, Any] = field(default_factory=dict)
    reasoning_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FormatItemResult:
    formatted_item: str = ""
    geometry_pass_text: str = ""
    format_pass_text: str = ""
    reasoning_payload: Dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""


@dataclass
class FigureDetectionResult:
    bbox_norm: List[float] = field(default_factory=list)
    confidence: float = 0.0
    detected: bool = False


@dataclass
class ProviderHealthResult:
    ok: bool
    detail: str = ""


class VisionProvider:
    def extract_raw(self, image_path: Path, context: Dict[str, Any]) -> RawExtractionResult:
        raise NotImplementedError

    def format_item(self, request: FormatItemRequest) -> FormatItemResult:
        return FormatItemResult(
            formatted_item=str(request.raw_item or ""),
            reasoning_payload=dict(request.reasoning_payload or {}),
        )

    def reason_item(self, request: ReasonItemRequest) -> ReasonItemResult:
        return ReasonItemResult(payload={}, provider=str(request.context.get("provider", "") or ""))

    def detect_figure(self, image_path: Path, context: Dict[str, Any]) -> FigureDetectionResult:
        return FigureDetectionResult()

    def probe_availability(self) -> ProviderHealthResult:
        return ProviderHealthResult(ok=True)
