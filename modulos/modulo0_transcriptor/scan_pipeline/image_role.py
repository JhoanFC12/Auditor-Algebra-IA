from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List


NUMBERED_HEADER_PATTERNS = (
    r"(?m)^\s*(\d{1,4})\s*[.)](?=\s)",
    r"(?<!\S)(\d{1,4})\s*[\].:)](?=\s*(?![A-Ea-e]\s*[\)\].:])[A-Za-zÀ-ÿ¿¡])",
)

CIRCLED_NUMBER_MAP = {
    "\u2460": "1. ",
    "\u2461": "2. ",
    "\u2462": "3. ",
    "\u2463": "4. ",
    "\u2464": "5. ",
    "\u2465": "6. ",
    "\u2466": "7. ",
    "\u2467": "8. ",
    "\u2468": "9. ",
    "\u2469": "10. ",
    "\u246A": "11. ",
    "\u246B": "12. ",
    "\u246C": "13. ",
    "\u246D": "14. ",
    "\u246E": "15. ",
    "\u246F": "16. ",
    "\u2470": "17. ",
    "\u2471": "18. ",
    "\u2472": "19. ",
    "\u2473": "20. ",
    "\u2776": "1. ",
    "\u2777": "2. ",
    "\u2778": "3. ",
    "\u2779": "4. ",
    "\u277A": "5. ",
    "\u277B": "6. ",
    "\u277C": "7. ",
    "\u277D": "8. ",
    "\u277E": "9. ",
    "\u277F": "10. ",
}


def _normalize_ocr_number_markers(text: str) -> str:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw:
        return ""
    out = raw
    out = re.sub(
        r"(?m)^(\s*)<\s*(\d{1,4})\s*\.\s*>",
        lambda m: f"{m.group(1)}{m.group(2)}. ",
        out,
    )
    out = re.sub(
        r"(?m)^(\s*)<\s*(\d{1,4})\s*>",
        lambda m: f"{m.group(1)}{m.group(2)}. ",
        out,
    )
    out = re.sub(
        r"(?<!\S)<\s*(\d{1,4})\s*\.\s*>",
        lambda m: f"{m.group(1)}. ",
        out,
    )
    out = re.sub(
        r"(?<!\S)<\s*(\d{1,4})\s*>",
        lambda m: f"{m.group(1)}. ",
        out,
    )
    for token, replacement in CIRCLED_NUMBER_MAP.items():
        out = out.replace(token, replacement)
    out = re.sub(
        r"(?m)^(\s*)[\(\[]\s*(\d{1,4})\s*[\)\]](?=\s)",
        lambda m: f"{m.group(1)}{m.group(2)}. ",
        out,
    )
    out = re.sub(
        r"(?im)^(\s*)n\.\s*(\d{1,4})\s*[.)]?\s*$",
        lambda m: f"{m.group(1)}{m.group(2)}. ",
        out,
    )
    out = re.sub(
        r"(?m)^(\s*)(\d{1,4})\s*$",
        lambda m: f"{m.group(1)}{m.group(2)}. ",
        out,
    )
    return out


def extract_numbered_headers(text: str) -> List[int]:
    raw = _normalize_ocr_number_markers(text)
    if not raw:
        return []
    values: List[int] = []
    seen: set[int] = set()
    for pattern in NUMBERED_HEADER_PATTERNS:
        for match in re.finditer(pattern, raw):
            try:
                value = int(match.group(1))
            except Exception:
                continue
            if value <= 0 or value in seen:
                continue
            seen.add(value)
            values.append(value)
    return values


def collect_new_item_numbers(
    *,
    raw_text: str,
    pending_num: int = 0,
    structured_items: List[dict[str, Any]] | None = None,
) -> List[int]:
    values: List[int] = []
    seen: set[int] = set()

    for value in extract_numbered_headers(raw_text):
        if value <= int(pending_num or 0) or value in seen:
            continue
        seen.add(value)
        values.append(value)

    for entry in list(structured_items or []):
        if not isinstance(entry, dict):
            continue
        try:
            value = int(entry.get("n", 0) or 0)
        except Exception:
            value = 0
        if value <= int(pending_num or 0) or value in seen:
            continue
        seen.add(value)
        values.append(value)

    values.sort()
    return values


@dataclass(frozen=True)
class ImageEvidence:
    raw_text: str
    has_pending_item: bool
    is_key_candidate: bool
    structured_item_count: int = 0
    leading_continuation: str = ""
    leading_options_count: int = 0
    leading_option_labels_count: int = 0
    leading_has_figure: bool = False
    segment_count: int = 0
    pending_prefix_signal: bool = False

    @property
    def header_numbers(self) -> List[int]:
        return extract_numbered_headers(self.raw_text)

    @property
    def has_new_items(self) -> bool:
        return self.structured_item_count > 0 or bool(self.header_numbers)

    @property
    def has_leading_text(self) -> bool:
        return bool(str(self.leading_continuation or "").strip())

    @property
    def has_leading_options(self) -> bool:
        return self.leading_options_count > 0

    @property
    def has_leading_labels(self) -> bool:
        return self.leading_option_labels_count > 0

    @property
    def has_leading_signal(self) -> bool:
        return (
            self.has_leading_text
            or self.has_leading_options
            or self.has_leading_labels
            or self.leading_has_figure
        )

    @property
    def has_segments(self) -> bool:
        return self.segment_count > 0


@dataclass(frozen=True)
class ImageRoleDecision:
    role: str
    keep_for_processing: bool
    has_new_items: bool
    has_leading_signal: bool
    has_segments: bool
    pending_prefix_signal: bool
    header_numbers: List[int]


def resolve_image_role(evidence: ImageEvidence) -> ImageRoleDecision:
    has_new_items = evidence.has_new_items
    has_leading_signal = evidence.has_leading_signal
    has_segments = evidence.has_segments

    if has_new_items and evidence.has_pending_item and (
        has_leading_signal or evidence.pending_prefix_signal
    ):
        role = "continuation_plus_new_items"
    elif has_new_items:
        role = "new_items_only"
    elif evidence.has_pending_item and (
        has_leading_signal or evidence.pending_prefix_signal or has_segments
    ):
        if evidence.has_leading_labels and not evidence.has_leading_options and not evidence.has_leading_text:
            role = "continuation_labels_only"
        elif evidence.leading_has_figure and not (
            evidence.has_leading_text or evidence.has_leading_options or evidence.has_leading_labels
        ):
            role = "continuation_graphic_only"
        else:
            role = "continuation_only"
    elif evidence.is_key_candidate:
        role = "pure_key_image"
    else:
        role = "unknown"

    return ImageRoleDecision(
        role=role,
        keep_for_processing=role != "pure_key_image",
        has_new_items=has_new_items,
        has_leading_signal=has_leading_signal,
        has_segments=has_segments,
        pending_prefix_signal=bool(evidence.pending_prefix_signal),
        header_numbers=evidence.header_numbers,
    )
