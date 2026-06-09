from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from ...scan_pipeline.extractor import ScanExtractor
from .base import ProviderHealthResult, RawExtractionResult, VisionProvider


class OpenAIVisionProvider(VisionProvider):
    def __init__(self, *, model: str = "", timeout_s: int = 180, strict_json: bool = True) -> None:
        self.extractor = ScanExtractor(provider="openai", model=model, timeout_s=timeout_s, strict_json=strict_json)

    def extract_raw(self, image_path: Path, context: Dict[str, Any]) -> RawExtractionResult:
        items, raw = self.extractor.extract_from_image(
            image_path=image_path,
            curso=str(context.get("curso", "") or ""),
            tema=str(context.get("tema", "") or ""),
            start_n=int(context.get("start_n", 1) or 1),
        )
        return RawExtractionResult(raw_text=raw, parsed_items=items)

    def probe_availability(self) -> ProviderHealthResult:
        api_key = (os.getenv("OPENAI_API_KEY", "") or "").strip()
        if api_key:
            return ProviderHealthResult(ok=True)
        return ProviderHealthResult(ok=False, detail="Missing ENV: OPENAI_API_KEY")
