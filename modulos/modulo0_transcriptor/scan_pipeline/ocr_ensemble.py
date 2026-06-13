from __future__ import annotations

import os
import re
from collections import Counter
import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import unicodedata
from typing import Any, List, Sequence

from .extractor import ScanExtractor
from .image_role import extract_numbered_headers


def _normalize_topic_name(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _sqrt_is_expected_for_topic(tema: str) -> bool:
    normalized = _normalize_topic_name(tema)
    sqrt_topics = (
        "radic",
        "raiz",
        "teoria de exponentes",
        "exponent",
        "potenci",
    )
    return any(token in normalized for token in sqrt_topics)


DEFAULT_HF_OCR_ENSEMBLE_MODELS: tuple[str, ...] = (
    "Jhoan12/math-ocr-qwen2.5-vl-3b-geometry-rules-merged-v4",
    "zai-org/GLM-4.5V",
    "Qwen/Qwen2.5-VL-72B-Instruct",
    "Qwen/Qwen2.5-VL-7B-Instruct",
)
MODEL_PREFERENCE: dict[str, int] = {
    "Jhoan12/math-ocr-qwen2.5-vl-3b-geometry-rules-merged-v4": 4,
    "zai-org/GLM-4.5V": 3,
    "Qwen/Qwen2.5-VL-72B-Instruct": 2,
    "Qwen/Qwen2.5-VL-7B-Instruct": 1,
}

_MOJIBAKE_TOKENS = ("Ã", "â", "Ë", "�")
_LATEX_HINT_RE = re.compile(r"\\(?:frac|sqrt|left|right|cdot|times|pi|theta|alpha|beta|gamma|delta)\b")
_RARE_ARTIFACT_TOKENS = (
    r"\overline",
    r"\sqrt",
    "√",
    "÷",
)


@dataclass
class OCRCandidateAnalysis:
    model: str
    raw_text: str
    score: int
    header_numbers: List[int]
    parsed_numbers: List[int]
    item_count: int
    complete_option_items: int
    option_label_total: int
    leading_option_count: int
    has_leading_continuation: bool
    continuation_candidate: bool
    mojibake_hits: int
    delimiter_imbalance: int
    penalties: List[str] = field(default_factory=list)
    bonuses: List[str] = field(default_factory=list)


def _expected_prefix_len(numbers: Sequence[int], *, start_n: int) -> int:
    if not numbers:
        return 0
    if int(start_n or 0) <= 0:
        return len(list(numbers))
    expected = int(start_n)
    count = 0
    for value in numbers:
        try:
            current = int(value)
        except Exception:
            break
        if current != expected:
            break
        count += 1
        expected += 1
    return count


def _structural_completeness_key(
    analysis: OCRCandidateAnalysis,
    *,
    start_n: int,
) -> tuple[int, int, int, int, int, int]:
    prefix_len = _expected_prefix_len(analysis.parsed_numbers, start_n=start_n)
    if _looks_like_numbering_restart(analysis, start_n=start_n):
        prefix_len = max(prefix_len, len(analysis.parsed_numbers))
    return (
        prefix_len,
        analysis.complete_option_items,
        analysis.item_count,
        len(analysis.parsed_numbers),
        len(analysis.header_numbers),
        1 if analysis.continuation_candidate else 0,
    )


def _looks_like_numbering_restart_values(
    *,
    parsed_numbers: Sequence[int],
    start_n: int,
    item_count: int,
    complete_option_items: int,
    option_label_total: int,
) -> bool:
    if int(start_n or 0) < 20:
        return False
    numbers = [int(value) for value in parsed_numbers if int(value or 0) > 0]
    if not numbers:
        return False
    first = int(numbers[0])
    if first >= int(start_n or 0) or first > 15:
        return False
    if int(item_count or 0) <= 0:
        return False
    option_coverage = bool(int(complete_option_items or 0) > 0 or int(option_label_total or 0) >= 3)
    if not option_coverage:
        return False
    if len(numbers) >= 2:
        return True
    if first == 1:
        return True
    return int(complete_option_items or 0) >= 1 and int(option_label_total or 0) >= 5


def _looks_like_numbering_restart(
    analysis: OCRCandidateAnalysis,
    *,
    start_n: int,
) -> bool:
    return _looks_like_numbering_restart_values(
        parsed_numbers=analysis.parsed_numbers,
        start_n=start_n,
        item_count=analysis.item_count,
        complete_option_items=analysis.complete_option_items,
        option_label_total=analysis.option_label_total,
    )


def _looks_like_strong_misaligned_start(
    analysis: OCRCandidateAnalysis,
    *,
    start_n: int,
) -> bool:
    if int(start_n or 0) <= 0:
        return False
    numbers = [int(value) for value in analysis.parsed_numbers if int(value or 0) > 0]
    if not numbers:
        return False
    if numbers[0] >= int(start_n or 0):
        return False
    if int(analysis.item_count or 0) <= 0:
        return False
    if len(numbers) != int(analysis.item_count or 0):
        return False
    if int(analysis.complete_option_items or 0) < int(analysis.item_count or 0):
        return False
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        return False
    if int(analysis.mojibake_hits or 0) >= 2:
        return False
    if int(analysis.delimiter_imbalance or 0) >= 6:
        return False
    return True


def should_continue_numbering_for_items(
    structured_items: Sequence[dict[str, Any]],
    *,
    start_n: int,
) -> bool:
    parsed_numbers: List[int] = []
    complete_option_items = 0
    option_label_total = 0

    for raw_item in list(structured_items or []):
        if not isinstance(raw_item, dict):
            continue
        try:
            parsed_numbers.append(int(raw_item.get("n", 0) or 0))
        except Exception:
            continue
        options = raw_item.get("options", {})
        if not isinstance(options, dict):
            options = {}
        non_empty = sum(1 for label in ("A", "B", "C", "D", "E") if str(options.get(label, "") or "").strip())
        option_label_total += non_empty
        if non_empty == 5:
            complete_option_items += 1

    return _looks_like_numbering_restart_values(
        parsed_numbers=parsed_numbers,
        start_n=start_n,
        item_count=len([it for it in structured_items if isinstance(it, dict)]),
        complete_option_items=complete_option_items,
        option_label_total=option_label_total,
    )


def renumber_items_continuously(
    structured_items: Sequence[dict[str, Any]],
    *,
    start_n: int,
) -> tuple[List[dict[str, Any]], List[tuple[int, int]]]:
    next_n = max(1, int(start_n or 1))
    renumbered: List[dict[str, Any]] = []
    mapping: List[tuple[int, int]] = []

    for offset, raw_item in enumerate(list(structured_items or [])):
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        try:
            old_n = int(item.get("n", 0) or 0)
        except Exception:
            old_n = 0
        new_n = next_n + offset
        item["n"] = int(new_n)
        if bool(item.get("has_figure")):
            item["figure_tag"] = f"img-{new_n}"
        else:
            item["figure_tag"] = ""
        renumbered.append(item)
        mapping.append((old_n, new_n))

    return (renumbered, mapping)


def _is_structurally_strong(
    analysis: OCRCandidateAnalysis,
    *,
    start_n: int,
) -> tuple[bool, tuple[int, int, int, int, int, int]]:
    metrics = _structural_completeness_key(analysis, start_n=start_n)
    prefix_len, complete_items, item_count, parsed_len, _header_len, continuation_flag = metrics
    strong = bool(
        continuation_flag
        or (
            item_count > 0
            and prefix_len > 0
            and parsed_len == item_count
            and complete_items >= item_count
        )
    )
    return strong, metrics


def should_escalate_from_primary_candidate(
    analysis: OCRCandidateAnalysis,
    *,
    start_n: int,
) -> tuple[bool, str]:
    return (False, "")


def _looks_like_instruction_echo(text: str) -> bool:
    raw = (text or "").strip().lower()
    if not raw:
        return False
    tokens = (
        "regla fundamental",
        "estructura general",
        "prohibiciones absolutas",
        "validacion final",
        "validación final",
        "patron obligatorio",
        "patrón obligatorio",
        "tu unica funcion",
        "tu única función",
        "respuesta:",
        "no incluyas",
        "no incluyes",
        "no agregue texto",
        "no agregues texto",
        "no agregue",
        "no agregues",
    )
    return sum(1 for token in tokens if token in raw) >= 2


def _looks_like_repetitive_garbage(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if low.count("no agregue texto") >= 2 or low.count("no agregues texto") >= 2:
        return True
    if re.search(r"(?i)(\b[\wáéíóúñ]{2,}\b(?:\s+\b[\wáéíóúñ]{2,}\b){1,5})\s+(?:\1\b[\s\.,;:!?-]*){3,}", low):
        return True
    words = re.findall(r"[A-Za-zÁÉÍÓÚáéíóúÑñ0-9]+", raw)
    if len(words) < 40:
        return False
    uniq_ratio = len(set(word.lower() for word in words)) / max(1, len(words))
    return uniq_ratio < 0.25


def _count_mojibake(text: str) -> int:
    raw = str(text or "")
    return sum(raw.count(token) for token in _MOJIBAKE_TOKENS)


def _count_delimiter_imbalance(text: str) -> int:
    raw = re.sub(r"(?<![A-Za-z0-9])([A-Ea-e])\)\s*", "", str(text or ""))
    imbalance = 0
    for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
        imbalance += abs(raw.count(opening) - raw.count(closing))
    return imbalance


def _canonical_similarity_text(text: str) -> str:
    raw = str(text or "").lower()
    raw = re.sub(r"(?<![A-Za-z0-9])([a-e])\)\s*", r"\1) ", raw)
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


def _artifact_presence(text: str) -> set[str]:
    raw = str(text or "")
    present: set[str] = set()
    for token in _RARE_ARTIFACT_TOKENS:
        if token in raw:
            present.add(token)
    return present


def _ordered_unique(values: Sequence[str]) -> List[str]:
    ordered: List[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in ordered:
            ordered.append(item)
    return ordered


def resolve_hf_ocr_ensemble_models(
    *,
    current_model: str,
    available_models: Sequence[str],
    unavailable_models: Sequence[str] | None = None,
) -> List[str]:
    configured_raw = (os.getenv("HF_OCR_ENSEMBLE_MODELS", "") or "").strip()
    if configured_raw:
        configured = [part.strip() for part in configured_raw.split(",") if part.strip()]
    else:
        configured = list(DEFAULT_HF_OCR_ENSEMBLE_MODELS)

    unavailable = {str(value or "").strip() for value in list(unavailable_models or []) if str(value or "").strip()}
    available = {str(value or "").strip() for value in list(available_models or []) if str(value or "").strip()}
    if not available:
        available = set(configured)
        current = str(current_model or "").strip()
        if current:
            available.add(current)

    ordered = _ordered_unique(configured)
    current = str(current_model or "").strip()
    if current and current in available and current not in unavailable and current not in ordered:
        ordered.append(current)

    return [model for model in ordered if model in available and model not in unavailable]


def analyze_ocr_candidate(
    *,
    model: str,
    raw_text: str,
    curso: str,
    tema: str,
    start_n: int,
) -> OCRCandidateAnalysis:
    raw = str(raw_text or "").strip()
    extractor = ScanExtractor(provider="ocr", model="", strict_json=False)
    items, structured_raw = extractor.build_local_structured_output(
        raw_output=raw,
        curso=curso,
        tema=tema,
        start_n=start_n,
    )
    structured: dict[str, object] = {}
    if isinstance(structured_raw, str):
        try:
            parsed_structured = json.loads(structured_raw)
            if isinstance(parsed_structured, dict):
                structured = parsed_structured
        except Exception:
            structured = {}
    parsed_numbers: List[int] = []
    complete_option_items = 0
    option_label_total = 0
    for item in items:
        try:
            parsed_numbers.append(int(item.get("n", 0) or 0))
        except Exception:
            continue
        options = item.get("options", {}) if isinstance(item, dict) else {}
        if not isinstance(options, dict):
            options = {}
        non_empty = sum(1 for label in ("A", "B", "C", "D", "E") if str(options.get(label, "") or "").strip())
        option_label_total += non_empty
        if non_empty == 5:
            complete_option_items += 1

    header_numbers = extract_numbered_headers(raw)
    mojibake_hits = _count_mojibake(raw)
    delimiter_imbalance = _count_delimiter_imbalance(raw)
    leading_continuation = str(structured.get("leading_continuation", "") or "").strip()
    leading_options = structured.get("leading_options", {}) if isinstance(structured, dict) else {}
    if not isinstance(leading_options, dict):
        leading_options = {}
    leading_option_count = sum(
        1 for label in ("A", "B", "C", "D", "E") if str(leading_options.get(label, "") or "").strip()
    )
    continuation_candidate = bool((not items) and (leading_continuation or leading_option_count >= 3))

    score = 0
    penalties: List[str] = []
    bonuses: List[str] = []

    if raw:
        score += 5
        bonuses.append("texto_no_vacio")
    if items:
        score += 22
        bonuses.append("items_detectados")
    elif continuation_candidate:
        score += 18
        bonuses.append("continuacion_valida")
    else:
        penalties.append("sin_items")
        score -= 40

    if parsed_numbers:
        expected = list(range(start_n, start_n + len(parsed_numbers)))
        numbering_restart = _looks_like_numbering_restart_values(
            parsed_numbers=parsed_numbers,
            start_n=start_n,
            item_count=len(items),
            complete_option_items=complete_option_items,
            option_label_total=option_label_total,
        )
        if parsed_numbers == expected:
            score += 26
            bonuses.append("numeracion_secuencial")
        elif parsed_numbers == list(range(parsed_numbers[0], parsed_numbers[0] + len(parsed_numbers))):
            score += 16
            bonuses.append("numeracion_contigua")
        if parsed_numbers[0] == start_n:
            score += 12
            bonuses.append("arranque_esperado")
        elif numbering_restart:
            score += 10
            bonuses.append("reinicio_numeracion")
        elif parsed_numbers[0] < start_n:
            score -= 18
            penalties.append("arranque_regresivo")

    if header_numbers:
        score += 10
        bonuses.append("headers_visibles")
    elif continuation_candidate:
        score += 6
        bonuses.append("sin_headers_pero_continuacion")
    else:
        penalties.append("sin_headers")

    if complete_option_items:
        bonus = complete_option_items * 6
        score += bonus
        bonuses.append(f"items_con_AE={complete_option_items}")
    elif option_label_total:
        score += min(8, option_label_total)
        bonuses.append(f"opciones_detectadas={option_label_total}")
    elif leading_option_count:
        score += min(6, leading_option_count)
        bonuses.append(f"leading_options={leading_option_count}")

    if _looks_like_instruction_echo(raw):
        score -= 100
        penalties.append("echo_prompt")
    has_full_structure = bool(
        items
        and parsed_numbers
        and len(parsed_numbers) == len(items)
        and complete_option_items == len(items)
    )
    if _looks_like_repetitive_garbage(raw) and not has_full_structure:
        score -= 60
        penalties.append("basura_repetitiva")
    if mojibake_hits:
        score -= min(36, mojibake_hits * 6)
        penalties.append(f"mojibake={mojibake_hits}")
    if delimiter_imbalance:
        score -= min(20, delimiter_imbalance * 4)
        penalties.append(f"delimitadores={delimiter_imbalance}")
    if "\\overline" in raw:
        score -= 10
        penalties.append("overline_sospechoso")
    if ("\\sqrt" in raw or "√" in raw) and not _sqrt_is_expected_for_topic(tema):
        score -= 8
        penalties.append("sqrt_sospechoso")
    if _LATEX_HINT_RE.search(raw) and not mojibake_hits:
        score += 4
        bonuses.append("latex_cercano")

    return OCRCandidateAnalysis(
        model=str(model or "").strip(),
        raw_text=raw,
        score=score,
        header_numbers=header_numbers,
        parsed_numbers=parsed_numbers,
        item_count=len(items),
        complete_option_items=complete_option_items,
        option_label_total=option_label_total,
        leading_option_count=leading_option_count,
        has_leading_continuation=bool(leading_continuation),
        continuation_candidate=continuation_candidate,
        mojibake_hits=mojibake_hits,
        delimiter_imbalance=delimiter_imbalance,
        penalties=penalties,
        bonuses=bonuses,
    )


def is_ocr_candidate_valid(
    analysis: OCRCandidateAnalysis,
    *,
    start_n: int,
) -> tuple[bool, List[str]]:
    reasons: List[str] = []
    penalties = set(analysis.penalties)
    structurally_strong, metrics = _is_structurally_strong(analysis, start_n=start_n)
    prefix_len, complete_items, item_count, parsed_len, header_len, continuation_flag = metrics
    numbering_restart = _looks_like_numbering_restart(analysis, start_n=start_n)

    if not str(analysis.raw_text or "").strip():
        reasons.append("vacio")
    if "echo_prompt" in penalties:
        reasons.append("echo_prompt")
    if analysis.mojibake_hits >= 2 or (analysis.mojibake_hits > 0 and not structurally_strong):
        reasons.append("mojibake")
    if analysis.delimiter_imbalance >= 4 and not structurally_strong:
        reasons.append("delimitadores_graves")
    if analysis.item_count <= 0 and not analysis.continuation_candidate:
        reasons.append("sin_items")
    if analysis.item_count > 0:
        if not analysis.parsed_numbers and header_len <= 0:
            reasons.append("sin_numeracion_recuperable")
        elif int(start_n or 0) > 0 and not continuation_flag and prefix_len <= 0 and not numbering_restart:
            reasons.append("cobertura_inicial_insuficiente")
        elif analysis.parsed_numbers[0] < start_n and not numbering_restart:
            reasons.append("arranque_regresivo")
        if analysis.option_label_total > 0 and analysis.complete_option_items <= 0 and not (
            prefix_len > 0 and analysis.option_label_total >= 3
        ):
            reasons.append("opciones_incompletas")
    elif analysis.continuation_candidate and analysis.leading_option_count <= 0 and not analysis.has_leading_continuation:
        reasons.append("continuacion_debil")

    return (len(reasons) == 0, reasons)


def is_ocr_candidate_recoverable(
    analysis: OCRCandidateAnalysis,
    *,
    start_n: int,
) -> tuple[bool, List[str]]:
    reasons: List[str] = []
    penalties = set(analysis.penalties)
    structurally_strong, metrics = _is_structurally_strong(analysis, start_n=start_n)
    prefix_len, _complete_items, item_count, parsed_len, header_len, continuation_flag = metrics
    numbering_restart = _looks_like_numbering_restart(analysis, start_n=start_n)
    strong_misaligned_start = _looks_like_strong_misaligned_start(analysis, start_n=start_n)

    if not str(analysis.raw_text or "").strip():
        reasons.append("vacio")
    if "echo_prompt" in penalties:
        reasons.append("echo_prompt")
    if analysis.mojibake_hits >= 4 and not structurally_strong:
        reasons.append("mojibake_grave")

    if continuation_flag:
        if analysis.leading_option_count <= 0 and not analysis.has_leading_continuation:
            reasons.append("continuacion_debil")
        return (len(reasons) == 0, reasons)

    if item_count <= 0:
        reasons.append("sin_items")
        return (False, reasons)

    has_number_signal = bool(parsed_len > 0 or header_len > 0)
    if not has_number_signal:
        reasons.append("sin_numeracion_recuperable")

    if (
        analysis.parsed_numbers
        and analysis.parsed_numbers[0] < start_n
        and not numbering_restart
        and not strong_misaligned_start
    ):
        reasons.append("arranque_regresivo")

    option_coverage = bool(analysis.complete_option_items > 0 or analysis.option_label_total >= 3)
    if not option_coverage:
        reasons.append("sin_cobertura_opciones")

    if analysis.delimiter_imbalance >= 6 and prefix_len <= 0 and not structurally_strong:
        reasons.append("delimitadores_graves")

    return (len(reasons) == 0, reasons)


def should_accept_ocr_candidate_fast(
    analysis: OCRCandidateAnalysis,
    *,
    start_n: int,
    min_score: int = 80,
) -> tuple[bool, List[str]]:
    valid, reasons = is_ocr_candidate_valid(analysis, start_n=start_n)
    if not valid:
        return (False, reasons)
    if analysis.score < int(min_score):
        reasons = list(reasons)
        reasons.append(f"score<{int(min_score)}")
        return (False, reasons)
    return (True, [])


def select_best_ocr_candidate(
    candidates: Sequence[OCRCandidateAnalysis],
    *,
    start_n: int = 0,
) -> OCRCandidateAnalysis:
    if not candidates:
        raise ValueError("No OCR candidates provided")

    enriched = list(candidates)
    signature_counter = Counter(tuple(candidate.parsed_numbers) for candidate in enriched if candidate.parsed_numbers)
    item_count_counter = Counter(candidate.item_count for candidate in enriched if candidate.item_count > 0)
    artifact_counter: Counter[str] = Counter()
    artifact_map = {id(candidate): _artifact_presence(candidate.raw_text) for candidate in enriched}
    for tokens in artifact_map.values():
        artifact_counter.update(tokens)

    for candidate in enriched:
        signature = tuple(candidate.parsed_numbers)
        if signature and signature_counter[signature] > 1:
            candidate.score += 12
            candidate.bonuses.append("consenso_numeracion")
        if candidate.item_count > 0 and item_count_counter[candidate.item_count] > 1:
            candidate.score += 6
            candidate.bonuses.append("consenso_items")

    canonical_map = {id(candidate): _canonical_similarity_text(candidate.raw_text) for candidate in enriched}
    for candidate in enriched:
        others = [other for other in enriched if other is not candidate]
        if not others:
            continue
        ratios = [
            SequenceMatcher(None, canonical_map[id(candidate)], canonical_map[id(other)]).ratio()
            for other in others
        ]
        if not ratios:
            continue
        avg_ratio = sum(ratios) / len(ratios)
        candidate.score += int(avg_ratio * 10)
        if max(ratios) >= 0.85:
            candidate.score += 6
            candidate.bonuses.append("consenso_textual")
        elif avg_ratio < 0.72:
            candidate.score -= 8
            candidate.penalties.append("outlier_textual")

        rare_tokens = [
            token
            for token in artifact_map[id(candidate)]
            if artifact_counter[token] == 1
        ]
        if rare_tokens:
            penalty = min(18, len(rare_tokens) * 9)
            candidate.score -= penalty
            candidate.penalties.append("artefacto_unico=" + ",".join(sorted(rare_tokens)))

    return max(
        enriched,
        key=lambda candidate: (
            *_structural_completeness_key(candidate, start_n=start_n),
            candidate.score,
            MODEL_PREFERENCE.get(candidate.model, 0),
            -candidate.mojibake_hits,
            -candidate.delimiter_imbalance,
        ),
    )
