from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ...scan_pipeline.extractor import ScanExtractor
from .base import RawExtractionResult, VisionProvider


class LocalOCRProvider(VisionProvider):
    def __init__(self, *, timeout_s: int = 180) -> None:
        self.extractor = ScanExtractor(provider="ocr", timeout_s=timeout_s, strict_json=False)

    def extract_raw(self, image_path: Path, context: Dict[str, Any]) -> RawExtractionResult:
        items, raw = self.extractor.extract_from_image(
            image_path=image_path,
            curso=str(context.get("curso", "") or ""),
            tema=str(context.get("tema", "") or ""),
            start_n=int(context.get("start_n", 1) or 1),
        )
        return RawExtractionResult(raw_text=raw, parsed_items=items)
