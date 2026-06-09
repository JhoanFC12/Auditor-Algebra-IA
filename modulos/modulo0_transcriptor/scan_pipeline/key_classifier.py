from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


KEYWORD_RE = re.compile(
    r"\b(clave|claves|rpta|rpta\.|rptas|respuesta|respuestas|solucionario|solucionarios|soluciones?)\b",
    re.IGNORECASE,
)
PAIR_RE = re.compile(r"(?:^|[\s,;|])(\d{1,3})\s*[\)\].:-]?\s*([ABCDE])(?:$|[\s,;|])", re.IGNORECASE)
TITLE_LINE_RE = re.compile(r"^\s*(?:clave|respuestas?|rpta)\s*[:\-]?\s*$", re.IGNORECASE | re.MULTILINE)
MASSIVE_PAIRS_RE = re.compile(r"(?:\b\d{1,3}\s*[\)\].:-]?\s*[ABCDE]\b.*?){6,}", re.IGNORECASE | re.DOTALL)


@dataclass
class KeyClassification:
    is_key_image: bool
    confidence: float
    reason: str
    ocr_text: str = ""


def _safe_text(value: str) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def classify_key_text(text: str, *, path: Optional[Path] = None) -> KeyClassification:
    txt = _safe_text(text)
    low = txt.lower()
    score = 0.0
    reasons = []

    if path is not None:
        stem = path.stem.lower()
        if any(token in stem for token in ("clave", "rpta", "respuesta", "solucion")):
            score += 0.55
            reasons.append("filename_keyword")

    if KEYWORD_RE.search(txt):
        score += 0.45
        reasons.append("keyword")

    pair_count = len(PAIR_RE.findall(txt))
    if pair_count >= 6:
        score += 0.55
        reasons.append(f"answer_pairs_{pair_count}")
    elif pair_count >= 4:
        score += 0.45
        reasons.append(f"answer_pairs_{pair_count}")
    elif pair_count >= 2:
        score += 0.2
        reasons.append(f"answer_pairs_{pair_count}")

    if TITLE_LINE_RE.search(txt):
        score += 0.25
        reasons.append("title_line")

    if MASSIVE_PAIRS_RE.search(txt):
        score += 0.35
        reasons.append("massive_pairs_block")

    words = re.findall(r"[A-Za-z\u00c1\u00c9\u00cd\u00d3\u00da\u00e1\u00e9\u00ed\u00f3\u00fa\u00d1\u00f1]+", low)
    if words:
        answer_letters = re.findall(r"\b[abcde]\b", low)
        if len(answer_letters) >= 4 and (len(answer_letters) / max(1, len(words))) > 0.28:
            score += 0.2
            reasons.append("high_letter_ratio")

    is_key = score >= 0.75
    reason = ",".join(reasons) if reasons else "no_signal"
    return KeyClassification(
        is_key_image=is_key,
        confidence=min(1.0, max(0.0, score)),
        reason=reason,
        ocr_text=txt,
    )


def _quick_ocr(path: Path, *, lang: str = "spa+eng") -> str:
    try:
        import pytesseract  # type: ignore
        from PIL import Image, ImageOps  # type: ignore
    except Exception:
        return ""
    try:
        with Image.open(path) as im:
            gray = ImageOps.grayscale(im)
            text = pytesseract.image_to_string(gray, lang=lang)
    except Exception:
        return ""
    return _safe_text(text)


def classify_key_image(path: Path, *, text_hint: str = "", ocr_lang: str = "spa+eng") -> KeyClassification:
    hint = _safe_text(text_hint)
    cls_hint = classify_key_text(hint, path=path)
    if cls_hint.is_key_image:
        return cls_hint
    ocr_text = _quick_ocr(path, lang=ocr_lang)
    if not ocr_text:
        return cls_hint
    cls_ocr = classify_key_text(ocr_text, path=path)
    if cls_ocr.confidence >= cls_hint.confidence:
        return cls_ocr
    return cls_hint