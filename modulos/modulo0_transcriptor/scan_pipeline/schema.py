from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict

from ..domain.image_binding import (
    IMAGE_BINDING_STATUS_NEEDS_REVIEW,
    ImageBinding,
)

SCAN_SCHEMA = "ScanItemJSON-v1"
OPTION_LABELS = ("A", "B", "C", "D", "E")


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    txt = _safe_str(value).lower()
    return txt in {"1", "true", "si", "yes", "y"}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _normalize_answer_key(value: Any) -> str:
    txt = _safe_str(value).upper()
    if not txt:
        return ""
    match = re.search(r"\b([A-E])\b", txt)
    return match.group(1) if match else ""


def _extract_answer_key(raw: Dict[str, Any]) -> str:
    for key in (
        "answer_key",
        "clave",
        "Clave",
        "respuesta",
        "Respuesta",
        "respuesta_correcta",
        "correct_answer",
        "key",
    ):
        value = raw.get(key)
        normalized = _normalize_answer_key(value)
        if normalized:
            return normalized

    for key in ("final_latex_candidate", "latex", "rendered", "item"):
        text = _safe_str(raw.get(key))
        if not text:
            continue
        match = re.search(r"\[\[\s*clave\s*=\s*([A-Ea-e])\s*\]\]", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).upper()
    return ""


def _normalize_options(raw: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    source = raw if isinstance(raw, dict) else {}
    for label in OPTION_LABELS:
        out[label] = _safe_str(source.get(label, "...")) or "..."
    return out


@dataclass
class ScanItem:
    schema: str
    n: int
    curso: str
    tema: str
    has_figure: bool
    figure_tag: str
    statement: str
    options: Dict[str, str]
    answer_key: str = ""
    needs_review: bool = False
    image_binding: ImageBinding = field(default_factory=ImageBinding)

    @classmethod
    def empty(
        cls,
        *,
        n: int,
        curso: str,
        tema: str,
    ) -> "ScanItem":
        return cls(
            schema=SCAN_SCHEMA,
            n=max(1, int(n)),
            curso=_safe_str(curso),
            tema=_safe_str(tema),
            has_figure=False,
            figure_tag="",
            statement="[[ocr_sin_texto]]",
            options={label: "..." for label in OPTION_LABELS},
            answer_key="",
            needs_review=True,
            image_binding=ImageBinding(status=IMAGE_BINDING_STATUS_NEEDS_REVIEW, needs_review=True),
        )

    @classmethod
    def from_dict(
        cls,
        raw: Dict[str, Any],
        *,
        default_n: int,
        curso: str,
        tema: str,
    ) -> "ScanItem":
        base_n = _safe_int(raw.get("n"), default=default_n)
        image_binding = ImageBinding.from_dict(raw.get("image_binding", {}))
        has_figure_hint = _safe_bool(raw.get("has_figure", False))
        figure_tag = _safe_str(raw.get("figure_tag", ""))
        if not image_binding.marker_name and figure_tag:
            image_binding.marker_name = figure_tag
            image_binding.marker_names = [figure_tag]
        if not image_binding.is_confirmed and has_figure_hint:
            image_binding.status = IMAGE_BINDING_STATUS_NEEDS_REVIEW
            image_binding.needs_review = True
            if figure_tag and figure_tag not in image_binding.marker_names:
                image_binding.marker_names.insert(0, figure_tag)
        has_figure = image_binding.is_confirmed
        if has_figure and not figure_tag:
            figure_tag = image_binding.marker_name or f"img-{max(1, base_n)}"
        if not has_figure:
            figure_tag = ""
        return cls(
            schema=_safe_str(raw.get("schema")) or SCAN_SCHEMA,
            n=max(1, base_n),
            curso=_safe_str(raw.get("curso")) or _safe_str(curso),
            tema=_safe_str(raw.get("tema")) or _safe_str(tema),
            has_figure=has_figure,
            figure_tag=figure_tag,
            statement=_safe_str(raw.get("statement")) or "[[ocr_sin_texto]]",
            options=_normalize_options(raw.get("options")),
            answer_key=_extract_answer_key(raw),
            needs_review=_safe_bool(raw.get("needs_review", False)) or bool(image_binding.needs_review),
            image_binding=image_binding,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SCAN_SCHEMA,
            "n": int(self.n),
            "curso": _safe_str(self.curso),
            "tema": _safe_str(self.tema),
            "has_figure": bool(self.has_figure),
            "figure_tag": _safe_str(self.figure_tag) if self.has_figure else "",
            "statement": _safe_str(self.statement),
            "options": _normalize_options(self.options),
            "answer_key": _normalize_answer_key(self.answer_key),
            "needs_review": bool(self.needs_review),
            "image_binding": self.image_binding.to_dict(),
        }
