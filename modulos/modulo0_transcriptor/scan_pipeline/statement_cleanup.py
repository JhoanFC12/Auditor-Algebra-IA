from __future__ import annotations

import re
import unicodedata
from typing import Dict, Tuple

from .schema import OPTION_LABELS
from .tokens import SEP_LINE, SEP_OPT


_MARKER_PATTERNS = {
    label: re.compile(rf"(?<![A-Za-z0-9]){label}\)")
    for label in OPTION_LABELS
}


def _safe_text(value: str) -> str:
    return str(value or "").strip()


def _is_missing_value(value: str) -> bool:
    txt = _safe_text(value)
    return (not txt) or (txt == "...")


def _find_option_markers(statement: str) -> Dict[str, int]:
    text = str(statement or "")
    positions: Dict[str, int] = {}
    search_from = 0
    for label in OPTION_LABELS:
        match = _MARKER_PATTERNS[label].search(text, search_from)
        if not match:
            continue
        positions[label] = match.start()
        search_from = match.end()
    return positions


def statement_contains_option_markers(statement: str) -> bool:
    text = str(statement or "")
    positions = _find_option_markers(text)
    if not all(label in positions for label in ("A", "B", "C")):
        return False
    start = positions["A"]
    if start <= 8:
        return False
    return True


def _clean_option_segment(text: str) -> str:
    raw = str(text or "")
    raw = raw.replace(SEP_OPT, " ").replace(SEP_LINE, " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw.strip(" -:;")


def extract_options_from_statement(statement: str) -> Tuple[str, Dict[str, str], bool]:
    text = str(statement or "")
    if not statement_contains_option_markers(text):
        return (text, {}, False)

    positions = _find_option_markers(text)
    start = positions.get("A")
    if start is None:
        return (text, {}, False)

    extracted: Dict[str, str] = {}
    ordered_labels = [label for label in OPTION_LABELS if label in positions]
    for idx, label in enumerate(ordered_labels):
        marker_pos = positions[label]
        next_pos = len(text)
        if idx + 1 < len(ordered_labels):
            next_pos = positions[ordered_labels[idx + 1]]
        segment = text[marker_pos + 2 : next_pos]
        value = _clean_option_segment(segment)
        if not _is_missing_value(value):
            extracted[label] = value

    clean_statement = text[:start].rstrip(f" {SEP_LINE}{SEP_OPT}")
    clean_statement = re.sub(r"\s+", " ", clean_statement).strip()
    return (clean_statement, extracted, True)


_CONTINUATION_TOKEN_RE = re.compile(r"[A-Za-z0-9\\]+")
_CONTINUATION_ANCHOR_RE = re.compile(r"[A-Za-z]\s*\([^)]{0,16}\)")


def _normalize_overlap_key(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


def _continuation_token_set(value: str) -> set[str]:
    tokens: set[str] = set()
    for token in _CONTINUATION_TOKEN_RE.findall(_normalize_overlap_key(value)):
        clean = str(token or "").strip()
        if not clean:
            continue
        if len(clean) <= 1 and clean not in {"x", "y", "f", "g", "h"}:
            continue
        tokens.add(clean)
    return tokens


def merge_statement_fragments(existing: str, addition: str) -> str:
    base = str(existing or "").strip()
    add = str(addition or "").strip()
    if not base:
        return add
    if not add:
        return base

    base_key = _normalize_overlap_key(base)
    add_key = _normalize_overlap_key(add)
    if not base_key:
        return add
    if not add_key:
        return base

    if add_key in base_key:
        return base
    if base_key in add_key and len(add_key) > int(len(base_key) * 1.1):
        return add

    base_tokens = _continuation_token_set(base)
    add_tokens = _continuation_token_set(add)
    if base_tokens and add_tokens:
        overlap = len(base_tokens & add_tokens) / max(1, min(len(base_tokens), len(add_tokens)))
        base_anchors = {
            _normalize_overlap_key(token)
            for token in _CONTINUATION_ANCHOR_RE.findall(base)
            if _normalize_overlap_key(token)
        }
        add_anchors = {
            _normalize_overlap_key(token)
            for token in _CONTINUATION_ANCHOR_RE.findall(add)
            if _normalize_overlap_key(token)
        }
        if overlap >= 0.72:
            return base
        if base_anchors and add_anchors and (base_anchors & add_anchors) and overlap >= 0.55:
            return base

    return f"{base} {add}".strip()
