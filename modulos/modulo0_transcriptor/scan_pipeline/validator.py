from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from ..latex_normalizer import collect_unknown_symbols
from .schema import OPTION_LABELS, SCAN_SCHEMA, ScanItem
from .statement_cleanup import statement_contains_option_markers
from .tokens import SEP_LINE, SEP_OPT


_ITEM_HEADER_RE = re.compile(r"^\\item\[\s*\\textbf\{(\d+)\.\}\s*\]")
_COURSE_TAG_RE = re.compile(r"\[\[curso=([^\]]+)\]\]")
_TOPIC_TAG_RE = re.compile(r"\[\[tema=([^\]]+)\]\]")
_OPTION_PATTERN_RE = re.compile(
    rf"{re.escape(SEP_LINE)}A\)\$.*?\${re.escape(SEP_OPT)}B\)\$.*?\${re.escape(SEP_OPT)}C\)\$.*?\${re.escape(SEP_LINE)}D\)\$.*?\${re.escape(SEP_OPT)}{re.escape(SEP_OPT)}E\)\$.*?\${re.escape(SEP_LINE)}$",
    re.DOTALL,
)
MATH_UNICODE_RE = re.compile(r"[\u00b0\u2220\u22a5\u2225\u00d7\u00f7\u2264\u2265\u2260\u2248\u221e\u03b1-\u03c9]")
TAG_RE = re.compile(r"\[\[[^\]]+\]\]")
ESCAPABLE_OUTSIDE_MATH_RE = re.compile(r"(?<!\\)[#%&_~^]")
ESCAPED_POWER_RE = re.compile(r"(?:[A-Za-z0-9]\\textasciicircum\{\}(?:[A-Za-z0-9]|\{)|\\textasciicircum\{\}(?:[A-Za-z0-9]|\{))")


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _is_int(value: Any) -> bool:
    try:
        int(value)
        return True
    except Exception:
        return False


def _is_escaped(text: str, idx: int) -> bool:
    slash_count = 0
    pos = idx - 1
    while pos >= 0 and text[pos] == "\\":
        slash_count += 1
        pos -= 1
    return (slash_count % 2) == 1


def _has_balanced_math_dollars(text: str) -> bool:
    count = 0
    for idx, ch in enumerate(text):
        if ch == "$" and not _is_escaped(text, idx):
            count += 1
    return (count % 2) == 0


def _contains_unescaped_special_outside_math(text: str) -> bool:
    in_math = False
    buf: List[str] = []
    for idx, ch in enumerate(text):
        if ch == "$" and not _is_escaped(text, idx):
            in_math = not in_math
            if not in_math and buf:
                segment = "".join(buf)
                segment = TAG_RE.sub(" ", segment)
                segment = re.sub(r"\\[A-Za-z]+(?:\*?)", " ", segment)
                if ESCAPABLE_OUTSIDE_MATH_RE.search(segment):
                    return True
                buf = []
            continue
        if not in_math:
            buf.append(ch)
    if buf:
        segment = "".join(buf)
        segment = TAG_RE.sub(" ", segment)
        segment = re.sub(r"\\[A-Za-z]+(?:\*?)", " ", segment)
        if ESCAPABLE_OUTSIDE_MATH_RE.search(segment):
            return True
    return False


def validate_item_json(item: Any) -> List[str]:
    data: Dict[str, Any] = item.to_dict() if isinstance(item, ScanItem) else dict(item or {})
    errors: List[str] = []

    if _safe_text(data.get("schema")) != SCAN_SCHEMA:
        errors.append("schema_invalido")
    if not _is_int(data.get("n")) or int(data.get("n")) <= 0:
        errors.append("n_invalido")
    if not _safe_text(data.get("curso")):
        errors.append("curso_faltante")
    if not _safe_text(data.get("tema")):
        errors.append("tema_faltante")
    statement = _safe_text(data.get("statement"))
    if not statement:
        errors.append("statement_vacio")
    if "\n" in statement or "\r" in statement:
        errors.append("statement_multilinea")
    if statement and statement_contains_option_markers(statement):
        errors.append("statement_contiene_opciones")

    has_figure = bool(data.get("has_figure", False))
    figure_tag = _safe_text(data.get("figure_tag"))
    image_binding_raw = data.get("image_binding", {})
    image_binding = image_binding_raw if isinstance(image_binding_raw, dict) else {}
    binding_status = _safe_text(image_binding.get("status", "")).lower()
    if has_figure and not re.fullmatch(r"img-\d+", figure_tag):
        errors.append("figure_tag_invalido")
    if (not has_figure) and figure_tag:
        errors.append("figure_tag_debe_estar_vacio")
    if has_figure and binding_status not in {"confirmed", "manual_confirmed"}:
        errors.append("image_binding_inconsistente")
    if binding_status in {"confirmed", "manual_confirmed"} and not has_figure:
        errors.append("has_figure_debe_ser_derivado")

    options = data.get("options")
    if not isinstance(options, dict):
        errors.append("options_invalido")
    else:
        for label in OPTION_LABELS:
            val = _safe_text(options.get(label))
            if not val:
                errors.append(f"opcion_{label}_faltante")
    return errors


def validate_rendered_item(rendered: str, *, item: ScanItem | None = None) -> List[str]:
    txt = _safe_text(rendered)
    errors: List[str] = []
    if not _ITEM_HEADER_RE.search(txt):
        errors.append("header_item_invalido")
    if not _COURSE_TAG_RE.search(txt):
        errors.append("tag_curso_faltante")
    if not _TOPIC_TAG_RE.search(txt):
        errors.append("tag_tema_faltante")
    if "opciones" in txt.lower():
        errors.append("palabra_opciones_prohibida")

    if not _OPTION_PATTERN_RE.search(txt):
        errors.append("patron_opciones_invalido")

    if MATH_UNICODE_RE.search(txt):
        errors.append("unicode_math_sin_normalizar")
    if not _has_balanced_math_dollars(txt):
        errors.append("math_delimiters_desbalanceados")
    if _contains_unescaped_special_outside_math(txt):
        errors.append("latex_special_sin_escape")
    if ESCAPED_POWER_RE.search(txt):
        errors.append("potencia_fuera_de_modo_math")
    if collect_unknown_symbols(txt):
        errors.append("simbolos_unicode_no_mapeados")

    image_pos = txt.find("[[Imagen=")
    options_pos = txt.find(f"{SEP_LINE}A)")
    if item is not None:
        expected_tag = f"img-{int(item.n)}" if item.has_figure else ""
        if item.has_figure:
            if image_pos < 0:
                errors.append("imagen_tag_faltante")
            else:
                expected_token = f" [[Imagen={expected_tag}]]"
                if expected_token not in txt:
                    errors.append("imagen_tag_incorrecto")
                if options_pos >= 0 and not (image_pos < options_pos):
                    errors.append("imagen_tag_fuera_de_posicion")
        else:
            if image_pos >= 0:
                errors.append("imagen_tag_prohibido")
    else:
        if image_pos >= 0 and options_pos >= 0 and not (image_pos < options_pos):
            errors.append("imagen_tag_fuera_de_posicion")

    return errors


def format_pass(rendered_items: Iterable[str], *, items: Iterable[ScanItem] | None = None) -> float:
    rendered_list = list(rendered_items)
    if not rendered_list:
        return 0.0
    item_list = list(items) if items is not None else []
    passed = 0
    for idx, rendered in enumerate(rendered_list):
        item = item_list[idx] if idx < len(item_list) else None
        if not validate_rendered_item(rendered, item=item):
            passed += 1
    return float(passed) / float(len(rendered_list))
