from __future__ import annotations

from typing import Dict


def merge_options(base_options: Dict[str, str], extra_options: Dict[str, str]) -> Dict[str, str]:
    merged = {str(k): str(v) for k, v in (base_options or {}).items()}
    for key, value in (extra_options or {}).items():
        label = str(key or "").strip().upper()
        if not label:
            continue
        raw = str(value or "").strip()
        if raw:
            merged[label] = raw
    return merged
