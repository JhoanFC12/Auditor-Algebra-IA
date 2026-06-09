from __future__ import annotations

import re
from typing import Any, Mapping


_PLACEHOLDER_VALUES = {"...", "…", "[[ocr_sin_texto]]"}
_CONTINUATION_PREFIXES = (
    "entre ",
    "y ",
    "o ",
    "con ",
    "del ",
    "de ",
    "en ",
    "por ",
    "para ",
    "si ",
    "x",
    "\\",
    ")",
    "]",
    "}",
    "+",
    "-",
    "/",
    "=",
)
_CONTINUATION_ENDINGS = ("+", "-", "*", "/", "=", "(", "[", "{", ":", ",", ";")


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _is_placeholder_option(value: str) -> bool:
    compact = _normalize_text(value).replace(" ", "").lower()
    return (not compact) or compact in _PLACEHOLDER_VALUES


def non_placeholder_option_count(options: Mapping[str, Any] | None) -> int:
    if not isinstance(options, Mapping):
        return 0
    count = 0
    for label in ("A", "B", "C", "D", "E"):
        value = str(options.get(label, "") or "").strip()
        if value and not _is_placeholder_option(value):
            count += 1
    return count


def normalize_small_number_outlier(
    *,
    raw_num: int,
    start_n: int,
) -> int:
    """
    Heuristic for OCR headers like 93/108 when the expected sequence is still
    in the small range. We only normalize when the suffix stays close to the
    expected number, which keeps the rule intentionally conservative.
    """

    value = int(raw_num or 0)
    start = int(start_n or 0)
    if value <= 0 or start <= 0 or start > 15 or value < 90:
        return value

    suffix_candidates = [value % 10, value % 100]
    for suffix in suffix_candidates:
        if suffix <= 0:
            continue
        if 0 <= (suffix - start) <= 2:
            return suffix
    return value


def _delimiter_balance(value: str, open_char: str, close_char: str) -> int:
    return value.count(open_char) - value.count(close_char)


def _looks_truncated_statement(statement: str, *, option_count: int) -> bool:
    text = _normalize_text(statement)
    if not text or option_count > 0:
        return False
    if text.endswith(_CONTINUATION_ENDINGS):
        return True
    if (
        _delimiter_balance(text, "(", ")") > 0
        or _delimiter_balance(text, "[", "]") > 0
        or _delimiter_balance(text, "{", "}") > 0
    ):
        return True
    return bool(re.search(r"(\\frac\{[^}]*|\\sqrt\{[^}]*|\\left\([^)]*)$", text))


def _looks_like_continuation_fragment(statement: str) -> bool:
    text = _normalize_text(statement)
    if not text:
        return False
    lower = text.lower()
    if lower.startswith(_CONTINUATION_PREFIXES):
        return True
    first = text[:1]
    if first and first.islower():
        return True
    return bool(len(text) <= 48 and re.match(r"^[xX0-9\\(\[\{+\-=/]", text))


def should_absorb_direct_item_into_pending(
    *,
    pending_num: int,
    incoming_num: int,
    options_only_like: bool,
) -> bool:
    """
    Guardrail for direct-render continuation merges.

    Only absorb a direct item into the pending one when:
    - the OCR repeated the same item number, or
    - the incoming block has no reliable number and looks like loose options.

    We must not absorb a clearly newer numbered item (for example 11 into 7),
    because that silently deletes valid problems from the final LaTeX output.
    """

    pending = int(pending_num or 0)
    incoming = int(incoming_num or 0)
    if pending <= 0:
        return False
    if incoming > 0:
        return incoming == pending
    return bool(options_only_like)


def should_merge_fragmented_item_into_previous(
    *,
    previous_num: int,
    previous_statement: str,
    previous_options: Mapping[str, Any] | None,
    incoming_num: int,
    incoming_statement: str,
    incoming_options: Mapping[str, Any] | None,
) -> bool:
    """
    Detects when OCR split one problem into two adjacent structured items.

    Typical cases:
    - previous statement ends with `+` / `-` / open delimiter and no options
    - incoming block starts with a continuation fragment like `entre ...` or `x...`
      and carries the options of the previous item
    """

    prev_num = int(previous_num or 0)
    next_num = int(incoming_num or 0)
    prev_option_count = non_placeholder_option_count(previous_options)
    next_option_count = non_placeholder_option_count(incoming_options)

    if not _looks_truncated_statement(previous_statement, option_count=prev_option_count):
        return False
    if not _looks_like_continuation_fragment(incoming_statement):
        return False
    if next_option_count <= 0 and not _normalize_text(incoming_statement):
        return False
    if prev_num > 0 and next_num > (prev_num + 1):
        return False
    return True
