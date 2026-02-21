from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from ..latex_normalizer import normalize_option, normalize_statement
from .extractor import IMAGE_EXTS, ScanExtractor
from .key_classifier import KeyClassification, classify_key_image, classify_key_text
from .renderer import render_document, render_item
from .schema import SCAN_SCHEMA, ScanItem
from .validator import validate_item_json, validate_rendered_item


def _natural_key(name: str) -> List[Any]:
    return [int(ch) if ch.isdigit() else ch.lower() for ch in re.split(r"(\d+)", name)]


def _excerpt(text: str, limit: int = 400) -> str:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(raw) <= limit:
        return raw
    return f"{raw[:limit]}..."


@dataclass
class PipelineItemResult:
    item: ScanItem
    rendered: str
    json_errors: List[str] = field(default_factory=list)
    render_errors: List[str] = field(default_factory=list)
    unknown_symbols: List[str] = field(default_factory=list)
    latex_warnings: List[str] = field(default_factory=list)
    latex_validation_errors: List[str] = field(default_factory=list)
    latex_normalized_changed: bool = False
    retries_used: int = 0
    source: str = ""
    raw_output: str = ""

    @property
    def errors(self) -> List[str]:
        out = list(self.json_errors)
        out.extend(self.render_errors)
        return out


@dataclass
class PipelineRunResult:
    items: List[PipelineItemResult] = field(default_factory=list)
    rendered_document: str = ""
    skipped_images: List[Dict[str, Any]] = field(default_factory=list)
    needs_review_count: int = 0
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    parse_failures: List[Dict[str, Any]] = field(default_factory=list)
    json_parse_failed_count: int = 0

    def rendered_items(self) -> List[str]:
        return [entry.rendered for entry in self.items]

    def to_report_dict(self) -> Dict[str, Any]:
        return {
            "items_total": len(self.items),
            "needs_review_count": int(self.needs_review_count),
            "json_parse_failed_count": int(self.json_parse_failed_count),
            "parse_failures": list(self.parse_failures),
            "skipped_images": list(self.skipped_images),
            "items": [
                {
                    "item": entry.item.to_dict(),
                    "json_errors": list(entry.json_errors),
                    "render_errors": list(entry.render_errors),
                    "unknown_symbols": list(entry.unknown_symbols),
                    "latex_warnings": list(entry.latex_warnings),
                    "latex_validation_errors": list(entry.latex_validation_errors),
                    "latex_normalized_changed": bool(entry.latex_normalized_changed),
                    "retries_used": int(entry.retries_used),
                    "source": str(entry.source),
                }
                for entry in self.items
            ],
            "diagnostics": list(self.diagnostics),
        }


class ScanPipeline:
    def __init__(
        self,
        *,
        provider: str = "hf",
        model: str = "",
        max_retries: int = 2,
        timeout_s: int = 180,
        debug_dir: str = "",
        ocr_lang: str = "spa+eng",
        temperature: float = 0.0,
        top_p: float = 1.0,
        max_tokens: int = 3200,
        seed: int | None = 42,
        strict_json: bool = True,
        parse_max_retries: int | None = None,
    ) -> None:
        self.provider = (provider or "hf").strip().lower()
        self.max_retries = max(0, int(max_retries))
        self.parse_max_retries = self.max_retries if parse_max_retries is None else max(0, int(parse_max_retries))
        self.ocr_lang = ocr_lang
        self.strict_json = bool(strict_json) if self.provider != "ocr" else False
        self.debug_dir = Path(debug_dir).resolve() if debug_dir else None
        if self.debug_dir is not None:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
        self.extractor = ScanExtractor(
            provider=self.provider,
            model=model,
            timeout_s=timeout_s,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            seed=seed,
            strict_json=self.strict_json,
        )

    def _save_debug(self, *, source_name: str, payload: Dict[str, Any]) -> None:
        if self.debug_dir is None:
            return
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", source_name).strip("._") or "debug"
        path = self.debug_dir / f"{safe}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _normalize_item(
        self,
        raw: Dict[str, Any],
        *,
        default_n: int,
        curso: str,
        tema: str,
    ) -> tuple[ScanItem, Dict[str, Any]]:
        item = ScanItem.from_dict(raw, default_n=default_n, curso=curso, tema=tema)
        item.schema = SCAN_SCHEMA
        item.curso = (curso or item.curso or "SIN_CURSO").strip()
        item.tema = (tema or item.tema or "SIN_TEMA").strip()
        item.n = max(1, int(item.n or default_n))
        statement_norm = normalize_statement(item.statement)
        item.statement = statement_norm.text or "[[ocr_sin_texto]]"
        unknown_symbols = list(statement_norm.unknown_symbols)
        latex_warnings = list(statement_norm.warnings)
        latex_changed = bool(statement_norm.changed)

        normalized_options: Dict[str, str] = {}
        for label in ("A", "B", "C", "D", "E"):
            opt_norm = normalize_option(item.options.get(label, "..."))
            wrapped = opt_norm.text or "$...$"
            body = wrapped[1:-1].strip() if wrapped.startswith("$") and wrapped.endswith("$") and len(wrapped) >= 2 else wrapped
            normalized_options[label] = body or "..."
            unknown_symbols.extend(opt_norm.unknown_symbols)
            latex_warnings.extend(opt_norm.warnings)
            latex_changed = latex_changed or opt_norm.changed
        item.options = normalized_options

        if item.has_figure:
            # Deterministic contract: figure tag is always img-n.
            item.figure_tag = f"img-{item.n}"
        else:
            item.figure_tag = ""

        meta = {
            "unknown_symbols": sorted(set(x for x in unknown_symbols if x)),
            "latex_warnings": sorted(set(x for x in latex_warnings if x)),
            "latex_normalized_changed": bool(latex_changed),
        }
        return (item, meta)

    def _parse_with_retry(
        self,
        *,
        image_path: Path,
        raw_output: str,
        curso: str,
        tema: str,
        start_n: int,
        initial_items: List[Dict[str, Any]] | None = None,
    ) -> tuple[List[Dict[str, Any]], str, int, List[str]]:
        parse_errors: List[str] = []
        current_raw = str(raw_output or "")

        parsed = list(initial_items or [])
        if not parsed:
            parsed = self.extractor.parse_raw_output(
                raw_output=current_raw,
                curso=curso,
                tema=tema,
                start_n=start_n,
                allow_text_fallback=(self.provider == "ocr") or (not self.strict_json),
            )
        if parsed:
            return (parsed, current_raw, 0, parse_errors)

        if self.provider == "ocr" or (not self.strict_json):
            parse_errors.append("parse_sin_items")
            return ([], current_raw, 0, parse_errors)

        parse_errors.append("json_parse_failed_attempt_0")
        retries_used = 0
        while retries_used < self.parse_max_retries:
            retries_used += 1
            repaired_items, repaired_raw = self.extractor.repair_raw_output(
                image_path=image_path,
                raw_output=current_raw,
                errors=parse_errors,
                curso=curso,
                tema=tema,
                start_n=start_n,
            )
            current_raw = repaired_raw
            if repaired_items:
                return (repaired_items, current_raw, retries_used, parse_errors)
            parse_errors.append(f"json_parse_failed_attempt_{retries_used}")

        parse_errors.append("json_parse_failed_final")
        return ([], current_raw, retries_used, parse_errors)

    def _record_parse_failure(
        self,
        *,
        run: PipelineRunResult,
        image_path: Path,
        parse_retries_used: int,
        parse_errors: List[str],
        raw_output: str,
    ) -> None:
        run.json_parse_failed_count += 1
        run.parse_failures.append(
            {
                "source": str(image_path),
                "parse_retries_used": int(parse_retries_used),
                "parse_errors": list(parse_errors),
                "raw_excerpt": _excerpt(raw_output, limit=500),
            }
        )

    def _validate_and_retry_item(
        self,
        *,
        item: ScanItem,
        normalize_meta: Dict[str, Any],
        image_path: Path,
        curso: str,
        tema: str,
    ) -> PipelineItemResult:
        retries_used = 0
        current = item
        current_meta = dict(normalize_meta or {})
        json_errors = validate_item_json(current)
        rendered = render_item(current)
        render_errors = validate_rendered_item(rendered, item=current)
        unknown_symbols = list(current_meta.get("unknown_symbols", []))
        latex_warnings = list(current_meta.get("latex_warnings", []))
        latex_normalized_changed = bool(current_meta.get("latex_normalized_changed", False))
        latex_validation_errors: List[str] = []
        if unknown_symbols:
            latex_validation_errors.append("unknown_symbols_detected")

        while (json_errors or render_errors or latex_validation_errors) and retries_used < self.max_retries:
            retries_used += 1
            corrected = self.extractor.correct_item(
                image_path=image_path,
                item=current.to_dict(),
                errors=[*json_errors, *render_errors, *latex_validation_errors],
                curso=curso,
                tema=tema,
            )
            current, current_meta = self._normalize_item(corrected, default_n=current.n, curso=curso, tema=tema)
            json_errors = validate_item_json(current)
            rendered = render_item(current)
            render_errors = validate_rendered_item(rendered, item=current)
            unknown_symbols = list(current_meta.get("unknown_symbols", []))
            latex_warnings = list(current_meta.get("latex_warnings", []))
            latex_normalized_changed = bool(current_meta.get("latex_normalized_changed", False)) or latex_normalized_changed
            latex_validation_errors = []
            if unknown_symbols:
                latex_validation_errors.append("unknown_symbols_detected")

        if json_errors or render_errors or latex_validation_errors:
            current.needs_review = True
            rendered = render_item(current)
            render_errors = validate_rendered_item(rendered, item=current)

        return PipelineItemResult(
            item=current,
            rendered=rendered,
            json_errors=list(json_errors),
            render_errors=list(render_errors),
            unknown_symbols=sorted(set(unknown_symbols)),
            latex_warnings=sorted(set(latex_warnings)),
            latex_validation_errors=list(latex_validation_errors),
            latex_normalized_changed=bool(latex_normalized_changed),
            retries_used=retries_used,
            source=str(image_path),
        )

    def process_raw_output(
        self,
        *,
        raw_output: str,
        image_path: Path,
        start_n: int,
        curso: str,
        tema: str,
        has_figure_hint: bool = False,
    ) -> PipelineRunResult:
        run = PipelineRunResult()
        key_cls = classify_key_text(raw_output, path=image_path)
        if key_cls.is_key_image:
            run.skipped_images.append(
                {
                    "source": str(image_path),
                    "reason": key_cls.reason,
                    "confidence": key_cls.confidence,
                }
            )
            run.rendered_document = render_document([])
            run.diagnostics.append(
                {
                    "source": str(image_path),
                    "status": "SKIPPED_KEY",
                    "reason": key_cls.reason,
                    "confidence": key_cls.confidence,
                }
            )
            return run

        parsed, effective_raw, parse_retries_used, parse_errors = self._parse_with_retry(
            image_path=image_path,
            raw_output=raw_output,
            curso=curso,
            tema=tema,
            start_n=start_n,
            initial_items=None,
        )
        parse_failure_fallback = False
        if not parsed:
            parse_failure_fallback = True
            self._record_parse_failure(
                run=run,
                image_path=image_path,
                parse_retries_used=parse_retries_used,
                parse_errors=parse_errors,
                raw_output=effective_raw,
            )
            parsed = [ScanItem.empty(n=start_n, curso=curso, tema=tema).to_dict()]

        next_seq = max(1, int(start_n))
        for idx, raw_item in enumerate(parsed):
            suggested_n = int(raw_item.get("n", 0) or 0)
            default_n = suggested_n if suggested_n > 0 else next_seq
            normalized, normalize_meta = self._normalize_item(raw_item, default_n=default_n, curso=curso, tema=tema)
            if suggested_n <= 0:
                normalized.n = next_seq
            if has_figure_hint and (not normalized.has_figure) and len(parsed) == 1:
                normalized.has_figure = True
                normalized.figure_tag = f"img-{normalized.n}"
            result_item = self._validate_and_retry_item(
                item=normalized,
                normalize_meta=normalize_meta,
                image_path=image_path,
                curso=curso,
                tema=tema,
            )
            run.items.append(result_item)
            next_seq = max(next_seq + 1, result_item.item.n + 1)

        run.needs_review_count = sum(1 for it in run.items if it.item.needs_review)
        run.rendered_document = render_document([it.item for it in run.items])
        run.diagnostics.extend(
            {
                "source": str(it.source),
                "n": int(it.item.n),
                "needs_review": bool(it.item.needs_review),
                "json_errors": list(it.json_errors),
                "render_errors": list(it.render_errors),
                "unknown_symbols": list(it.unknown_symbols),
                "latex_warnings": list(it.latex_warnings),
                "latex_validation_errors": list(it.latex_validation_errors),
                "latex_normalized_changed": bool(it.latex_normalized_changed),
                "retries_used": int(it.retries_used),
                "parse_retries_used": int(parse_retries_used),
                "parse_errors": list(parse_errors),
                "is_parse_failure_fallback": bool(parse_failure_fallback),
            }
            for it in run.items
        )
        self._save_debug(
            source_name=image_path.stem,
            payload={
                "source": str(image_path),
                "raw_output": raw_output,
                "effective_raw_output": effective_raw,
                "report": run.to_report_dict(),
            },
        )
        return run

    def run_on_folder(
        self,
        *,
        input_dir: Path,
        start_n: int,
        curso: str,
        tema: str,
    ) -> PipelineRunResult:
        folder = Path(input_dir)
        run = PipelineRunResult()
        if not folder.exists():
            raise FileNotFoundError(f"Input folder not found: {folder}")

        images = sorted(
            [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS],
            key=lambda p: _natural_key(p.name),
        )
        seq_n = max(1, int(start_n))

        for image_path in images:
            key_cls: KeyClassification = classify_key_image(image_path, ocr_lang=self.ocr_lang)
            if key_cls.is_key_image:
                run.skipped_images.append(
                    {
                        "source": str(image_path),
                        "reason": key_cls.reason,
                        "confidence": key_cls.confidence,
                    }
                )
                continue

            raw_items, raw_output = self.extractor.extract_from_image(
                image_path=image_path,
                curso=curso,
                tema=tema,
                start_n=seq_n,
            )
            parsed_items, effective_raw, parse_retries_used, parse_errors = self._parse_with_retry(
                image_path=image_path,
                raw_output=raw_output,
                curso=curso,
                tema=tema,
                start_n=seq_n,
                initial_items=raw_items,
            )
            parse_failure_fallback = False
            if not parsed_items:
                parse_failure_fallback = True
                self._record_parse_failure(
                    run=run,
                    image_path=image_path,
                    parse_retries_used=parse_retries_used,
                    parse_errors=parse_errors,
                    raw_output=effective_raw,
                )
                parsed_items = [ScanItem.empty(n=seq_n, curso=curso, tema=tema).to_dict()]

            local_rows: List[PipelineItemResult] = []
            for raw in parsed_items:
                suggested_n = int(raw.get("n", 0) or 0)
                default_n = suggested_n if suggested_n > 0 else seq_n
                normalized, normalize_meta = self._normalize_item(raw, default_n=default_n, curso=curso, tema=tema)
                if suggested_n <= 0:
                    normalized.n = seq_n
                row = self._validate_and_retry_item(
                    item=normalized,
                    normalize_meta=normalize_meta,
                    image_path=image_path,
                    curso=curso,
                    tema=tema,
                )
                row.raw_output = effective_raw
                local_rows.append(row)
                seq_n = max(seq_n + 1, row.item.n + 1)

            run.items.extend(local_rows)
            run.diagnostics.extend(
                {
                    "source": str(image_path),
                    "n": int(row.item.n),
                    "needs_review": bool(row.item.needs_review),
                    "json_errors": list(row.json_errors),
                    "render_errors": list(row.render_errors),
                    "unknown_symbols": list(row.unknown_symbols),
                    "latex_warnings": list(row.latex_warnings),
                    "latex_validation_errors": list(row.latex_validation_errors),
                    "latex_normalized_changed": bool(row.latex_normalized_changed),
                    "retries_used": int(row.retries_used),
                    "parse_retries_used": int(parse_retries_used),
                    "parse_errors": list(parse_errors),
                    "is_parse_failure_fallback": bool(parse_failure_fallback),
                }
                for row in local_rows
            )
            self._save_debug(
                source_name=image_path.stem,
                payload={
                    "source": str(image_path),
                    "raw_output": raw_output,
                    "effective_raw_output": effective_raw,
                    "rows": [
                        {
                            "item": row.item.to_dict(),
                            "json_errors": row.json_errors,
                            "render_errors": row.render_errors,
                            "unknown_symbols": row.unknown_symbols,
                            "latex_warnings": row.latex_warnings,
                            "latex_validation_errors": row.latex_validation_errors,
                            "latex_normalized_changed": row.latex_normalized_changed,
                            "retries_used": row.retries_used,
                        }
                        for row in local_rows
                    ],
                    "parse_retries_used": parse_retries_used,
                    "parse_errors": parse_errors,
                },
            )

        run.needs_review_count = sum(1 for row in run.items if row.item.needs_review)
        run.rendered_document = render_document([row.item for row in run.items])
        return run
