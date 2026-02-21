from __future__ import annotations

from typing import Dict, Iterable

from ..latex_normalizer import normalize_option, normalize_statement
from .schema import OPTION_LABELS, ScanItem
from .tokens import SEP_LINE, SEP_OPT


def _safe_text(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _option_math(text: str) -> str:
    out = normalize_option(text).text
    return out or "$...$"


def render_item(item: ScanItem) -> str:
    n = max(1, int(item.n))
    statement = normalize_statement(item.statement).text
    image_token = ""
    if item.has_figure:
        tag = _safe_text(item.figure_tag) or f"img-{n}"
        image_token = f" [[Imagen={tag}]]"

    options: Dict[str, str] = {label: _option_math(item.options.get(label, "...")) for label in OPTION_LABELS}
    option_block = (
        f"{SEP_LINE}A){options['A']}"
        f"{SEP_OPT}B){options['B']}"
        f"{SEP_OPT}C){options['C']}"
        f"{SEP_LINE}D){options['D']}"
        f"{SEP_OPT}{SEP_OPT}E){options['E']}{SEP_LINE}"
    )
    return (
        f"\\item[\\textbf{{{n}.}}] [[curso={_safe_text(item.curso)}]] "
        f"[[tema={_safe_text(item.tema)}]] {statement}{image_token}{option_block}"
    ).strip()


def render_document(items: Iterable[ScanItem]) -> str:
    lines = [render_item(item) for item in items]
    body = "\n".join(lines).strip()
    if body:
        return f"\\begin{{enumerate}}\n{body}\n\\end{{enumerate}}\n"
    return "\\begin{enumerate}\n\\end{enumerate}\n"
