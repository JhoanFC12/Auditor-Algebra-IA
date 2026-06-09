from __future__ import annotations

from typing import Any, Dict, List

from ..scan_pipeline.extractor import parse_items_from_text


def parse_structured_items(text: str, *, curso: str, tema: str, start_n: int) -> List[Dict[str, Any]]:
    return parse_items_from_text(text, curso=curso, tema=tema, start_n=start_n)
