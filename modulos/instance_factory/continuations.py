from __future__ import annotations

import re
from typing import Any


CONTINUATION_MARKER_RE = re.compile(r"^\s*(?:\[CONT\.?\]|<\s*CONT\.?\s*>)", re.IGNORECASE)


def truthy_continuation_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    raw = str(value).strip().lower()
    return raw in {
        "1",
        "true",
        "t",
        "si",
        "s\u00ed",
        "s\u00c3\u00ad",
        "s\u00e3\u00ad",
        "s",
        "yes",
        "y",
        "on",
    }


def has_continuation_marker(value: Any) -> bool:
    return bool(CONTINUATION_MARKER_RE.match(str(value or "")))


def strip_continuation_marker(value: Any) -> str:
    return CONTINUATION_MARKER_RE.sub("", str(value or ""), count=1).strip()


def continuation_flags_enabled(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return truthy_continuation_flag(payload.get("es_continuacion")) or truthy_continuation_flag(
        payload.get("fusionar_con_anterior")
    )
