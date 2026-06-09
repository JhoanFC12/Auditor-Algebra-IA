from __future__ import annotations

import base64
import string
import json
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional runtime dependency
    OpenAI = None  # type: ignore[assignment]

TRAINED_OCR_VISION_MODEL = "Jhoan12/math-ocr-qwen2.5-vl-3b-geometry-rules-merged-v4"
from ..latex_normalizer import normalize_scan_json_display_text

from .prompts import (
    SYSTEM_PROMPT_EXTRACT,
    SYSTEM_PROMPT_GRAPHIC_CONTINUATION,
    SYSTEM_PROMPT_RAW_OCR,
    build_correction_prompt,
    build_extract_prompt,
    build_faithful_ocr_prompt,
    build_graphic_continuation_prompt,
    build_parse_retry_prompt,
    build_structure_prompt,
)
from .schema import OPTION_LABELS
from .tokens import SEP_LINE, SEP_OPT


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MOJIBAKE_RE = re.compile(r"(?:Ã.|Â.|â..|�)")
CIRCLED_NUMBER_MAP = {
    "①": "1. ",
    "②": "2. ",
    "③": "3. ",
    "④": "4. ",
    "⑤": "5. ",
    "⑥": "6. ",
    "⑦": "7. ",
    "⑧": "8. ",
    "⑨": "9. ",
    "⑩": "10. ",
    "⑪": "11. ",
    "⑫": "12. ",
    "⑬": "13. ",
    "⑭": "14. ",
    "⑮": "15. ",
    "⑯": "16. ",
    "⑰": "17. ",
    "⑱": "18. ",
    "⑲": "19. ",
    "⑳": "20. ",
    "❶": "1. ",
    "❷": "2. ",
    "❸": "3. ",
    "❹": "4. ",
    "❺": "5. ",
    "❻": "6. ",
    "❼": "7. ",
    "❽": "8. ",
    "❾": "9. ",
    "❿": "10. ",
}

ITEM_BLOCK_RE = re.compile(
    r"(\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\].*?)(?=\s*\\item\s*\[\s*\\textbf|\Z)",
    re.IGNORECASE | re.DOTALL,
)
ITEM_NUM_RE = re.compile(r"\\item\s*\[\s*\\textbf\{\s*(\d+)\.?\s*\}\s*\]", re.IGNORECASE)
IMAGE_TAG_RE = re.compile(r"\[\[\s*Imagen\s*=\s*(img-\d+)\s*\]\]", re.IGNORECASE)
CLAVE_TAG_RE = re.compile(r"\[\[\s*Clave\s*=\s*([A-Ea-e])\s*\]\]", re.IGNORECASE)

STRUCTURED_RE = re.compile(
    r"(?is)\bITEM\s*:\s*(?P<num>\d+)\s*"
    r"ENUNCIADO\s*:\s*(?P<enu>.*?)\s*"
    r"(?:FIGURA\s*:\s*(?P<fig>SI|NO)\s*)?"
    r"OPCIONES\s*:\s*"
    r"A\)\s*(?P<A>.*?)\s*"
    r"B\)\s*(?P<B>.*?)\s*"
    r"C\)\s*(?P<C>.*?)\s*"
    r"D\)\s*(?P<D>.*?)\s*"
    r"E\)\s*(?P<E>.*?)\s*"
    r"ENDITEM"
)
NUMBERED_HEADER_LINE_RE = re.compile(r"(?m)^\s*(\d{1,4})\s*[.)]\s+")
INLINE_NUMBERED_HEADER_RE = re.compile(
    r"(?<![0-9])(\d{1,4})\s*[\].:)](?=\s*[A-Za-zÁÉÍÓÚÜÑáéíóúüñ¿¡])"
)
OPTION_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-E])\)\s*")
OPTION_TOKEN_LOOSE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Ea-e])\)\s*")
OPTION_START_RE = re.compile(r"(?<![A-Za-z0-9])([Aa])\)\s*")

# Tighten inline header detection so interval endings like "... /6] A)" are not
# misread as numbered item headers. Inline headers must start after whitespace.
INLINE_NUMBERED_HEADER_RE = re.compile(
    r"(?<!\S)(\d{1,4})\s*[\].:)](?=\s*[A-Za-zÃÃ‰ÃÃ“ÃšÃœÃ‘Ã¡Ã©Ã­Ã³ÃºÃ¼Ã±Â¿Â¡])"
)

# Final override: inline headers must not fire on inline option tokens such as
# "1] B)" or "3] D)" inside alternative lists.
INLINE_NUMBERED_HEADER_RE = re.compile(
    r"(?<!\S)(\d{1,4})\s*[\].:)]"
    r"(?=\s*(?![A-Ea-e]\s*[\)\].:])[A-Za-zÀ-ÿ¿¡])"
)


def _safe_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _extract_chat_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for chunk in content:
            if isinstance(chunk, dict):
                txt = chunk.get("text", "")
                if txt:
                    out.append(str(txt))
            elif isinstance(chunk, str):
                out.append(chunk)
        return "\n".join(out)
    return str(content or "")


def _repair_mojibake_text(text: str) -> str:
    raw = str(text or "")
    if not raw or not MOJIBAKE_RE.search(raw):
        return raw
    current = raw
    for _ in range(2):
        if not MOJIBAKE_RE.search(current):
            break
        try:
            repaired = current.encode("latin-1", errors="strict").decode("utf-8", errors="strict")
        except Exception:
            try:
                repaired = current.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
            except Exception:
                break
        if repaired == current:
            break
        current = repaired
    return current


def _decode_scan_escapes(text: str) -> str:
    out = _safe_text(text)
    out = out.replace(r"\u00a3", SEP_LINE)
    out = out.replace(r"\u00e6", SEP_OPT)
    out = out.replace("Â£", SEP_LINE)
    out = out.replace("Ã¦", SEP_OPT)
    out = out.replace("Ã‚Â£", SEP_LINE)
    out = out.replace("ÃƒÂ¦", SEP_OPT)
    return _normalize_ocr_number_markers(out)


def _normalize_ocr_number_markers(text: str) -> str:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw:
        return ""
    out = raw
    # Normalize explicit "PROBLEMA N°/Nº/No. <n>" headers to canonical numbered headers.
    # This lets downstream splitting detect multi-problem scans even when OCR keeps
    # "PROBLEMA Nº 43 ... PROBLEMA Nº 44 ..." on a single line.
    out = re.sub(
        r"(?im)(?<![A-Za-z0-9])(?:PROBLEMA|PREGUNTA)\s*(?:N[°ºo]\.?\s*)?(\d{1,4})\b",
        lambda m: f"\n{m.group(1)}. ",
        out,
    )
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
    extra_replacements = {
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
    for token, replacement in {**CIRCLED_NUMBER_MAP, **extra_replacements}.items():
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


def _is_line_start_position(raw: str, pos: int) -> bool:
    if pos <= 0:
        return True
    line_start = raw.rfind("\n", 0, pos)
    return (line_start + 1) == pos


def _has_inline_header_prefix_context(raw: str, pos: int) -> bool:
    if _is_line_start_position(raw, pos):
        return True
    prefix = raw[max(0, pos - 120):pos]
    if not prefix:
        return False
    tail = prefix[-80:]
    if "E)" in tail.upper():
        return True
    if re.search(r"(?is)[\n\r]\s*$", prefix):
        return True
    return False


def _select_canonical_header_hits(text: str, *, start_n: int = 0) -> List[tuple[int, int]]:
    raw = _normalize_ocr_number_markers(text)
    hits = _find_numbered_header_hits(raw)
    if not hits:
        return []

    strong_hits = [(pos, num) for (pos, num) in hits if _has_inline_header_prefix_context(raw, pos)]
    if start_n <= 0:
        return strong_hits or hits

    accepted: List[tuple[int, int]] = []
    expected = int(start_n)
    for pos, num in strong_hits:
        if num == expected:
            accepted.append((pos, num))
            expected = num + 1
            continue
        if num > expected and num <= expected + 3:
            accepted.append((pos, num))
            expected = num + 1
            continue

    if accepted:
        return accepted
    return strong_hits or hits


def _should_promote_prefix_to_missing_start_item(
    prefix: str,
    *,
    start_n: int,
    first_header_num: int,
) -> bool:
    raw = re.sub(r"\s+", " ", str(prefix or "")).strip()
    if int(start_n or 0) <= 0:
        return False
    if int(first_header_num or 0) != int(start_n) + 1:
        return False
    raw = _strip_existing_continuation_markers(raw)
    if not raw:
        return False
    if _looks_like_option_only_continuation(raw):
        return False
    upper = raw.upper()
    option_count = sum(1 for label in ("A)", "B)", "C)", "D)", "E)") if label in upper)
    has_statement_signal = bool(
        len(raw) >= 32
        or " CALCULE " in f" {upper} "
        or " DETERMINE " in f" {upper} "
        or " HALLE " in f" {upper} "
        or " ES:" in upper
        or " SON:" in upper
        or "¿" in raw
        or "?" in raw
    )
    if option_count >= 3 and has_statement_signal:
        return True
    if option_count >= 5:
        return True
    return False


def _looks_like_option_only_continuation(text: str) -> bool:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return False
    raw = _strip_existing_continuation_markers(raw)
    if not raw:
        return False
    matches = list(OPTION_TOKEN_RE.finditer(raw))
    if len(matches) < 3:
        return False
    prefix = raw[: matches[0].start()].strip()
    if not prefix:
        return True
    if re.fullmatch(r"(?:<\s*)?\d{1,4}\s*(?:[.)]|>\s*\.?\s*>?)?", prefix):
        return True
    return False


def _strip_leading_problem_number_for_continuation(text: str, *, expected_n: int = 0) -> str:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return ""
    raw = re.sub(r"^\s*<\s*(\d{1,4})\s*\.?\s*>\s*", "", raw, count=1)
    if int(expected_n or 0) > 0:
        raw = re.sub(rf"^\s*{int(expected_n)}\s*[.)]\s*", "", raw, count=1)
    raw = re.sub(r"^\s*(\d{1,4})\s*[.)]\s*(?=[A-Ea-e]\))", "", raw, count=1)
    return raw.strip()


def _strip_existing_continuation_markers(text: str) -> str:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return ""
    return re.sub(
        r"(?is)^(?:\[\s*cont(?:inuacion)?\.?\s*\]\s*)+",
        "",
        raw,
        count=1,
    ).strip()


def _looks_like_full_problem_without_header(text: str) -> bool:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return False
    raw = _strip_existing_continuation_markers(raw)
    if not raw:
        return False
    if _looks_like_option_only_continuation(raw):
        return False
    upper = raw.upper()
    option_count = sum(1 for label in ("A)", "B)", "C)", "D)", "E)") if label in upper)
    if option_count < 3:
        return False
    has_statement_signal = bool(
        len(raw) >= 32
        or " CALCULE " in f" {upper} "
        or " DETERMINE " in f" {upper} "
        or " HALLE " in f" {upper} "
        or " DEL GR" in upper
        or " SEG" in upper
        or " EN EL GRAF" in upper
        or " EN LA FIG" in upper
        or " DADO " in f" {upper} "
        or " SI " in f" {upper} "
        or " ES:" in upper
        or " SON:" in upper
        or "¿" in raw
        or "?" in raw
    )
    return has_statement_signal


def canonicalize_faithful_ocr_text(text: str, *, start_n: int = 0) -> str:
    raw = _normalize_ocr_number_markers(text)
    if not raw:
        return ""

    hits = _select_canonical_header_hits(raw, start_n=int(start_n or 0))
    if not hits:
        collapsed = re.sub(r"\s+", " ", raw).strip()
        if _looks_like_option_only_continuation(collapsed):
            cleaned = _strip_existing_continuation_markers(
                _strip_leading_problem_number_for_continuation(
                    collapsed,
                    expected_n=int(start_n or 0),
                )
            )
            if cleaned:
                return f"[CONT.] {cleaned}"
        if int(start_n or 0) > 0 and _looks_like_full_problem_without_header(collapsed):
            promoted = _strip_existing_continuation_markers(collapsed)
            promoted = _strip_leading_problem_number_for_continuation(
                promoted,
                expected_n=int(start_n or 0),
            )
            return f"<{int(start_n)}.> {promoted}"
        return collapsed

    if (
        int(start_n or 0) > 0
        and hits
        and max(num for _pos, num in hits) < int(start_n)
    ):
        collapsed = re.sub(r"\s+", " ", raw).strip()
        if _looks_like_option_only_continuation(collapsed):
            cleaned = _strip_existing_continuation_markers(
                _strip_leading_problem_number_for_continuation(
                    collapsed,
                    expected_n=int(start_n or 0),
                )
            )
            if cleaned:
                return f"[CONT.] {cleaned}"

    blocks: List[str] = []
    prefix = raw[: hits[0][0]].strip() if hits and hits[0][0] > 0 else ""
    if prefix:
        prefix = re.sub(r"\s+", " ", prefix).strip()
        if prefix:
            first_num = int(hits[0][1]) if hits else 0
            if _should_promote_prefix_to_missing_start_item(
                prefix,
                start_n=int(start_n or 0),
                first_header_num=first_num,
            ):
                promoted_prefix = _strip_existing_continuation_markers(prefix)
                promoted_prefix = _strip_leading_problem_number_for_continuation(
                    promoted_prefix,
                    expected_n=int(start_n or 0),
                )
                if promoted_prefix:
                    blocks.append(f"<{int(start_n)}.> {promoted_prefix}")
            else:
                cleaned_prefix = _strip_existing_continuation_markers(prefix)
                cleaned_prefix = _strip_leading_problem_number_for_continuation(
                    cleaned_prefix,
                    expected_n=max(0, int(first_num or 0) - 1),
                )
                if cleaned_prefix:
                    blocks.append(f"[CONT.] {cleaned_prefix}")
    for idx, (start, num) in enumerate(hits):
        end = hits[idx + 1][0] if idx + 1 < len(hits) else len(raw)
        block = raw[start:end].strip()
        if not block:
            continue
        block = re.sub(
            r"^\s*(\d{1,4})\s*[.)]\s*",
            lambda m, value=int(num): f"<{value}.> ",
            block,
            count=1,
        )
        block = re.sub(r"\s+", " ", block).strip()
        blocks.append(block)
    if blocks:
        return "\n\n".join(blocks).strip()
    return re.sub(r"\s+", " ", raw).strip()


def _strip_fence(text: str) -> str:
    txt = _safe_text(text)
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json|JSON)?", "", txt).strip()
        txt = re.sub(r"```$", "", txt).strip()
    return txt


def _has_numbered_item_header(text: str) -> bool:
    return NUMBERED_HEADER_LINE_RE.search(_safe_text(text)) is not None


def _find_numbered_header_hits(text: str) -> List[tuple[int, int]]:
    raw = _normalize_ocr_number_markers(text)
    hits: List[tuple[int, int]] = []
    seen_positions: set[int] = set()
    seen_numbers: set[tuple[int, int]] = set()
    for pat in (NUMBERED_HEADER_LINE_RE, INLINE_NUMBERED_HEADER_RE):
        for match in pat.finditer(raw):
            try:
                value = int(match.group(1))
            except Exception:
                continue
            start = int(match.start())
            if value <= 0 or start < 0:
                continue
            key = (start, value)
            if start in seen_positions or key in seen_numbers:
                continue
            seen_positions.add(start)
            seen_numbers.add(key)
            hits.append((start, value))
    hits.sort(key=lambda item: item[0])
    return hits


def _split_prefix_before_first_numbered_header(text: str) -> tuple[str, str]:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    hits = _find_numbered_header_hits(raw)
    start: int | None = hits[0][0] if hits else None
    if start is None:
        return (raw.strip(), "")
    if start <= 0:
        return ("", raw.strip())
    return (raw[:start].strip(), raw[start:].strip())


def _extract_detected_header_numbers(text: str, *, start_n: int = 0) -> List[int]:
    numbers: List[int] = []
    seen: set[int] = set()
    for _start, value in _select_canonical_header_hits(text, start_n=int(start_n or 0)):
        if value in seen:
            continue
        seen.add(value)
        numbers.append(value)
    return numbers


def _has_detected_numbered_headers(text: str) -> bool:
    return bool(_find_numbered_header_hits(text))


def _split_numbered_ocr_blocks(text: str) -> List[tuple[int, str]]:
    raw = _normalize_ocr_number_markers(text)
    hits = _select_canonical_header_hits(raw)
    if hits:
        blocks: List[tuple[int, str]] = []
        for idx, (start_pos, number) in enumerate(hits):
            if number <= 0:
                continue
            header_match = re.match(r"\s*\d{1,4}\s*[\].:)]\s*", raw[start_pos:])
            body_start = start_pos + (header_match.end() if header_match else 0)
            body_end = int(hits[idx + 1][0]) if idx + 1 < len(hits) else len(raw)
            body = raw[body_start:body_end].strip()
            if body:
                blocks.append((number, body))
        if blocks:
            return blocks

    matches = list(NUMBERED_HEADER_LINE_RE.finditer(raw))
    blocks: List[tuple[int, str]] = []
    for idx, match in enumerate(matches):
        try:
            number = int(match.group(1))
        except Exception:
            continue
        if number <= 0:
            continue
        start = int(match.end())
        end = int(matches[idx + 1].start()) if idx + 1 < len(matches) else len(raw)
        body = raw[start:end].strip()
        if body:
            blocks.append((number, body))
    return blocks


def _extract_loose_options_from_raw_text(text: str) -> tuple[str, Dict[str, str]]:
    raw = _safe_text(text)
    if not raw:
        return ("", {})
    matches = list(OPTION_TOKEN_RE.finditer(raw))
    if not matches:
        return (raw, {})

    prefix = raw[: matches[0].start()]
    options: Dict[str, str] = {}
    for idx, match in enumerate(matches):
        label = str(match.group(1) or "").upper()
        seg_start = match.end()
        seg_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
        chunk = re.sub(r"\s+", " ", raw[seg_start:seg_end]).strip(" \t\n\r:;")
        if chunk:
            options[label] = chunk
    return (prefix.strip(), options)


def _strip_leading_continuation_marker(text: str) -> str:
    raw = _safe_text(text)
    if not raw:
        return ""
    return re.sub(
        r"(?is)^\s*(?:\[\s*cont(?:inuacion)?\.?\s*\]|<\s*cont(?:inuacion)?\.?\s*>|cont(?:inuacion)?\s*:)\s*",
        "",
        raw,
        count=1,
    ).strip()


def _extract_option_label_sequence(text: str) -> List[str]:
    raw = _safe_text(text)
    if not raw:
        return []
    labels: List[str] = []
    seen: set[str] = set()
    for match in OPTION_TOKEN_RE.finditer(raw):
        label = str(match.group(1) or "").upper()
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def _looks_graph_noise_line(line: str) -> bool:
    clean = _safe_text(line)
    if not clean:
        return False
    compact = re.sub(r"\s+", "", clean)
    lowered = compact.lower()

    if lowered in {"x", "y", "0", "o", "f(x)", "fx"}:
        return True
    if re.fullmatch(r"[A-Z]", clean):
        return True
    if re.fullmatch(r"[PQ]\([^)]{0,80}\)", clean):
        return True
    if re.fullmatch(r"[A-Za-z]{1,4}\([^)]{0,30}\)", clean) and "=" not in clean:
        return True
    if re.fullmatch(r"[0-9π/;,\-+.]+", compact):
        return True
    return False


def _filter_admissible_continuation_text(text: str) -> tuple[str, bool]:
    raw = _safe_text(text)
    if not raw:
        return ("", False)
    kept: List[str] = []
    graph_like_dropped = False
    for line in raw.split("\n"):
        clean = _safe_text(line)
        if not clean:
            continue
        if OPTION_TOKEN_RE.match(clean):
            break
        if _looks_graph_noise_line(clean):
            graph_like_dropped = True
            continue
        kept.append(clean)
    return (re.sub(r"\s+", " ", " ".join(kept)).strip(), graph_like_dropped)


def _build_continuation_only_payload(raw_output: str) -> Dict[str, Any]:
    cleaned_raw = _strip_leading_continuation_marker(raw_output)
    prefix_text, leading_options = _extract_loose_options_from_raw_text(cleaned_raw)
    admissible_prefix, graph_like_dropped = _filter_admissible_continuation_text(prefix_text)
    leading_option_labels = _extract_option_label_sequence(cleaned_raw)
    figure_hint = bool(graph_like_dropped or (leading_option_labels and not leading_options))
    return {
        "leading_continuation": admissible_prefix,
        "leading_options": leading_options,
        "leading_option_labels": leading_option_labels,
        "leading_has_figure": figure_hint,
        "items": [],
    }


def _normalize_structured_payload(
    *,
    payload: Any,
    leading_payload: Dict[str, Any] | None,
    detected_headers: List[int],
) -> Dict[str, Any]:
    base_leading = dict(leading_payload or {})
    allow_payload_leading = leading_payload is not None
    result: Dict[str, Any] = {
        "leading_continuation": str(base_leading.get("leading_continuation", "") or ""),
        "leading_options": dict(base_leading.get("leading_options", {}) or {}),
        "leading_option_labels": list(base_leading.get("leading_option_labels", []) or []),
        "leading_has_figure": bool(base_leading.get("leading_has_figure", False)),
        "items": [],
    }

    if isinstance(payload, dict):
        if allow_payload_leading and not result["leading_continuation"]:
            result["leading_continuation"] = str(payload.get("leading_continuation", "") or "")
        if allow_payload_leading and not result["leading_options"]:
            raw_leading_opts = payload.get("leading_options", {})
            if isinstance(raw_leading_opts, dict):
                result["leading_options"] = dict(raw_leading_opts)
        if allow_payload_leading and not result["leading_option_labels"]:
            raw_labels = payload.get("leading_option_labels", [])
            if isinstance(raw_labels, list):
                result["leading_option_labels"] = [
                    str(label).strip().upper()
                    for label in raw_labels
                    if str(label).strip().upper() in OPTION_LABELS
                ]
        if allow_payload_leading:
            result["leading_has_figure"] = bool(
                result["leading_has_figure"]
                or payload.get("leading_has_figure")
                or payload.get("leading_figure")
                or payload.get("leading_figure_hint")
            )
        raw_items = payload.get("items", [])
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []

    items = [dict(it) for it in raw_items if isinstance(it, dict)]
    if not result["leading_option_labels"] and result["leading_options"]:
        result["leading_option_labels"] = [
            label for label in OPTION_LABELS if label in result["leading_options"]
        ]
    if detected_headers and items:
        if leading_payload and len(items) > len(detected_headers):
            items = items[-len(detected_headers):]
        for idx, item in enumerate(items[: len(detected_headers)]):
            item["n"] = int(detected_headers[idx])
    result["items"] = items
    return result


def _build_local_structured_payload(
    *,
    raw_output: str,
    curso: str,
    tema: str,
    start_n: int,
) -> Dict[str, Any]:
    if re.match(r"(?is)^\s*(?:\[\s*cont(?:inuacion)?\.?\s*\]|<\s*cont(?:inuacion)?\.?\s*>|cont(?:inuacion)?\s*:)", str(raw_output or "")):
        return _build_continuation_only_payload(raw_output)

    leading_raw, suffix_raw = _split_prefix_before_first_numbered_header(raw_output)
    if not suffix_raw:
        structured_items = parse_items_from_text(
            raw_output,
            curso=curso,
            tema=tema,
            start_n=start_n,
        )
        if structured_items:
            return _normalize_structured_payload(
                payload={"items": structured_items},
                leading_payload=None,
                detected_headers=[],
            )
        return _build_continuation_only_payload(raw_output)

    leading_payload: Dict[str, Any] | None = None
    if leading_raw:
        leading_payload = _build_continuation_only_payload(leading_raw)

    detected_headers = _extract_detected_header_numbers(suffix_raw, start_n=start_n)
    structured_items = parse_items_from_text(
        suffix_raw,
        curso=curso,
        tema=tema,
        start_n=start_n,
    )
    return _normalize_structured_payload(
        payload={"items": structured_items},
        leading_payload=leading_payload,
        detected_headers=detected_headers,
    )


def _repair_common_json_issues(raw: str) -> str:
    txt = str(raw or "")
    txt = _repair_json_string_backslashes(txt)
    # Tolerate trailing commas from model output.
    txt = re.sub(r",(\s*[}\]])", r"\1", txt)
    return txt


def _is_hex_quad(text: str, idx: int) -> bool:
    chunk = str(text or "")[idx : idx + 4]
    if len(chunk) != 4:
        return False
    return all(ch in string.hexdigits for ch in chunk)


def _repair_json_string_backslashes(raw: str) -> str:
    txt = str(raw or "")
    out: List[str] = []
    in_string = False
    i = 0

    while i < len(txt):
        ch = txt[i]

        if ch == '"':
            slash_count = 0
            j = i - 1
            while j >= 0 and txt[j] == "\\":
                slash_count += 1
                j -= 1
            if (slash_count % 2) == 0:
                in_string = not in_string
            out.append(ch)
            i += 1
            continue

        if (not in_string) or ch != "\\":
            out.append(ch)
            i += 1
            continue

        if i + 1 >= len(txt):
            out.append("\\\\")
            i += 1
            continue

        nxt = txt[i + 1]
        if nxt in {'"', "\\", "/"}:
            out.append("\\")
            out.append(nxt)
            i += 2
            continue

        if nxt == "u":
            if _is_hex_quad(txt, i + 2):
                out.append("\\")
                out.append("u")
                out.extend(list(txt[i + 2 : i + 6]))
                i += 6
                continue
            out.append("\\\\")
            i += 1
            continue

        if nxt == "'":
            out.append("\\\\")
            i += 1
            continue

        if nxt in {"n", "r", "t", "b", "f"}:
            next2 = txt[i + 2] if (i + 2) < len(txt) else ""
            if next2.isalpha():
                out.append("\\\\")
                i += 1
                continue
            out.append("\\")
            out.append(nxt)
            i += 2
            continue

        if nxt.isalpha():
            out.append("\\\\")
            i += 1
            continue

        out.append("\\\\")
        i += 1

    return "".join(out)


def _loads_json_candidates(raw: str) -> Any:
    candidates: List[str] = [str(raw or "")]
    match = re.search(r"(\{.*\}|\[.*\])", str(raw or ""), re.DOTALL)
    if match:
        candidates.append(match.group(1))

    for candidate in candidates:
        if not candidate:
            continue
        repaired = _repair_common_json_issues(candidate)
        try:
            return json.loads(repaired)
        except Exception:
            pass
        if repaired != candidate:
            try:
                return json.loads(candidate)
            except Exception:
                pass
    return None


def _first_json_payload(text: str) -> Any:
    raw = _strip_fence(text)
    if not raw:
        return None
    return _loads_json_candidates(raw)


def _normalize_graphic_continuation_payload(payload: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "has_figure": False,
        "starts_new_numbered_item": False,
        "numbered_item_labels": [],
        "contains_option_graphs": False,
        "option_labels_visible": [],
        "figure_scope": "none",
        "usable_text_outside_graph": "",
        "notes": "",
    }
    if not isinstance(payload, dict):
        return result

    scope = str(payload.get("figure_scope", "") or "").strip().lower()
    if scope in {"statement", "options_only", "statement_and_options", "none"}:
        result["figure_scope"] = scope

    labels: List[str] = []
    seen: set[str] = set()
    for raw_label in list(payload.get("option_labels_visible", []) or []):
        label = str(raw_label or "").strip().upper().replace(")", "").replace(".", "")
        if label in OPTION_LABELS and label not in seen:
            seen.add(label)
            labels.append(label)
    result["option_labels_visible"] = labels

    numbered_labels: List[str] = []
    for raw_label in list(payload.get("numbered_item_labels", []) or []):
        label = str(raw_label or "").strip()
        if label:
            numbered_labels.append(label)
    result["numbered_item_labels"] = numbered_labels

    result["has_figure"] = bool(payload.get("has_figure"))
    result["starts_new_numbered_item"] = bool(payload.get("starts_new_numbered_item"))
    result["contains_option_graphs"] = bool(
        payload.get("contains_option_graphs")
        or (labels and result["figure_scope"] in {"options_only", "statement_and_options"})
    )
    result["usable_text_outside_graph"] = _safe_text(payload.get("usable_text_outside_graph", ""))
    result["notes"] = _safe_text(payload.get("notes", ""))
    return result


def _split_options(body: str) -> tuple[str, Dict[str, str]]:
    raw = _decode_scan_escapes(body or "")
    start = -1
    for token in (
        f"{SEP_LINE}A)",
        f"{SEP_LINE}a)",
    ):
        start = raw.find(token)
        if start >= 0:
            break
    if start < 0:
        plain = OPTION_START_RE.search(raw)
        if plain is not None:
            start = plain.start()

    enu_src = raw if start < 0 else raw[:start]
    opt_src = "" if start < 0 else raw[start:]
    if not opt_src:
        return (re.sub(r"\s+", " ", enu_src).strip(), {label: "..." for label in OPTION_LABELS})

    label_re = OPTION_TOKEN_RE
    matches = list(label_re.finditer(opt_src))
    options: Dict[str, str] = {label: "..." for label in OPTION_LABELS}
    for idx, m in enumerate(matches):
        label = (m.group(1) or "").upper()
        seg_start = m.end()
        seg_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(opt_src)
        chunk = _decode_scan_escapes(opt_src[seg_start:seg_end])
        chunk = chunk.replace(SEP_LINE, " ").replace(SEP_OPT, " ")
        chunk = re.sub(r"\s+", " ", chunk).strip()
        if chunk:
            options[label] = chunk
    enu = re.sub(r"\s+", " ", enu_src).strip()
    return (enu, options)


def _looks_like_complete_option_block(text: str) -> bool:
    raw = _decode_scan_escapes(text or "")
    if not raw:
        return False
    labels = _extract_option_label_sequence(raw)
    return labels[:5] == list(OPTION_LABELS)


def _split_plain_ocr_question_blocks(text: str) -> List[str]:
    raw = _decode_scan_escapes(text or "")
    if not raw:
        return []
    paragraphs = [chunk.strip() for chunk in re.split(r"\n\s*\n+", raw) if chunk and chunk.strip()]
    if not paragraphs:
        return []
    blocks: List[str] = []
    buffer: List[str] = []
    for paragraph in paragraphs:
        buffer.append(paragraph)
        candidate = "\n\n".join(buffer).strip()
        if _looks_like_complete_option_block(candidate):
            blocks.append(candidate)
            buffer = []
    if buffer:
        candidate = "\n\n".join(buffer).strip()
        if _looks_like_complete_option_block(candidate):
            blocks.append(candidate)
    return blocks


def _humanize_parsed_item(item: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(item or {})
    out["statement"] = normalize_scan_json_display_text(str(out.get("statement", "") or ""))

    raw_options = out.get("options")
    if isinstance(raw_options, dict):
        normalized_options: Dict[str, str] = {}
        for label in OPTION_LABELS:
            normalized_options[label] = normalize_scan_json_display_text(str(raw_options.get(label, "...") or "..."))
        out["options"] = normalized_options
    return out


def _complete_option_count(item: Dict[str, Any]) -> int:
    options = item.get("options", {})
    if not isinstance(options, dict):
        return 0
    return sum(1 for label in OPTION_LABELS if not _looks_placeholder_option(options.get(label, "")))


def _looks_placeholder_option(value: Any) -> bool:
    text = re.sub(r"\s+", "", str(value or "")).strip().lower()
    if not text:
        return True
    return text in {"...", "$...$", "\\ldots", "$\\ldots$", "…", "$…$"}


def _looks_truncated_statement(statement: str) -> bool:
    text = _safe_text(statement)
    if not text:
        return True
    return bool(re.search(r"(?:[,;:+\-*/(]|\ba\s*\+\s*)$", text))


def _has_graph_phrase(text: Any) -> bool:
    raw = _safe_text(text).lower()
    if not raw:
        return False
    return bool(
        re.search(
            r"\b(?:en|segun|según|del|de la)\s+(?:el\s+)?(?:gr[aá]?fico|gr.?fico|figura)\b|"
            r"\b(?:gr[aá]?fico|gr.?fico|figura)\s+(?:adjunta|mostrada|siguiente)\b|"
            r"\bseg[uú]n\s+la\s+figura\b",
            raw,
            re.IGNORECASE,
        )
    )


def _has_notation_regression(model_text: Any, local_text: Any) -> bool:
    model = str(model_text or "")
    local = str(local_text or "")
    if not model or not local:
        return False
    if r"\overset{\frown}" in local and r"\overset{\frown}" not in model:
        return True
    if r"\sqrt" in local and r"\root" in model:
        return True
    if ("^\\circ" in local or "°" in local) and (
        "^\\theta" in model or "^\\bullet" in model or "\x00" in model or "theta" in model.lower()
    ):
        return True
    return False


def _prefer_local_options(model_item: Dict[str, Any], local_item: Dict[str, Any]) -> bool:
    local_options = local_item.get("options", {})
    model_options = model_item.get("options", {})
    if not isinstance(local_options, dict):
        return False
    if _complete_option_count(local_item) < 3:
        return False
    if _complete_option_count(model_item) < _complete_option_count(local_item):
        return True
    if isinstance(model_options, dict):
        model_placeholders = sum(1 for label in OPTION_LABELS if _looks_placeholder_option(model_options.get(label, "")))
        local_real = sum(1 for label in OPTION_LABELS if not _looks_placeholder_option(local_options.get(label, "")))
        if model_placeholders and local_real >= 3:
            return True
        for label in OPTION_LABELS:
            if _has_notation_regression(model_options.get(label, ""), local_options.get(label, "")):
                return True
    return False


def _repair_structured_item_against_local(item: Dict[str, Any], local_item: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(item or {})
    local_statement = _safe_text(local_item.get("statement", ""))
    model_statement = _safe_text(out.get("statement", ""))
    should_use_local_statement = bool(
        local_statement
        and (
            len(local_statement) >= len(model_statement) + 8
            or (_looks_truncated_statement(model_statement) and len(local_statement) > len(model_statement))
            or (
                _complete_option_count(local_item) >= 5
                and OPTION_TOKEN_RE.search(model_statement) is not None
            )
            or _has_notation_regression(model_statement, local_statement)
        )
    )
    if should_use_local_statement:
        out["statement"] = local_statement
        out["needs_review"] = bool(out.get("needs_review")) or bool(local_item.get("needs_review"))

    if _prefer_local_options(out, local_item):
        model_options = out.get("options", {})
        local_options = local_item.get("options", {})
        merged_options: Dict[str, str] = {}
        if not isinstance(model_options, dict):
            model_options = {}
        if not isinstance(local_options, dict):
            local_options = {}
        model_is_incomplete = _complete_option_count(out) < _complete_option_count(local_item)
        for label in OPTION_LABELS:
            model_value = model_options.get(label, "")
            local_value = local_options.get(label, "")
            if (
                not _looks_placeholder_option(local_value)
                and (
                    _looks_placeholder_option(model_value)
                    or _has_notation_regression(model_value, local_value)
                    or model_is_incomplete
                )
            ):
                merged_options[label] = str(local_value)
            else:
                merged_options[label] = str(model_value or local_value or "...")
        out["options"] = merged_options

    try:
        local_n = int(local_item.get("n") or 0)
    except Exception:
        local_n = 0
    if local_n > 0:
        out["n"] = local_n

    if _has_graph_phrase(out.get("statement", "")) or bool(local_item.get("has_figure")):
        out["has_figure"] = True
        if not _safe_text(out.get("figure_tag", "")):
            try:
                out["figure_tag"] = f"img-{max(1, int(out.get('n') or local_n or 1))}"
            except Exception:
                out["figure_tag"] = "img-1"
    return out


def _merge_structured_items_with_local_ocr(
    *,
    model_items: List[Dict[str, Any]],
    raw_output: str,
    curso: str,
    tema: str,
    start_n: int,
) -> List[Dict[str, Any]]:
    if not model_items:
        return []
    local_payload = _build_local_structured_payload(
        raw_output=raw_output,
        curso=curso,
        tema=tema,
        start_n=start_n,
    )
    local_items = [dict(it) for it in local_payload.get("items", []) if isinstance(it, dict)]
    if not local_items:
        return model_items

    local_by_number: Dict[int, Dict[str, Any]] = {}
    for local_item in local_items:
        try:
            local_n = int(local_item.get("n") or 0)
        except Exception:
            local_n = 0
        if local_n > 0 and local_n not in local_by_number:
            local_by_number[local_n] = local_item

    merged: List[Dict[str, Any]] = []
    used_local_indexes: set[int] = set()
    for idx, model_item in enumerate(model_items):
        item = dict(model_item)
        local_item: Dict[str, Any] | None = None
        try:
            model_n = int(item.get("n") or 0)
        except Exception:
            model_n = 0
        if model_n > 0:
            local_item = local_by_number.get(model_n)
        if local_item is None and idx < len(local_items):
            local_item = local_items[idx]
            used_local_indexes.add(idx)
        if local_item is None:
            merged.append(item)
            continue

        item = _repair_structured_item_against_local(item, local_item)
        merged.append(item)

    model_numbers: set[int] = set()
    for item in merged:
        try:
            n = int(item.get("n") or 0)
        except Exception:
            n = 0
        if n > 0:
            model_numbers.add(n)
    for local_idx, local_item in enumerate(local_items):
        if local_idx in used_local_indexes:
            continue
        try:
            local_n = int(local_item.get("n") or 0)
        except Exception:
            local_n = 0
        if local_n > 0 and local_n not in model_numbers:
            merged.append(dict(local_item))

    def _sort_key(row: Dict[str, Any]) -> int:
        try:
            return int(row.get("n") or 0)
        except Exception:
            return 0

    merged.sort(key=_sort_key)
    for item in merged:
        if _has_graph_phrase(item.get("statement", "")):
            item["has_figure"] = True
            if not _safe_text(item.get("figure_tag", "")):
                try:
                    item["figure_tag"] = f"img-{max(1, int(item.get('n') or 1))}"
                except Exception:
                    item["figure_tag"] = "img-1"
    return merged


def parse_items_from_text(
    text: str,
    *,
    curso: str,
    tema: str,
    start_n: int,
) -> List[Dict[str, Any]]:
    raw = _decode_scan_escapes(text)
    out: List[Dict[str, Any]] = []

    for m in STRUCTURED_RE.finditer(raw):
        n = _safe_int(m.group("num"), default=start_n + len(out))
        has_figure = str(m.group("fig") or "").strip().upper() == "SI"
        options = {label: _safe_text(m.group(label)) or "..." for label in OPTION_LABELS}
        out.append(
            {
                "schema": "ScanItemJSON-v1",
                "n": max(1, n),
                "curso": _safe_text(curso),
                "tema": _safe_text(tema),
                "has_figure": bool(has_figure),
                "figure_tag": f"img-{max(1, n)}" if has_figure else "",
                "statement": _safe_text(m.group("enu")) or "[[ocr_sin_texto]]",
                "options": options,
                "needs_review": False,
            }
        )
    if out:
        return out

    blocks = [b.strip() for b in ITEM_BLOCK_RE.findall(raw)]
    if raw.startswith("\\item") and not blocks:
        blocks = [raw]

    for idx, block in enumerate(blocks):
        num_match = ITEM_NUM_RE.search(block)
        n = _safe_int(num_match.group(1) if num_match else (start_n + idx), default=start_n + idx)
        body = ITEM_NUM_RE.sub("", block, count=1).strip()
        has_figure = bool(IMAGE_TAG_RE.search(body))
        clave_match = CLAVE_TAG_RE.search(body)
        answer_key = clave_match.group(1).upper() if clave_match else ""
        body_clean = IMAGE_TAG_RE.sub(" ", body)
        body_clean = CLAVE_TAG_RE.sub(" ", body_clean)
        body_clean = re.sub(r"\[\[\s*(?:curso|tema)\s*=[^\]]+\]\]", " ", body_clean, flags=re.IGNORECASE)
        body_clean = re.sub(r"\s+", " ", body_clean).strip()
        statement, options = _split_options(body_clean)
        out.append(
            {
                "schema": "ScanItemJSON-v1",
                "n": max(1, n),
                "curso": _safe_text(curso),
                "tema": _safe_text(tema),
                "has_figure": bool(has_figure),
                "figure_tag": f"img-{max(1, n)}" if has_figure else "",
                "statement": statement or "[[ocr_sin_texto]]",
                "options": options,
                "answer_key": answer_key,
                "needs_review": False,
            }
        )

    if out:
        return out

    numbered_blocks = _split_numbered_ocr_blocks(raw)
    for number, body in numbered_blocks:
        statement, options = _split_options(body)
        out.append(
            {
                "schema": "ScanItemJSON-v1",
                "n": max(1, number),
                "curso": _safe_text(curso),
                "tema": _safe_text(tema),
                "has_figure": False,
                "figure_tag": "",
                "statement": statement or "[[ocr_sin_texto]]",
                "options": options,
                "needs_review": False,
            }
        )

    if out:
        return out

    plain_blocks = _split_plain_ocr_question_blocks(raw)
    for idx, block in enumerate(plain_blocks):
        statement, options = _split_options(block)
        labels_present = [label for label in OPTION_LABELS if str(options.get(label, "...") or "...").strip() != "..."]
        if not statement or len(labels_present) < len(OPTION_LABELS):
            continue
        out.append(
            {
                "schema": "ScanItemJSON-v1",
                "n": max(1, int(start_n) + idx),
                "curso": _safe_text(curso),
                "tema": _safe_text(tema),
                "has_figure": False,
                "figure_tag": "",
                "statement": statement or "[[ocr_sin_texto]]",
                "options": options,
                "needs_review": False,
            }
        )

    return out


class ScanExtractor:
    def __init__(
        self,
        *,
        provider: str = "hf",
        model: str = "",
        timeout_s: int = 180,
        temperature: float = 0.0,
        top_p: float = 1.0,
        max_tokens: int = 3200,
        seed: int | None = 42,
        strict_json: bool = True,
    ) -> None:
        self.provider = (provider or "hf").strip().lower()
        self.model = (model or "").strip()
        self.timeout_s = int(timeout_s)
        self.temperature = float(temperature)
        self.top_p = float(top_p)
        self.max_tokens = int(max_tokens)
        self.seed = int(seed) if seed is not None else None
        self.strict_json = bool(strict_json) if self.provider != "ocr" else False

    def _encode_image(self, image_path: Path, *, max_side_px: int | None = None) -> str:
        mime = "image/png"
        if image_path.suffix.lower() in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif image_path.suffix.lower() == ".webp":
            mime = "image/webp"
        elif image_path.suffix.lower() == ".bmp":
            mime = "image/bmp"
        raw = image_path.read_bytes()
        if max_side_px and max_side_px > 0:
            try:
                from PIL import Image

                with Image.open(image_path) as image:
                    image = image.convert("RGB")
                    if max(image.size) > int(max_side_px):
                        image.thumbnail((int(max_side_px), int(max_side_px)))
                    buffer = BytesIO()
                    image.save(buffer, format="JPEG", quality=88, optimize=True)
                    raw = buffer.getvalue()
                    mime = "image/jpeg"
            except Exception:
                raw = image_path.read_bytes()
        return f"data:{mime};base64,{base64.b64encode(raw).decode('utf-8')}"

    def _resolve_hf_base_url_for_model(self, model: str) -> str:
        if str(model or "").strip() == TRAINED_OCR_VISION_MODEL:
            endpoint = (os.getenv("HF_TRAINED_OCR_BASE_URL", "") or "").strip()
            if not endpoint:
                raise RuntimeError(
                    "El modelo OCR entrenado requiere configurar HF_TRAINED_OCR_BASE_URL "
                    "con la URL /v1 OpenAI-compatible del endpoint dedicado."
                )
            return endpoint.rstrip("/")
        return ((os.getenv("HF_BASE_URL", "") or "").strip() or "https://router.huggingface.co/v1").rstrip("/")

    def _get_hf_client(self, model: str = "") -> Any:
        if OpenAI is None:
            raise RuntimeError("La libreria openai no esta disponible para ejecutar el cliente HF compatible.")
        token = (os.getenv("HF_TOKEN", "") or os.getenv("HUGGINGFACEHUB_API_TOKEN", "")).strip()
        if not token:
            raise RuntimeError("Missing ENV: HF_TOKEN")
        base_url = self._resolve_hf_base_url_for_model(model or self._resolve_model())
        return OpenAI(base_url=base_url, api_key=token, timeout=self.timeout_s)

    def _friendly_hf_runtime_error(self, exc: Exception, *, model: str) -> str:
        if self.provider != "hf":
            return ""
        raw = str(exc or "")
        lowered = raw.lower()
        if "inference providers" not in lowered and "403" not in lowered:
            return ""
        if "permission" not in lowered and "forbidden" not in lowered and "403" not in lowered:
            return ""
        try:
            base_url = self._resolve_hf_base_url_for_model(model)
        except Exception:
            base_url = ""
        if str(model or "").strip() == TRAINED_OCR_VISION_MODEL:
            endpoint_hint = (
                "Configura HF_TRAINED_OCR_BASE_URL con la URL /v1 del endpoint dedicado "
                "del OCR entrenado; no uses https://router.huggingface.co/v1 para este modelo "
                "salvo que tu token tenga permisos de Inference Providers."
            )
        else:
            endpoint_hint = (
                "Activa en tu token fine-grained el permiso 'Make calls to Inference Providers' "
                "o usa un endpoint dedicado compatible con OpenAI."
            )
        location = f" Base actual: {base_url}." if base_url else ""
        return (
            "Hugging Face 403: el HF_TOKEN actual no tiene permisos suficientes para llamar "
            f"Inference Providers en nombre de la cuenta del modelo ({model}). {endpoint_hint}{location}"
        )

    def _get_openai_client(self) -> Any:
        if OpenAI is None:
            raise RuntimeError("La libreria openai no esta disponible.")
        api_key = (os.getenv("OPENAI_API_KEY", "") or "").strip()
        if not api_key:
            raise RuntimeError("Missing ENV: OPENAI_API_KEY")
        return OpenAI(api_key=api_key, timeout=self.timeout_s)

    def _resolve_model(self) -> str:
        if self.model:
            return self.model
        if self.provider == "openai":
            return TRAINED_OCR_VISION_MODEL
        return (os.getenv("HF_MODEL", TRAINED_OCR_VISION_MODEL) or TRAINED_OCR_VISION_MODEL).strip()

    def _resolve_max_tokens_for_model(self, model: str) -> int:
        requested = max(256, int(self.max_tokens or 0))
        if self.provider == "hf" and str(model or "").strip() == TRAINED_OCR_VISION_MODEL:
            try:
                cap = int(str(os.getenv("HF_TRAINED_OCR_MAX_TOKENS", "1500") or "1500").strip())
            except Exception:
                cap = 1500
            return max(256, min(requested, cap))
        return requested

    def _resolve_image_max_side_for_model(self, model: str) -> int | None:
        if self.provider != "hf" or str(model or "").strip() != TRAINED_OCR_VISION_MODEL:
            return None
        try:
            max_side = int(str(os.getenv("HF_TRAINED_OCR_IMAGE_MAX_SIDE", "960") or "960").strip())
        except Exception:
            max_side = 960
        return max(480, min(1600, max_side))

    def _call_vision_chat(self, client: OpenAI, payload: Dict[str, Any]) -> str:
        resp = client.chat.completions.create(**payload)
        content = resp.choices[0].message.content if resp and resp.choices else ""
        return _repair_mojibake_text(_extract_chat_text(content))

    def _vision_chat(
        self,
        *,
        prompt: str,
        image_path: Path,
        strict_json: bool | None = None,
        system_prompt: str | None = None,
    ) -> str:
        model = self._resolve_model()
        client = self._get_openai_client() if self.provider == "openai" else self._get_hf_client(model)
        img_url = self._encode_image(
            image_path,
            max_side_px=self._resolve_image_max_side_for_model(model),
        )
        use_json = self.strict_json if strict_json is None else bool(strict_json)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": str(system_prompt or SYSTEM_PROMPT_EXTRACT)},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": img_url}},
                    ],
                },
            ],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self._resolve_max_tokens_for_model(model),
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        if use_json and self.provider in {"hf", "openai"}:
            payload["response_format"] = {"type": "json_object"}

        try:
            return self._call_vision_chat(client, payload)
        except Exception as exc:
            err = str(exc).lower()
            fallback = dict(payload)
            changed = False
            if "response_format" in fallback and (
                "response_format" in err
                or "json_object" in err
                or "json schema" in err
                or "unsupported" in err
            ):
                fallback.pop("response_format", None)
                changed = True
            if "seed" in fallback and "seed" in err:
                fallback.pop("seed", None)
                changed = True
            if changed:
                try:
                    return self._call_vision_chat(client, fallback)
                except Exception as retry_exc:
                    friendly = self._friendly_hf_runtime_error(retry_exc, model=model)
                    if friendly:
                        raise RuntimeError(friendly) from retry_exc
                    raise
            friendly = self._friendly_hf_runtime_error(exc, model=model)
            if friendly:
                raise RuntimeError(friendly) from exc
            raise

    def _ocr_local(self, image_path: Path, *, lang: str = "spa+eng") -> str:
        try:
            import pytesseract  # type: ignore
            from PIL import Image, ImageOps  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"OCR provider requires pytesseract + tesseract. {exc}")
        with Image.open(image_path) as im:
            gray = ImageOps.grayscale(im)
            return _safe_text(pytesseract.image_to_string(gray, lang=lang))

    def parse_raw_output(
        self,
        *,
        raw_output: str,
        curso: str,
        tema: str,
        start_n: int,
        allow_text_fallback: bool | None = None,
    ) -> List[Dict[str, Any]]:
        if allow_text_fallback is None:
            allow_text_fallback = (self.provider == "ocr") or (not self.strict_json)

        payload = _first_json_payload(raw_output)
        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list):
                return [_humanize_parsed_item(dict(it)) for it in items if isinstance(it, dict)]
            if payload.get("schema") == "ScanItemJSON-v1":
                return [_humanize_parsed_item(dict(payload))]
        if isinstance(payload, list):
            out = [_humanize_parsed_item(dict(it)) for it in payload if isinstance(it, dict)]
            if out:
                return out

        if allow_text_fallback:
            return parse_items_from_text(raw_output, curso=curso, tema=tema, start_n=start_n)
        return []

    def build_local_structured_output(
        self,
        *,
        raw_output: str,
        curso: str,
        tema: str,
        start_n: int,
    ) -> tuple[List[Dict[str, Any]], str]:
        payload = _build_local_structured_payload(
            raw_output=raw_output,
            curso=curso,
            tema=tema,
            start_n=start_n,
        )
        items = [dict(it) for it in payload.get("items", []) if isinstance(it, dict)]
        return (items, json.dumps(payload, ensure_ascii=False, indent=2))

    def detect_graphic_continuation(
        self,
        *,
        image_path: Path,
    ) -> Dict[str, Any]:
        if self.provider == "ocr":
            return _normalize_graphic_continuation_payload(None)
        raw = self._vision_chat(
            prompt=build_graphic_continuation_prompt(),
            image_path=image_path,
            strict_json=True,
            system_prompt=SYSTEM_PROMPT_GRAPHIC_CONTINUATION,
        )
        return _normalize_graphic_continuation_payload(_first_json_payload(raw))

    def extract_from_image(
        self,
        *,
        image_path: Path,
        curso: str,
        tema: str,
        start_n: int,
    ) -> tuple[List[Dict[str, Any]], str]:
        if self.provider == "ocr":
            raw = self._ocr_local(image_path)
            return (
                parse_items_from_text(raw, curso=curso, tema=tema, start_n=start_n),
                raw,
            )
        raw = self._vision_chat(
            prompt=build_faithful_ocr_prompt(curso=curso, tema=tema),
            image_path=image_path,
            strict_json=False,
            system_prompt=SYSTEM_PROMPT_RAW_OCR,
        )
        try:
            items, _structured_raw = self.structure_raw_output(
                image_path=image_path,
                raw_output=raw,
                curso=curso,
                tema=tema,
                start_n=start_n,
            )
        except Exception:
            items = []
        return (items, raw)

    def structure_raw_output(
        self,
        *,
        image_path: Path,
        raw_output: str,
        curso: str,
        tema: str,
        start_n: int,
    ) -> tuple[List[Dict[str, Any]], str]:
        if self.provider == "ocr":
            return self.build_local_structured_output(
                raw_output=raw_output,
                curso=curso,
                tema=tema,
                start_n=start_n,
            )

        leading_raw, suffix_raw = _split_prefix_before_first_numbered_header(raw_output)
        if not suffix_raw:
            payload = _build_local_structured_payload(
                raw_output=raw_output,
                curso=curso,
                tema=tema,
                start_n=start_n,
            )
            structured_raw = json.dumps(payload, ensure_ascii=False, indent=2)
            items = [dict(it) for it in payload.get("items", []) if isinstance(it, dict)]
            return (items, structured_raw)

        leading_payload: Dict[str, Any] | None = None
        structure_source_raw = suffix_raw
        if leading_raw:
            leading_payload = _build_continuation_only_payload(leading_raw)
        detected_headers = _extract_detected_header_numbers(structure_source_raw, start_n=start_n)

        prompt = build_structure_prompt(
            raw_ocr_text=structure_source_raw,
            curso=curso,
            tema=tema,
            start_n=start_n,
        )
        model_structured_raw = ""
        structured_items: List[Dict[str, Any]] = []
        try:
            model_structured_raw = self._vision_chat(
                prompt=prompt,
                image_path=image_path,
                strict_json=True,
                system_prompt=SYSTEM_PROMPT_EXTRACT,
            )
            structured_items = self.parse_raw_output(
                raw_output=model_structured_raw,
                curso=curso,
                tema=tema,
                start_n=start_n,
                allow_text_fallback=False,
            )
        except Exception:
            model_structured_raw = ""
            structured_items = []

        if not structured_items:
            return self.build_local_structured_output(
                raw_output=raw_output,
                curso=curso,
                tema=tema,
                start_n=start_n,
            )

        normalized_payload = _normalize_structured_payload(
            payload=_first_json_payload(model_structured_raw),
            leading_payload=leading_payload,
            detected_headers=detected_headers,
        )
        if (not normalized_payload.get("items")) and structured_items:
            normalized_payload = _normalize_structured_payload(
                payload={"items": structured_items},
                leading_payload=leading_payload,
                detected_headers=detected_headers,
            )
        normalized_items = [dict(it) for it in normalized_payload.get("items", []) if isinstance(it, dict)]
        normalized_items = _merge_structured_items_with_local_ocr(
            model_items=normalized_items,
            raw_output=raw_output,
            curso=curso,
            tema=tema,
            start_n=start_n,
        )
        normalized_payload["items"] = normalized_items
        if not normalized_payload.get("leading_option_labels") and normalized_payload.get("leading_options"):
            normalized_payload["leading_option_labels"] = [
                label for label in OPTION_LABELS if label in dict(normalized_payload.get("leading_options", {}) or {})
            ]
        structured_raw = json.dumps(normalized_payload, ensure_ascii=False, indent=2)
        return (normalized_items, structured_raw)

    def repair_raw_output(
        self,
        *,
        image_path: Path,
        raw_output: str,
        errors: Iterable[str],
        curso: str,
        tema: str,
        start_n: int,
    ) -> tuple[List[Dict[str, Any]], str]:
        if self.provider == "ocr":
            parsed = self.parse_raw_output(
                raw_output=raw_output,
                curso=curso,
                tema=tema,
                start_n=start_n,
                allow_text_fallback=True,
            )
            return (parsed, raw_output)

        prompt = build_parse_retry_prompt(
            raw_output=raw_output,
            errors=errors,
            curso=curso,
            tema=tema,
            start_n=start_n,
        )
        repaired_raw = self._vision_chat(prompt=prompt, image_path=image_path)
        repaired_items = self.parse_raw_output(
            raw_output=repaired_raw,
            curso=curso,
            tema=tema,
            start_n=start_n,
            allow_text_fallback=False,
        )
        return (repaired_items, repaired_raw)

    def correct_item(
        self,
        *,
        image_path: Path,
        item: Dict[str, Any],
        errors: Iterable[str],
        curso: str,
        tema: str,
    ) -> Dict[str, Any]:
        if self.provider == "ocr":
            return dict(item)
        prompt = build_correction_prompt(bad_item=item, errors=errors, curso=curso, tema=tema)
        raw = self._vision_chat(prompt=prompt, image_path=image_path)
        payload = _first_json_payload(raw)
        if isinstance(payload, dict):
            return _humanize_parsed_item(dict(payload))
        return dict(item)
