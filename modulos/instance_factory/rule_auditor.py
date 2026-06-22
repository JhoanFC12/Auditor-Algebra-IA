from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


REPORT_SCHEMA_VERSION = "ocr_normalizer_rule_audit_report_v1"
RECORD_SCHEMA_VERSION = "ocr_normalizer_rule_audit_record_v1"

TARGET_OCR = "ocr"
TARGET_NORMALIZER = "normalizer"

ERROR = "error"
WARNING = "warning"

OPTION_LABELS = ("A", "B", "C", "D", "E")
FINAL_SEP_LINE = "\u00a3"
FINAL_SEP_OPT = "\u00e6"

RULE_ORDER = (
    "unit_spacing",
    "angle_symbol",
    "degree_format",
    "option_spacing",
    "continuation_marker",
    "segment_vs_length",
    "arc_vs_measure",
    "numeric_sets",
    "hallucination_risk",
    "final_format_valid",
    "alternatives_complete",
)


@dataclass(frozen=True)
class RuleMetric:
    name: str
    status: str
    severity: str
    violations: list[str]
    evidence: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "severity": self.severity,
            "violations": list(self.violations),
            "evidence": list(self.evidence),
        }


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _metric(
    name: str,
    violations: Iterable[str] = (),
    *,
    evidence: Iterable[str] = (),
    severity: str = ERROR,
    applies: bool = True,
) -> RuleMetric:
    rows = [str(item) for item in violations if str(item or "").strip()]
    status = "pass" if applies and not rows else ("fail" if rows else "not_applicable")
    return RuleMetric(
        name=name,
        status=status,
        severity=severity,
        violations=rows,
        evidence=[str(item)[:220] for item in evidence if str(item or "").strip()],
    )


def _regex_evidence(pattern: str, text: str, *, flags: int = 0, limit: int = 5) -> list[str]:
    out: list[str] = []
    for match in re.finditer(pattern, text, flags):
        start = max(0, match.start() - 20)
        end = min(len(text), match.end() + 20)
        out.append(text[start:end].replace("\n", " "))
        if len(out) >= limit:
            break
    return out


def _inside_latex_math(text: str, position: int) -> bool:
    dollar_count = 0
    index = 0
    while index < max(0, position):
        if text[index] == "$" and (index == 0 or text[index - 1] != "\\"):
            dollar_count += 1
        index += 1
    return bool(dollar_count % 2)


def _option_counts(text: str) -> dict[str, int]:
    counts = {label: 0 for label in OPTION_LABELS}
    for match in re.finditer(r"(?<![A-Za-z])([A-E])\)", text):
        if _inside_latex_math(text, match.start(1)):
            continue
        counts[match.group(1)] += 1
    return counts


def _has_any_option(text: str) -> bool:
    return any(_option_counts(text).values())


def _has_final_option_markers(text: str) -> bool:
    return bool(re.search(rf"(?:{FINAL_SEP_LINE}|{FINAL_SEP_OPT})[A-E]\)", text))


def _final_option_marker_counts(text: str) -> dict[str, int]:
    counts = {label: 0 for label in OPTION_LABELS}
    for label in re.findall(rf"(?:{FINAL_SEP_LINE}|{FINAL_SEP_OPT})([A-E])\)", text):
        counts[label] += 1
    return counts


def _extract_final_options(text: str) -> dict[str, str] | None:
    match = re.search(
        rf"{FINAL_SEP_LINE}A\)([\s\S]*?){FINAL_SEP_OPT}B\)([\s\S]*?){FINAL_SEP_OPT}C\)([\s\S]*?)"
        rf"{FINAL_SEP_LINE}D\)([\s\S]*?){FINAL_SEP_OPT}{FINAL_SEP_OPT}E\)([\s\S]*?){FINAL_SEP_LINE}",
        text,
    )
    if not match:
        return None
    return dict(zip(OPTION_LABELS, (item.strip() for item in match.groups())))


def _metric_unit_spacing(text: str) -> RuleMetric:
    unit = r"(?:u|cm|mm|m|km|dm|in|ft|cm\^2|m\^2|cm\^3|m\^3)"
    violations: list[str] = []
    evidence: list[str] = []
    patterns = [
        (rf"\$\s*\d+(?:[.,]\d+)?\s*,\s*{unit}\b", "unit_comma_in_math"),
        (rf"\$\s*\d+(?:[.,]\d+)?\s*(?<!\\,){unit}\b", "missing_latex_thin_space"),
        (rf"\b\d+(?:[.,]\d+)?\s*,\s*{unit}\b", "unit_comma"),
    ]
    for pattern, code in patterns:
        rows = _regex_evidence(pattern, text, flags=re.IGNORECASE)
        if rows:
            violations.append(code)
            evidence.extend(rows)
    return _metric("unit_spacing", violations, evidence=evidence)


def _metric_angle_symbol(text: str) -> RuleMetric:
    violations: list[str] = []
    evidence: list[str] = []
    patterns = [
        (r"\\angle\b", "uses_angle_not_sphericalangle"),
        (r"\u2220", "unicode_angle_symbol"),
        (r"\bm\s*<\s*[A-Z]{3,4}\b", "angle_measure_as_less_than"),
        (r"(?<![<>=])<\s*[A-Z]{3,4}\b", "angle_as_less_than"),
    ]
    for pattern, code in patterns:
        rows = _regex_evidence(pattern, text)
        if rows:
            violations.append(code)
            evidence.extend(rows)
    return _metric("angle_symbol", violations, evidence=evidence)


def _metric_degree_format(text: str) -> RuleMetric:
    violations: list[str] = []
    evidence: list[str] = []
    patterns = [
        (r"\d+\s*[\u00ba\u00b0]", "unicode_degree"),
        (r"\d+\s*\\circ\b", "missing_degree_caret"),
        (r"(?<!\^)\{?\\circ\}?", "circ_without_caret"),
    ]
    for pattern, code in patterns:
        rows = _regex_evidence(pattern, text)
        if rows:
            violations.append(code)
            evidence.extend(rows)
    return _metric("degree_format", violations, evidence=evidence)


def _metric_option_spacing(text: str) -> RuleMetric:
    rows: list[str] = []
    for match in re.finditer(r"(?<![A-Za-z])([A-E])\)(?=\S)", text):
        if _inside_latex_math(text, match.start(1)):
            continue
        start = max(0, match.start() - 20)
        end = min(len(text), match.end() + 20)
        rows.append(text[start:end].replace("\n", " "))
        if len(rows) >= 5:
            break
    return _metric("option_spacing", ["missing_space_after_option_label"] if rows else [], evidence=rows)


def _metric_continuation_marker(text: str) -> RuleMetric:
    markers = re.findall(r"\[(?:\s*CONT(?:INUACION|INUACI[OÓ]N)?\.?\s*)\]", text, flags=re.IGNORECASE)
    bad = [item for item in markers if item != "[CONT.]"]
    if re.search(r"\bCONT(?:INUACION|INUACI[OÓ]N)?\.?\b", text, flags=re.IGNORECASE) and "[CONT.]" not in text:
        bad.append("plain_continuation_marker")
    return _metric("continuation_marker", ["invalid_continuation_marker"] if bad else [], evidence=bad)


def _metric_segment_vs_length(text: str) -> RuleMetric:
    violations: list[str] = []
    evidence: list[str] = []
    patterns = [
        (r"\bm\s*\\overline\s*\{[A-Z]{2}\}", "segment_has_measure_prefix"),
        (r"(?:calcule|halle|medida|longitud)\s+\$?\\overline\s*\{[A-Z]{2}\}", "length_requested_as_segment_object"),
        (r"(?:segmento|segment)\s+\$?[A-Z]{2}\$?(?![A-Za-z])", "segment_object_without_overline"),
    ]
    for pattern, code in patterns:
        rows = _regex_evidence(pattern, text, flags=re.IGNORECASE)
        if rows:
            violations.append(code)
            evidence.extend(rows)
    return _metric("segment_vs_length", violations, evidence=evidence)


def _metric_arc_vs_measure(text: str) -> RuleMetric:
    violations: list[str] = []
    evidence: list[str] = []
    patterns = [
        (r"\\wideparen\s*\{[A-Z]{2,3}\}", "uses_wideparen_not_overparen"),
        (r"\bm\s*\\wideparen\s*\{[A-Z]{2,3}\}", "arc_measure_uses_wideparen"),
        (r"(?:medida\s+del\s+arco|m\s+del\s+arco)\s+\$?\\overparen\s*\{[A-Z]{2,3}\}", "arc_measure_without_m_prefix"),
    ]
    for pattern, code in patterns:
        rows = _regex_evidence(pattern, text, flags=re.IGNORECASE)
        if rows:
            violations.append(code)
            evidence.extend(rows)
    return _metric("arc_vs_measure", violations, evidence=evidence)


def _metric_numeric_sets(text: str) -> RuleMetric:
    violations: list[str] = []
    evidence: list[str] = []
    patterns = [
        (r"[\u2115\u2124\u211a\u211d\u2102]", "unicode_numeric_set"),
        (r"\\(?:mathbf|mathrm)\s*\{[NZQRC]\}", "non_mathbb_numeric_set"),
    ]
    for pattern, code in patterns:
        rows = _regex_evidence(pattern, text)
        if rows:
            violations.append(code)
            evidence.extend(rows)
    return _metric("numeric_sets", violations, evidence=evidence)


def _figure_count(row: dict[str, Any]) -> int:
    figure = row.get("figure_segmentation") if isinstance(row.get("figure_segmentation"), dict) else {}
    for key in ("segments_total", "figure_count"):
        try:
            value = int(figure.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    segments = figure.get("segments")
    if isinstance(segments, list) and segments:
        return len(segments)
    images = row.get("images")
    if isinstance(images, list) and images:
        return len(images)
    for payload in _embedded_normalizer_inputs(row):
        nested_count = _figure_count(payload)
        if nested_count > 0:
            return nested_count
    return 0


def _embedded_normalizer_inputs(row: dict[str, Any]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    candidates: list[Any] = [row.get("normalizer_input"), row.get("input")]
    for key in ("prompt", "messages"):
        value = row.get(key)
        if isinstance(value, list):
            candidates.extend(
                item.get("content")
                for item in value
                if isinstance(item, dict) and str(item.get("role") or "").lower() == "user"
            )
        else:
            candidates.append(value)
    for value in candidates:
        if isinstance(value, dict):
            payloads.append(value)
            continue
        if not isinstance(value, str) or not value.strip().startswith("{"):
            continue
        try:
            parsed = json.loads(value)
        except Exception:
            continue
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


def _metric_hallucination_risk(text: str, row: dict[str, Any], *, target: str) -> RuleMetric:
    violations: list[str] = []
    evidence: list[str] = []
    if target == TARGET_OCR:
        patterns = [
            (r"\[\[Imagen=", "ocr_contains_image_tag"),
            (r"\b(?:la imagen muestra|se observa en la figura|el grafico muestra)\b", "ocr_graph_description_risk"),
        ]
    else:
        patterns = [
            (r"\b(?:por lo tanto|soluci[oó]n|resolviendo)\b", "final_contains_solution_language"),
            (r"\[\[Imagen=", "final_contains_image_tag"),
        ]
    for pattern, code in patterns:
        rows = _regex_evidence(pattern, text, flags=re.IGNORECASE)
        if rows:
            if code == "final_contains_image_tag" and _figure_count(row) > 0:
                continue
            violations.append(code)
            evidence.extend(rows)
    return _metric("hallucination_risk", violations, evidence=evidence, severity=WARNING)


def _metric_final_format_valid(text: str) -> RuleMetric:
    violations: list[str] = []
    evidence: list[str] = []
    required_patterns = [
        (r"^\\item\s*\[\s*\\textbf\{\s*\d{1,4}\.\s*\}\s*\]", "missing_item_number"),
        (r"\[\[curso=[^\]]*\]\]", "missing_curso_tag"),
        (r"\[\[tema=[^\]]*\]\]", "missing_tema_tag"),
        (r"\[\[Estado=sin_revisar\]\]", "missing_estado_tag"),
        (r"\[\[Clave=[^\]]*\]\]", "missing_clave_tag"),
    ]
    for pattern, code in required_patterns:
        if not re.search(pattern, text):
            violations.append(code)
    if (_has_any_option(text) or _has_final_option_markers(text)) and _extract_final_options(text) is None:
        violations.append("missing_final_option_separator_pattern")
        evidence.extend(_regex_evidence(r"[A-E]\)", text))
    return _metric("final_format_valid", violations, evidence=evidence)


def _metric_alternatives_complete(text: str, *, target: str) -> RuleMetric:
    if target == TARGET_NORMALIZER:
        final_options = _extract_final_options(text)
        if final_options is None:
            if not _has_any_option(text) and not _has_final_option_markers(text):
                return _metric("alternatives_complete", applies=False)
            counts = _option_counts(text)
            missing = [label for label, count in counts.items() if count < 1]
            duplicated = [label for label, count in counts.items() if count > 1]
            return _metric(
                "alternatives_complete",
                [f"missing:{label}" for label in missing] + [f"duplicated:{label}" for label in duplicated],
                evidence=_regex_evidence(r"[A-E]\)", text),
            )
        empty = [label for label, value in final_options.items() if not value]
        counts = _final_option_marker_counts(text)
        duplicated = [label for label, count in counts.items() if count > 1]
        return _metric(
            "alternatives_complete",
            [f"empty:{label}" for label in empty] + [f"duplicated:{label}" for label in duplicated],
            evidence=[],
        )
    if not _has_any_option(text):
        return _metric("alternatives_complete", applies=False)
    counts = _option_counts(text)
    missing = [label for label, count in counts.items() if count < 1]
    duplicated = [label for label, count in counts.items() if count > 1]
    return _metric(
        "alternatives_complete",
        [f"missing:{label}" for label in missing] + [f"duplicated:{label}" for label in duplicated],
        evidence=_regex_evidence(r"[A-E]\)", text),
    )


def audit_text(text: Any, *, target: str, row: dict[str, Any] | None = None) -> dict[str, Any]:
    row = row or {}
    normalized_target = TARGET_NORMALIZER if str(target).lower() == TARGET_NORMALIZER else TARGET_OCR
    clean = _clean_text(text)
    metrics = [
        _metric_unit_spacing(clean),
        _metric_angle_symbol(clean),
        _metric_degree_format(clean),
        _metric_option_spacing(clean),
        _metric_continuation_marker(clean),
        _metric_segment_vs_length(clean),
        _metric_arc_vs_measure(clean),
        _metric_numeric_sets(clean),
        _metric_hallucination_risk(clean, row, target=normalized_target),
    ]
    if normalized_target == TARGET_NORMALIZER:
        metrics.extend(
            [
                _metric_final_format_valid(clean),
                _metric_alternatives_complete(clean, target=normalized_target),
            ]
        )
    else:
        metrics.extend(
            [
                _metric("final_format_valid", applies=False),
                _metric_alternatives_complete(clean, target=normalized_target),
            ]
        )
    failures = [item for item in metrics if item.status == "fail"]
    blocking_failures = [item for item in failures if item.severity == ERROR]
    return {
        "target": normalized_target,
        "text_chars": len(clean),
        "passed": not failures,
        "eligible_for_training": not blocking_failures and not any(item.name == "hallucination_risk" for item in failures),
        "failed_rules": [item.name for item in failures],
        "blocking_rules": [item.name for item in blocking_failures],
        "metrics": {item.name: item.to_dict() for item in metrics},
    }


def _completion_text(row: dict[str, Any]) -> str:
    for key in ("final_latex", "prediction", "completion", "target"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value
    completion = row.get("completion")
    if isinstance(completion, list):
        return " ".join(str(item.get("content") or "") for item in completion if isinstance(item, dict)).strip()
    messages = row.get("messages")
    if isinstance(messages, list):
        assistant = [
            str(item.get("content") or "")
            for item in messages
            if isinstance(item, dict) and str(item.get("role") or "").lower() == "assistant"
        ]
        if assistant:
            return assistant[-1]
    normalized = row.get("normalized") if isinstance(row.get("normalized"), dict) else {}
    if normalized.get("latex_rendered_item"):
        return str(normalized.get("latex_rendered_item") or "")
    normalized_human = row.get("normalized_human") if isinstance(row.get("normalized_human"), dict) else {}
    if normalized_human.get("latex_rendered_item"):
        return str(normalized_human.get("latex_rendered_item") or "")
    return ""


def audit_row(row: dict[str, Any], *, mode: str = "auto") -> dict[str, Any]:
    raw_ocr = _clean_text(row.get("raw_ocr") or row.get("ocr") or row.get("text") or "")
    final_latex = _clean_text(_completion_text(row))
    normalized_mode = str(mode or "auto").lower()
    audits: dict[str, Any] = {}
    if normalized_mode in {"auto", "both", TARGET_OCR} and raw_ocr:
        audits[TARGET_OCR] = audit_text(raw_ocr, target=TARGET_OCR, row=row)
    if normalized_mode in {"auto", "both", TARGET_NORMALIZER} and final_latex:
        audits[TARGET_NORMALIZER] = audit_text(final_latex, target=TARGET_NORMALIZER, row=row)
    if normalized_mode == TARGET_OCR:
        audits.pop(TARGET_NORMALIZER, None)
    if normalized_mode == TARGET_NORMALIZER:
        audits.pop(TARGET_OCR, None)
    eligible = bool(audits) and all(item.get("eligible_for_training") for item in audits.values())
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "record_id": str(row.get("record_id") or row.get("id") or row.get("sample_id") or row.get("crop_id") or ""),
        "sample_id": str(row.get("sample_id") or ""),
        "source": row.get("source") if isinstance(row.get("source"), dict) else {},
        "audited_at": _now(),
        "eligible_for_training": eligible,
        "audits": audits,
    }


def summarize_audits(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_target: dict[str, Any] = {}
    for target in (TARGET_OCR, TARGET_NORMALIZER):
        target_records = [row for row in records if target in row.get("audits", {})]
        rule_counts = {
            name: {"pass": 0, "fail": 0, "not_applicable": 0}
            for name in RULE_ORDER
        }
        for record in target_records:
            metrics = record["audits"][target].get("metrics", {})
            for name in RULE_ORDER:
                status = str((metrics.get(name) or {}).get("status") or "not_applicable")
                if status not in rule_counts[name]:
                    status = "not_applicable"
                rule_counts[name][status] += 1
        by_target[target] = {
            "records": len(target_records),
            "passed": sum(1 for row in target_records if row["audits"][target].get("passed")),
            "eligible_for_training": sum(
                1 for row in target_records if row["audits"][target].get("eligible_for_training")
            ),
            "rules": rule_counts,
        }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "created_at": _now(),
        "records_total": len(records),
        "eligible_records_total": sum(1 for row in records if row.get("eligible_for_training")),
        "targets": by_target,
        "policy": {
            "writes_to_staging": False,
            "writes_to_problemas": False,
            "eligible_samples_file": "eligible_samples.jsonl",
            "blocking_severity": ERROR,
        },
    }


def write_audit_report(rows: list[dict[str, Any]], *, out_dir: Path, mode: str = "auto") -> dict[str, Any]:
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_records = [audit_row(row, mode=mode) for row in rows]
    summary = summarize_audits(audit_records)
    (out_dir / "records.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in audit_records),
        encoding="utf-8",
    )
    (out_dir / "eligible_samples.jsonl").write_text(
        "".join(
            json.dumps({"audit": audit, "sample": rows[index]}, ensure_ascii=False) + "\n"
            for index, audit in enumerate(audit_records)
            if audit.get("eligible_for_training")
        ),
        encoding="utf-8",
    )
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary
