from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple


SEP_LINE = "\u00a3"
SEP_OPT = "\u00e6"

TAG_TOKEN_RE = re.compile(r"(\[\[[^\]]+\]\])")
UNESCAPED_DOLLAR_RE = re.compile(r"(?<!\\)\$")
DOUBLE_ESCAPED_ACCENT_RE = re.compile(r"\\{2,}(?:['~\"]\{?[A-Za-z]\}?|textquestiondown\{\}|textexclamdown\{\})")
_TEXT_STYLE_MACRO_RE = re.compile(r"\\(?:text|textit|textbf|emph)\{([^{}]*)\}")
MATH_HINT_RE = re.compile(
    r"(=|<|>|\\frac|\\dfrac|\\sqrt|\\angle|\\pi|\\theta|\\alpha|\\beta|\\gamma|\\delta|"
    r"\\leq|\\geq|\\neq|\\approx|\\times|\\div|[\dA-Za-z]\s*[\+\-\*/\^]\s*[\dA-Za-z])"
)

MATH_UNICODE_MAP = {
    "\u2220": r"\angle",
    "\u22a5": r"\perp",
    "\u2225": r"\parallel",
    "\u00d7": r"\times",
    "\u00f7": r"\div",
    "\u2264": r"\leq",
    "\u2265": r"\geq",
    "\u2260": r"\neq",
    "\u2248": r"\approx",
    "\u221e": r"\infty",
    "\u00b1": r"\pm",
    "\u2213": r"\mp",
    "\u00b7": r"\cdot",
    "\u2022": r"\cdot",
    "\u221a": r"\sqrt{}",
    "\u03b1": r"\alpha",
    "\u03b2": r"\beta",
    "\u03b3": r"\gamma",
    "\u03b4": r"\delta",
    "\u03b5": r"\epsilon",
    "\u03b8": r"\theta",
    "\u03bb": r"\lambda",
    "\u03bc": r"\mu",
    "\u03c0": r"\pi",
    "\u03c1": r"\rho",
    "\u03c3": r"\sigma",
    "\u03c4": r"\tau",
    "\u03c6": r"\phi",
    "\u03c9": r"\omega",
}

SPANISH_TEXT_MAP = {
    "\u00e1": r"\'a",
    "\u00e9": r"\'e",
    "\u00ed": r"\'i",
    "\u00f3": r"\'o",
    "\u00fa": r"\'u",
    "\u00c1": r"\'A",
    "\u00c9": r"\'E",
    "\u00cd": r"\'I",
    "\u00d3": r"\'O",
    "\u00da": r"\'U",
    "\u00f1": r"\~n",
    "\u00d1": r"\~N",
    "\u00fc": r"\"u",
    "\u00dc": r"\"U",
    "\u00bf": r"\textquestiondown{}",
    "\u00a1": r"\textexclamdown{}",
}

TEXT_SPECIAL_MAP = {
    "#": r"\#",
    "%": r"\%",
    "&": r"\&",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

MOJIBAKE_SPANISH_MAP = {
    "Ã¡": "á",
    "Ã©": "é",
    "Ã­": "í",
    "Ã³": "ó",
    "Ãº": "ú",
    "Ã": "Á",
    "Ã‰": "É",
    "Ã": "Í",
    "Ã“": "Ó",
    "Ãš": "Ú",
    "Ã±": "ñ",
    "Ã‘": "Ñ",
    "Ã¼": "ü",
    "Ãœ": "Ü",
    "Â¿": "¿",
    "Â¡": "¡",
}

LATEX_ACCENT_TO_UNICODE = {
    "'": {
        "a": "á",
        "e": "é",
        "i": "í",
        "o": "ó",
        "u": "ú",
        "A": "Á",
        "E": "É",
        "I": "Í",
        "O": "Ó",
        "U": "Ú",
    },
    "~": {
        "n": "ñ",
        "N": "Ñ",
    },
    '"': {
        "u": "ü",
        "U": "Ü",
    },
}


@dataclass
class LatexNormalizeResult:
    text: str
    unknown_symbols: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    changed: bool = False


def _safe_text(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n")


def _decode_scan_escapes(text: str) -> str:
    out = _safe_text(text)
    replacements = {
        r"\u00a3": SEP_LINE,
        r"\\u00a3": SEP_LINE,
        "\u00a3": SEP_LINE,
        "\u00c2\u00a3": SEP_LINE,
        r"\u00e6": SEP_OPT,
        r"\\u00e6": SEP_OPT,
        "\u00e6": SEP_OPT,
        "\u00c3\u00a6": SEP_OPT,
    }
    for src, dst in replacements.items():
        out = out.replace(src, dst)
    return out


def _fix_spanish_mojibake(text: str) -> Tuple[str, bool]:
    out = str(text or "")
    changed = False
    for src, dst in MOJIBAKE_SPANISH_MAP.items():
        if src in out:
            out = out.replace(src, dst)
            changed = True
    return (out, changed)


def _replace_accent_command(match: re.Match, accent: str) -> str:
    letter = match.group(1)
    return LATEX_ACCENT_TO_UNICODE[accent].get(letter, match.group(0))


def _canonicalize_latex_accent_escapes(text: str) -> Tuple[str, bool]:
    out = str(text or "")
    changed = False

    if DOUBLE_ESCAPED_ACCENT_RE.search(out):
        changed = True

    original = out
    out = re.sub(r"\\+textquestiondown\{\}", "¿", out)
    out = re.sub(r"\\+textexclamdown\{\}", "¡", out)
    out = re.sub(r"\\+'\{?([AaEeIiOoUu])\}?", lambda m: _replace_accent_command(m, "'"), out)
    out = re.sub(r"\\+~\{?([Nn])\}?", lambda m: _replace_accent_command(m, "~"), out)
    out = re.sub(r'\\+"\{?([Uu])\}?', lambda m: _replace_accent_command(m, '"'), out)
    return (out, changed or (out != original))


def _canonicalize_spanish_accents(text: str) -> Tuple[str, List[str]]:
    out = str(text or "")
    warnings: List[str] = []

    out, had_mojibake = _fix_spanish_mojibake(out)
    if had_mojibake:
        warnings.append("accent_mojibake_fixed")

    out, changed_accent_escape = _canonicalize_latex_accent_escapes(out)
    if DOUBLE_ESCAPED_ACCENT_RE.search(str(text or "")):
        warnings.append("accent_escape_canonicalized")

    return (out, sorted(set(warnings)))


def _balance_dollars(text: str) -> Tuple[str, bool]:
    raw = str(text or "")
    matches = list(UNESCAPED_DOLLAR_RE.finditer(raw))
    if len(matches) % 2 == 0:
        return raw, False
    last = matches[-1]
    fixed = raw[: last.start()] + raw[last.end() :]
    return fixed, True


def _split_math_segments(text: str) -> List[Tuple[bool, str]]:
    parts: List[Tuple[bool, str]] = []
    buf: List[str] = []
    in_math = False
    i = 0
    raw = str(text or "")
    while i < len(raw):
        ch = raw[i]
        if ch == "$" and (i == 0 or raw[i - 1] != "\\"):
            parts.append((in_math, "".join(buf)))
            buf = []
            in_math = not in_math
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append((in_math, "".join(buf)))
    return parts


def _normalize_degree_notation(text: str) -> str:
    out = str(text or "")
    out = re.sub(r"(\d+(?:[.,]\d+)?|[A-Za-z])\s*\u00b0", r"\1^\\circ", out)
    out = out.replace("\u00b0", r"^\circ")
    return out


def _replace_unicode_math(text: str) -> str:
    out = str(text or "")
    out = _normalize_degree_notation(out)
    for src, dst in MATH_UNICODE_MAP.items():
        out = out.replace(src, dst)
    out = re.sub(r"\bm\s*\\angle\s*([A-Z]{3})\b", r"m\\angle \1", out, flags=re.IGNORECASE)
    out = re.sub(r"(?<![mM])\\angle\s*([A-Z]{3})\b", r"\\angle \1", out)
    out = re.sub(r"\bsen\b", r"\\sin", out, flags=re.IGNORECASE)
    out = re.sub(r"\btg\b", r"\\tan", out, flags=re.IGNORECASE)
    out = re.sub(r"\bctg\b", r"\\cot", out, flags=re.IGNORECASE)
    return out


def _normalize_math_fragment(text: str) -> str:
    out = _decode_scan_escapes(text)
    out, _ = _canonicalize_spanish_accents(out)
    out = out.replace(SEP_LINE, " ").replace(SEP_OPT, " ")
    out = out.replace("$", " ")
    out = _replace_unicode_math(out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _escape_text_chunk(text: str) -> str:
    out_parts: List[str] = []
    source, _ = _canonicalize_spanish_accents(str(text or ""))
    for ch in source:
        if ch in SPANISH_TEXT_MAP:
            out_parts.append(SPANISH_TEXT_MAP[ch])
            continue
        if ch in TEXT_SPECIAL_MAP:
            out_parts.append(TEXT_SPECIAL_MAP[ch])
            continue
        out_parts.append(ch)
    return "".join(out_parts)


def _unwrap_text_style_macros(text: str) -> str:
    out = str(text or "")
    while True:
        updated = _TEXT_STYLE_MACRO_RE.sub(
            lambda match: _canonicalize_spanish_accents(match.group(1) or "")[0],
            out,
        )
        if updated == out:
            break
        out = updated
    return out


def _strip_presentation_math_wrappers_for_display(text: str) -> str:
    out = str(text or "")
    out = re.sub(r"\\left\s*([\(\)\[\]\{\}\|])", r"\1", out)
    out = re.sub(r"\\right\s*([\(\)\[\]\{\}\|])", r"\1", out)
    out = re.sub(r"\\left\s*\.", "", out)
    out = re.sub(r"\\right\s*\.", "", out)
    return out


def _escape_plain_text_keep_tags(text: str) -> str:
    chunks = TAG_TOKEN_RE.split(str(text or ""))
    out: List[str] = []
    for chunk in chunks:
        if not chunk:
            continue
        if TAG_TOKEN_RE.fullmatch(chunk):
            out.append(chunk)
        else:
            out.append(_escape_text_chunk(chunk))
    return "".join(out)


def _should_wrap_math_like(plain: str) -> bool:
    candidate = str(plain or "").strip()
    if not candidate or "$" in candidate:
        return False
    if not MATH_HINT_RE.search(candidate):
        return False
    if re.match(r"^(resuelve|calcule|halle|determine|encuentre)\b", candidate, flags=re.IGNORECASE):
        return False
    words = re.findall(r"[A-Za-z]{3,}", candidate)
    lower_words = [w for w in words if w.islower()]
    if len(lower_words) >= 2:
        return False
    return True


def _restore_angle_word_in_plain_text(text: str) -> Tuple[str, bool]:
    out = str(text or "")
    changed = False

    replacements = (
        (
            re.compile(
                r"\b(un|el|del|al|este|esta|ese|esa|aquel|aquella|mismo|misma|mismos|mismas|otro|otra|otros|otras|cada)\s+\\angle\b",
                flags=re.IGNORECASE,
            ),
            lambda m: f"{m.group(1)} ángulo",
        ),
        (
            re.compile(r"(?<![A-Za-z])\\angle(?=\s*[,;:\.\)]|\s*$)"),
            lambda _m: "ángulo",
        ),
    )

    for pattern, repl in replacements:
        updated = pattern.sub(repl, out)
        if updated != out:
            out = updated
            changed = True

    return (out, changed)


def _wrap_bare_exponent_fragments(text: str) -> Tuple[str, bool]:
    out = str(text or "")
    changed = False

    def wrap(match: re.Match) -> str:
        nonlocal changed
        body = _normalize_math_fragment(match.group(1))
        if not body:
            return match.group(0)
        changed = True
        return f"${body}$"

    out = re.sub(
        r"(?<![\\A-Za-z0-9\{])((?:[A-Za-z]|\d+(?:[.,]\d+)?)(?:\^\{[^{}]+\}|\^[A-Za-z0-9]))(?![A-Za-z0-9\}])",
        wrap,
        out,
    )
    return (out, changed)


def _consume_balanced_group(text: str, start: int) -> Tuple[str, int] | None:
    raw = str(text or "")
    if start >= len(raw) or raw[start] != "{":
        return None

    depth = 0
    i = start
    while i < len(raw):
        ch = raw[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return (raw[start : i + 1], i + 1)
        i += 1
    return None


def _wrap_structured_math_commands(text: str) -> str:
    raw = str(text or "")
    out: List[str] = []
    i = 0
    command_group_counts = {
        "frac": 2,
        "dfrac": 2,
        "sqrt": 1,
        "overline": 1,
        "bar": 1,
        "vec": 1,
        "overrightarrow": 1,
    }

    while i < len(raw):
        if raw[i] != "\\":
            out.append(raw[i])
            i += 1
            continue

        matched = False
        for command, group_count in command_group_counts.items():
            token = "\\" + command
            if not raw.startswith(token, i):
                continue

            cursor = i + len(token)
            pieces = [token]
            while cursor < len(raw) and raw[cursor].isspace():
                pieces.append(raw[cursor])
                cursor += 1
            if command == "sqrt" and cursor < len(raw) and raw[cursor] == "[":
                end_optional = raw.find("]", cursor + 1)
                if end_optional > cursor:
                    pieces.append(raw[cursor : end_optional + 1])
                    cursor = end_optional + 1
                    while cursor < len(raw) and raw[cursor].isspace():
                        pieces.append(raw[cursor])
                        cursor += 1

            groups: List[str] = []
            next_cursor = cursor
            for _ in range(group_count):
                group = _consume_balanced_group(raw, next_cursor)
                if group is None:
                    groups = []
                    break
                group_text, next_cursor = group
                groups.append(group_text)
                pieces.append(group_text)
                while next_cursor < len(raw) and raw[next_cursor].isspace():
                    pieces.append(raw[next_cursor])
                    next_cursor += 1

            if not groups or len(groups) != group_count:
                continue

            body = _normalize_math_fragment("".join(pieces).strip())
            if body:
                out.append(f"${body}$")
                i = next_cursor
                matched = True
                break

        if matched:
            continue

        out.append(raw[i])
        i += 1

    return "".join(out)


def _merge_adjacent_math_segments(text: str) -> str:
    out = str(text or "")
    while True:
        updated = re.sub(
            r"\$([^$]+)\$\s*([=+\-*/])\s*\$([^$]+)\$",
            lambda m: f"${m.group(1).strip()} {m.group(2)} {m.group(3).strip()}$",
            out,
        )
        if updated == out:
            break
        out = updated
    return out


def _wrap_common_math_fragments(text: str) -> Tuple[str, bool]:
    out = str(text or "")
    wrapped_bare_exponent = False

    def wrap(match: re.Match) -> str:
        body = _normalize_math_fragment(match.group(1))
        return f"${body}$" if body else ""

    def apply_plain_segments(value: str, transform) -> str:
        rebuilt: List[str] = []
        for is_math, chunk in _split_math_segments(value):
            if is_math:
                rebuilt.append(f"${chunk}$")
            else:
                rebuilt.append(transform(chunk))
        return "".join(rebuilt)

    # m\angle ABC = 30^\circ
    out = re.sub(
        r"\b(m\\angle\s*[A-Z]{1,3}\s*=\s*[^,.;:]+)",
        wrap,
        out,
        flags=re.IGNORECASE,
    )
    # \angle ABC
    out = re.sub(r"(\\angle\s*[A-Z]{1,3})", wrap, out)
    # 30^\circ or x^\circ
    out = re.sub(r"((?:\d+(?:[.,]\d+)?|[A-Za-z])\s*\^\\circ)", wrap, out)
    # \left( ... \right) expressions are already LaTeX math, even without $...$.
    out = re.sub(
        r"(\\left\s*(?:[()\[\]{}|.]|\\[A-Za-z]+)\s*.*?\\right\s*(?:[()\[\]{}|.]|\\[A-Za-z]+))",
        wrap,
        out,
    )
    # Combinatorial / indexed symbols such as C_{36}^{n-1}.
    out = apply_plain_segments(
        out,
        lambda plain: re.sub(
            r"((?:\d+\s*)?[A-Za-z](?:_\{[^{}]+\}|_[A-Za-z0-9]+)(?:\^\{[^{}]+\}|\^[A-Za-z0-9]+)?)",
            wrap,
            plain,
        ),
    )
    out = apply_plain_segments(out, _wrap_structured_math_commands)
    out = _merge_adjacent_math_segments(out)

    rebuilt: List[str] = []
    for is_math, part in _split_math_segments(out):
        if is_math:
            rebuilt.append(f"${part}$" if part else "")
            continue

        plain = re.sub(
            r"(?<![\{\\])((?:\\?[A-Za-z0-9]+)(?:\s*[=+\-*/]\s*(?:\\?[A-Za-z0-9\^\(\)]+))+)(?!\})",
            wrap,
            part,
        )
        plain, chunk_wrapped_bare_exponent = _wrap_bare_exponent_fragments(plain)
        wrapped_bare_exponent = wrapped_bare_exponent or chunk_wrapped_bare_exponent
        rebuilt.append(plain)

    out = "".join(rebuilt)
    out = _merge_adjacent_math_segments(out)
    return (re.sub(r"\s+", " ", out).strip(), wrapped_bare_exponent)


def _escape_text_preserving_math(text: str) -> str:
    parts = _split_math_segments(text)
    out: List[str] = []
    for is_math, chunk in parts:
        if is_math:
            body = _normalize_math_fragment(chunk)
            if body:
                out.append(f"${body}$")
            continue
        out.append(_escape_plain_text_keep_tags(chunk))
    return "".join(out)


def collect_unknown_symbols(text: str) -> List[str]:
    return sorted(
        {
            ch
            for ch in str(text or "")
            if ord(ch) > 127 and ch not in {SEP_LINE, SEP_OPT, "\n", "\r", "\t"}
        }
    )


def _normalize_text_with_math(text: str, *, wrap_math_like: bool) -> LatexNormalizeResult:
    source = _decode_scan_escapes(text)
    source, accent_warnings = _canonicalize_spanish_accents(source)
    source = source.replace("$$", "$")
    source = re.sub(r"\${3,}", "$", source)
    source, had_unbalanced_dollars = _balance_dollars(source)

    parts = _split_math_segments(source)
    out: List[str] = []
    wrapped_bare_exponent = False
    restored_angle_word = False
    for is_math, chunk in parts:
        if is_math:
            body = _normalize_math_fragment(chunk)
            if body:
                out.append(f"${body}$")
            continue

        plain = _replace_unicode_math(chunk)
        plain = _unwrap_text_style_macros(plain)
        plain = plain.replace(SEP_OPT, " ")
        plain = re.sub(r"\s+", " ", plain).strip()
        if not plain:
            continue

        plain, chunk_restored_angle_word = _restore_angle_word_in_plain_text(plain)
        restored_angle_word = restored_angle_word or chunk_restored_angle_word

        if wrap_math_like and _should_wrap_math_like(plain):
            body = _normalize_math_fragment(plain)
            out.append(f"${body}$" if body else "")
            continue

        plain, chunk_wrapped_bare_exponent = _wrap_common_math_fragments(plain)
        wrapped_bare_exponent = wrapped_bare_exponent or chunk_wrapped_bare_exponent
        out.append(_escape_text_preserving_math(plain))

    normalized = " ".join([piece.strip() for piece in out if piece.strip()]).strip()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    unknown = collect_unknown_symbols(normalized)
    warnings: List[str] = list(accent_warnings)
    if had_unbalanced_dollars:
        warnings.append("unbalanced_math_delimiters")
    if restored_angle_word:
        warnings.append("angle_word_restored")
    if wrapped_bare_exponent:
        warnings.append("bare_exponent_wrapped")
    if unknown:
        warnings.append("unknown_symbols")
    return LatexNormalizeResult(
        text=normalized,
        unknown_symbols=unknown,
        warnings=warnings,
        changed=(normalized != _safe_text(text).strip()),
    )


def normalize_plain_text_pdflatex(text: str) -> str:
    raw = _replace_unicode_math(_decode_scan_escapes(text))
    raw, _ = _canonicalize_spanish_accents(raw)
    return _escape_plain_text_keep_tags(raw)


def normalize_scan_json_display_text(text: str) -> str:
    source = _decode_scan_escapes(text)
    source, _ = _canonicalize_spanish_accents(source)
    source = source.replace("$$", "$")
    source = re.sub(r"\${3,}", "$", source)
    source, _ = _balance_dollars(source)
    source = _unwrap_text_style_macros(source)
    source = _strip_presentation_math_wrappers_for_display(source)

    parts = _split_math_segments(source)
    out: List[str] = []
    for is_math, chunk in parts:
        if is_math:
            chunk = _unwrap_text_style_macros(chunk)
            chunk = _strip_presentation_math_wrappers_for_display(chunk)
            body = _normalize_math_fragment(chunk)
            if body:
                out.append(f"${body}$")
            continue

        plain = _replace_unicode_math(chunk)
        plain = _unwrap_text_style_macros(plain)
        plain = _strip_presentation_math_wrappers_for_display(plain)
        plain = plain.replace(SEP_OPT, " ")
        plain = re.sub(r"\s+", " ", plain).strip()
        if not plain:
            continue
        plain, _ = _restore_angle_word_in_plain_text(plain)
        out.append(plain)

    normalized = " ".join(piece.strip() for piece in out if piece.strip()).strip()
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_statement(text: str, *, mode: str = "pdflatex_strict") -> LatexNormalizeResult:
    _ = mode
    source = _decode_scan_escapes(text)
    source = source.replace("\n", SEP_LINE)
    source = source.replace("$$", "$")
    source = re.sub(rf"{re.escape(SEP_LINE)}+", SEP_LINE, source)

    parts = [chunk.strip() for chunk in source.split(SEP_LINE)]
    norm_parts: List[str] = []
    unknown: List[str] = []
    warnings: List[str] = []
    changed = False

    for part in parts:
        if not part:
            norm_parts.append("")
            continue
        norm = _normalize_text_with_math(part, wrap_math_like=True)
        norm_parts.append(norm.text)
        unknown.extend(norm.unknown_symbols)
        warnings.extend(norm.warnings)
        changed = changed or norm.changed

    out = SEP_LINE.join(norm_parts)
    out = re.sub(
        rf"{re.escape(SEP_LINE)}\s*(\$[^$]+\$)\s*{re.escape(SEP_LINE)}",
        r" \1 ",
        out,
    )
    out = re.sub(
        rf"{re.escape(SEP_LINE)}\s*(\$[^$]+\$)",
        r" \1",
        out,
    )
    out = re.sub(
        rf"(\$[^$]+\$)\s*{re.escape(SEP_LINE)}",
        r"\1 ",
        out,
    )
    out = re.sub(r"\s+", " ", out).strip()
    if not out:
        out = "[[ocr_sin_texto]]"

    unknown = sorted(set(unknown + collect_unknown_symbols(out)))
    if unknown and "unknown_symbols" not in warnings:
        warnings.append("unknown_symbols")

    return LatexNormalizeResult(
        text=out,
        unknown_symbols=unknown,
        warnings=sorted(set(warnings)),
        changed=changed or (out != _safe_text(text).strip()),
    )


def normalize_option(text: str, *, mode: str = "pdflatex_strict") -> LatexNormalizeResult:
    _ = mode
    source = _decode_scan_escapes(text)
    source, accent_warnings = _canonicalize_spanish_accents(source)
    source = source.replace(SEP_LINE, " ").replace(SEP_OPT, " ")
    source = re.sub(r"\s+", " ", source).strip()

    inner = source
    if inner.startswith("$") and inner.endswith("$") and len(inner) >= 2:
        inner = inner[1:-1].strip()
    inner = inner.replace("$", " ").strip()

    norm_inner = _normalize_math_fragment(inner)
    if not norm_inner:
        norm_inner = "..."
    out = f"${norm_inner}$"
    unknown = collect_unknown_symbols(out)
    warnings: List[str] = list(accent_warnings)
    if unknown:
        warnings.append("unknown_symbols")
    return LatexNormalizeResult(
        text=out,
        unknown_symbols=unknown,
        warnings=warnings,
        changed=(out != _safe_text(text).strip()),
    )


def normalize_scan_item_text(text: str, *, mode: str = "pdflatex_strict") -> LatexNormalizeResult:
    _ = mode
    source = _decode_scan_escapes(text)
    source, accent_warnings = _canonicalize_spanish_accents(source)
    source = source.replace("$$", "$")
    source = re.sub(r"\${3,}", "$", source)

    chunks = TAG_TOKEN_RE.split(source)
    out_chunks: List[str] = []
    had_unbalanced = False
    for chunk in chunks:
        if not chunk:
            continue
        if TAG_TOKEN_RE.fullmatch(chunk):
            # Never mutate metadata/image tags.
            out_chunks.append(chunk)
            continue

        piece = _replace_unicode_math(chunk)
        piece, unbalanced_piece = _balance_dollars(piece)
        had_unbalanced = had_unbalanced or unbalanced_piece

        parts = _split_math_segments(piece)
        rebuilt_parts: List[str] = []
        for is_math, part in parts:
            if is_math:
                body = _normalize_math_fragment(part)
                if body:
                    rebuilt_parts.append(f"${body}$")
                continue
            plain = _replace_unicode_math(part)
            rebuilt_parts.append(plain)
        out_chunks.append("".join(rebuilt_parts))

    source = "".join(out_chunks)
    # Preserve item separators around inline math. The transcriptor uses
    # `£...£` as a structural wrapper, so collapsing `£$...$£` into `$...$`
    # breaks the persisted/output format across save/load cycles.
    source = re.sub(r"\s+", " ", source).strip()
    source, unbalanced_tail = _balance_dollars(source)
    had_unbalanced = had_unbalanced or unbalanced_tail
    unknown = collect_unknown_symbols(source)
    warnings: List[str] = list(accent_warnings)
    if had_unbalanced:
        warnings.append("unbalanced_math_delimiters")
    if unknown:
        warnings.append("unknown_symbols")
    return LatexNormalizeResult(
        text=source,
        unknown_symbols=unknown,
        warnings=warnings,
        changed=(source != _safe_text(text).strip()),
    )
