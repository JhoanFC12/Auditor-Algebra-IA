from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


sys.path.insert(0, str(_repo_root()))

from modulos.modulo0_transcriptor.scan_pipeline.pipeline import ScanPipeline  # noqa: E402
from modulos.modulo0_transcriptor.scan_pipeline.tokens import SEP_LINE, SEP_OPT  # noqa: E402
from modulos.modulo0_transcriptor.session_schema import enrich_session_payload_with_structure  # noqa: E402
from modulos.modulo0_transcriptor.gui_transcriptor import TranscriptorWindow  # noqa: E402


ITEM_NUM_RE = re.compile(r"\\item\s*\[\s*\\textbf\s*\{\s*(\d+)\s*\.?\s*\}\s*\]", re.IGNORECASE)
TAG_CURSO_RE = re.compile(r"\[\[\s*curso\s*=\s*([^\]]*?)\s*\]\]", re.IGNORECASE)
TAG_TEMA_RE = re.compile(r"\[\[\s*tema\s*=\s*([^\]]*?)\s*\]\]", re.IGNORECASE)
RENDER_TAGS_RE = re.compile(
    r"^(?P<prefix>\s*\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\]\s*)"
    r"\[\[\s*curso\s*=\s*[^\]]*?\s*\]\]\s*"
    r"\[\[\s*tema\s*=\s*[^\]]*?\s*\]\]\s*",
    re.IGNORECASE,
)
ITEM_PREFIX_RE = re.compile(
    r"^\s*\\item\s*\[\s*\\textbf\{\s*(?P<num>\d+)\.?\s*\}\s*\]\s*",
    re.IGNORECASE,
)
OPTION_BLOCK_RE = re.compile(
    rf"{re.escape(SEP_LINE)}\s*A\)\s*(?P<A>.*?)\s*"
    rf"{re.escape(SEP_OPT)}\s*B\)\s*(?P<B>.*?)\s*"
    rf"{re.escape(SEP_OPT)}\s*C\)\s*(?P<C>.*?)\s*"
    rf"{re.escape(SEP_LINE)}\s*D\)\s*(?P<D>.*?)\s*"
    rf"{re.escape(SEP_OPT)}\s*{re.escape(SEP_OPT)}\s*E\)\s*(?P<E>.*?)\s*"
    rf"{re.escape(SEP_LINE)}\s*$",
    re.IGNORECASE | re.DOTALL,
)
IMAGE_TOKEN_RE = re.compile(r"\[\[\s*Imagen\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)


@dataclass
class PreservedEntry:
    archivo_origen: str
    image_paths: List[str]
    corrected: bool
    curso: str | None = None
    tema: str | None = None


class _DummyVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _DummyController:
    @staticmethod
    def normalizar_item_una_linea(text: str) -> str:
        raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        return " ".join(part.strip() for part in raw.split("\n") if part.strip())

    @staticmethod
    def parsear_numero_original(item_latex: str) -> int | None:
        match = ITEM_NUM_RE.search(str(item_latex or ""))
        if not match:
            return None
        return _safe_int(match.group(1), 0) or None


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return json.loads(path.read_text(encoding="utf-8-sig"))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _natural_key(text: str) -> List[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(text or ""))]


def _parse_item_number(text: str) -> int:
    match = ITEM_NUM_RE.search(str(text or ""))
    if not match:
        return 0
    return _safe_int(match.group(1), 0)


def _extract_tag(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(str(text or ""))
    if not match:
        return None
    return str(match.group(1))


def _build_preserved_map(payload: Dict[str, Any]) -> Dict[int, PreservedEntry]:
    out: Dict[int, PreservedEntry] = {}

    def _consume_item(
        item_text: str,
        *,
        archivo_origen: str,
        image_paths: Iterable[str],
        corrected: bool,
    ) -> None:
        num = _parse_item_number(item_text)
        if num <= 0:
            return
        if num in out:
            return
        out[num] = PreservedEntry(
            archivo_origen=str(archivo_origen or "").strip(),
            image_paths=[str(v or "").strip() for v in image_paths if str(v or "").strip()],
            corrected=bool(corrected),
            curso=_extract_tag(TAG_CURSO_RE, item_text),
            tema=_extract_tag(TAG_TEMA_RE, item_text),
        )

    for raw in payload.get("items", []) or []:
        if not isinstance(raw, dict):
            continue
        _consume_item(
            str(raw.get("item", raw.get("item_text", "")) or ""),
            archivo_origen=str(raw.get("archivo_origen", "") or ""),
            image_paths=list(raw.get("imagenes", raw.get("image_paths", [])) or []),
            corrected=bool(raw.get("corrected", False)),
        )

    state_v3 = payload.get("state_v3", {})
    if isinstance(state_v3, dict):
        for raw in state_v3.get("items", []) or []:
            if not isinstance(raw, dict):
                continue
            _consume_item(
                str(raw.get("item_text", raw.get("item", "")) or ""),
                archivo_origen=str(raw.get("archivo_origen", "") or ""),
                image_paths=list(raw.get("image_paths", raw.get("imagenes", [])) or []),
                corrected=bool(raw.get("corrected", False)),
            )

    return out


def _replace_course_theme_tags(rendered: str, preserved: PreservedEntry | None) -> str:
    if preserved is None:
        return rendered
    if preserved.curso is None and preserved.tema is None:
        return rendered
    match = RENDER_TAGS_RE.match(str(rendered or ""))
    if not match:
        return rendered
    curso = "" if preserved.curso is None else str(preserved.curso)
    tema = "" if preserved.tema is None else str(preserved.tema)
    replacement = f"{match.group('prefix')}[[curso={curso}]] [[tema={tema}]] "
    return replacement + str(rendered or "")[match.end():]


def _restore_visible_quote_style(rendered: str, raw_statement: str) -> str:
    out = str(rendered or "")
    original = str(raw_statement or "")
    if "“" in original and "”" in original:
        out = re.sub(r'"\s*(\$[^$]+\$)\s*"', r'“\1”', out)
    return out


def _ordered_labels(payload: Dict[str, Any]) -> List[str]:
    labels = list((payload.get("ocr_structured_by_label") or {}).keys())
    if labels:
        return sorted({str(v or "").strip() for v in labels if str(v or "").strip()}, key=_natural_key)
    labels = list((payload.get("ocr_raw_first_by_label") or {}).keys())
    return sorted({str(v or "").strip() for v in labels if str(v or "").strip()}, key=_natural_key)


def _build_structured_item_summary(raw_item: Dict[str, Any]) -> str:
    num = _safe_int(raw_item.get("n"), 0)
    figura = "SI" if bool(raw_item.get("has_figure", False)) else "NO"
    enunciado = str(raw_item.get("statement", "") or "").strip() or "..."
    options = raw_item.get("options", {}) if isinstance(raw_item, dict) else {}
    rows = [
        f"ITEM: {num if num > 0 else '?'}",
        f"ENUNCIADO: {enunciado}",
        f"FIGURA: {figura}",
        "OPCIONES:",
    ]
    for label in ("A", "B", "C", "D", "E"):
        value = str((options.get(label, "...") if isinstance(options, dict) else "...") or "...").strip() or "..."
        rows.append(f"{label}) {value}")
    rows.append("ENDITEM")
    return "\n".join(rows)


def _build_formatter(model_name: str) -> TranscriptorWindow:
    obj = object.__new__(TranscriptorWindow)
    obj.controller = _DummyController()
    obj.format_model_var = _DummyVar(model_name)
    obj.hf_token_var = _DummyVar("")
    obj._geometry_pass_by_label = {}
    obj._geometry_pass_payload_by_label = {}
    obj._log_usage = lambda **kwargs: None
    return obj


def _format_structured_item_with_llm(
    formatter: TranscriptorWindow,
    *,
    label: str,
    raw_item: Dict[str, Any],
    timeout_s: int,
    retries: int,
) -> Dict[str, Any] | None:
    raw_summary = _build_structured_item_summary(raw_item)
    formatted = formatter._format_item_hf(
        model="Qwen/Qwen2.5-VL-72B-Instruct",
        timeout_s=int(timeout_s),
        retries=max(0, int(retries)),
        label=label,
        raw_item=raw_summary,
        curso_hint="",
        tema_hint="",
        subtema_hint="",
        run_geometry_pass=False,
        reasoning_payload=None,
    )
    candidate = str(formatted or "").strip()
    if not candidate:
        return None
    if "<" not in raw_summary and ">" not in raw_summary and ("<" in candidate or ">" in candidate):
        return None
    parsed = _parse_scan_line_candidate(
        candidate,
        fallback_n=_safe_int(raw_item.get("n"), 1) or 1,
        fallback_has_figure=bool(raw_item.get("has_figure", False)),
    )
    if parsed is None:
        return None
    fallback_n = _safe_int(raw_item.get("n"), 0)
    return {
        "schema": "ScanItemJSON-v1",
        "n": _safe_int(parsed.get("num"), fallback_n or 1),
        "curso": "",
        "tema": "",
        "has_figure": bool(parsed.get("has_figure", False)) or bool(raw_item.get("has_figure", False)),
        "figure_tag": "",
        "statement": str(parsed.get("statement", raw_item.get("statement", "")) or "").strip() or str(raw_item.get("statement", "") or "").strip() or "...",
        "options": {
            label_key: str((parsed.get("options", {}) or {}).get(label_key, raw_item.get("options", {}).get(label_key, "...")) or "...").strip() or "..."
            for label_key in ("A", "B", "C", "D", "E")
        },
        "needs_review": bool(raw_item.get("needs_review", False)),
    }


def _parse_scan_line_candidate(candidate: str, *, fallback_n: int, fallback_has_figure: bool) -> Dict[str, Any] | None:
    raw = str(candidate or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return None
    prefix = ITEM_PREFIX_RE.match(raw)
    if not prefix:
        return None
    num = _safe_int(prefix.group("num"), fallback_n or 1) or (fallback_n or 1)
    body = raw[prefix.end():].strip()
    option_match = OPTION_BLOCK_RE.search(body)
    if not option_match:
        return None
    statement = body[: option_match.start()].strip()
    has_figure = bool(IMAGE_TOKEN_RE.search(statement)) or bool(fallback_has_figure)
    statement = IMAGE_TOKEN_RE.sub(" ", statement)
    statement = re.sub(r"\[\[\s*curso\s*=\s*[^\]]*?\s*\]\]", " ", statement, flags=re.IGNORECASE)
    statement = re.sub(r"\[\[\s*tema\s*=\s*[^\]]*?\s*\]\]", " ", statement, flags=re.IGNORECASE)
    statement = re.sub(r"\s+", " ", statement).strip()
    return {
        "num": int(num),
        "statement": statement or "...",
        "has_figure": bool(has_figure),
        "options": {
            label: str(option_match.group(label) or "").strip() or "..."
            for label in ("A", "B", "C", "D", "E")
        },
    }


def rebuild_session(
    path: Path,
    *,
    write: bool,
    make_backup: bool,
    hf_format_model: str = "",
    timeout_s: int = 180,
    retries: int = 0,
) -> Dict[str, Any]:
    payload = _load_json(path)
    preserved_by_number = _build_preserved_map(payload)
    structured_by_label = payload.get("ocr_structured_by_label") or {}
    raw_by_label = payload.get("ocr_raw_first_by_label") or {}
    pipeline = ScanPipeline(
        provider="ocr",
        model="",
        max_retries=0,
        parse_max_retries=0,
        strict_json=False,
    )
    formatter = _build_formatter(hf_format_model) if str(hf_format_model or "").strip() else None

    rebuilt_main_items: List[Dict[str, Any]] = []
    rebuilt_state_items: List[Dict[str, Any]] = []
    rebuilt_numbers: List[int] = []
    skipped_labels: List[str] = []
    llm_used = 0
    llm_fallback = 0

    for label in _ordered_labels(payload):
        structured_text = str(structured_by_label.get(label, "") or "").strip()
        if not structured_text:
            skipped_labels.append(label)
            continue
        try:
            structured_payload = json.loads(structured_text)
        except Exception:
            skipped_labels.append(label)
            continue
        raw_items = list(structured_payload.get("items") or [])
        if not raw_items:
            skipped_labels.append(label)
            continue
        for raw_item in [dict(item) for item in raw_items if isinstance(item, dict)]:
            fallback_n = _safe_int(raw_item.get("n"), 1) or 1
            selected_item = dict(raw_item)
            if formatter is not None:
                llm_item = _format_structured_item_with_llm(
                    formatter,
                    label=f"{label}#n{fallback_n}",
                    raw_item=raw_item,
                    timeout_s=timeout_s,
                    retries=retries,
                )
                if llm_item is not None:
                    selected_item = llm_item
                    llm_used += 1
                else:
                    llm_fallback += 1
            run = pipeline.process_raw_output(
                raw_output=str(raw_by_label.get(label, "") or _build_structured_item_summary(raw_item)),
                image_path=Path(label),
                start_n=fallback_n,
                curso="",
                tema="",
                has_figure_hint=bool(selected_item.get("has_figure", False)),
                initial_items=[selected_item],
            )
            if not run.items:
                continue
            row = run.items[0]
            num = int(row.item.n)
            rebuilt_numbers.append(num)
            preserved = preserved_by_number.get(num)
            archivo_origen = (
                str((preserved.archivo_origen if preserved else "") or "").strip()
                or Path(label).stem
            )
            image_paths = list((preserved.image_paths if preserved else []) or [])
            corrected = bool((preserved.corrected if preserved else False))
            rendered = _replace_course_theme_tags(str(row.rendered or "").strip(), preserved)
            rendered = _restore_visible_quote_style(rendered, str(raw_item.get("statement", "") or ""))
            rebuilt_main_items.append(
                {
                    "archivo_origen": archivo_origen,
                    "item": rendered,
                    "imagenes": list(image_paths),
                }
            )
            rebuilt_state_items.append(
                {
                    "archivo_origen": archivo_origen,
                    "item_text": rendered,
                    "image_paths": list(image_paths),
                    "corrected": corrected,
                }
            )

    rebuilt_main_items.sort(key=lambda row: _parse_item_number(str(row.get("item", "") or "")) or 10**9)
    rebuilt_state_items.sort(key=lambda row: _parse_item_number(str(row.get("item_text", "") or "")) or 10**9)

    output_lines = [str(row.get("item", "") or "").strip() for row in rebuilt_main_items if str(row.get("item", "") or "").strip()]
    payload["items"] = rebuilt_main_items
    payload["output_text"] = "\n".join(output_lines).strip()
    payload["saved_at"] = datetime.now().isoformat(timespec="seconds")

    state_v3 = payload.get("state_v3", {})
    if not isinstance(state_v3, dict):
        state_v3 = {}
    state_v3["items"] = rebuilt_state_items
    state_v3["output_text"] = payload["output_text"]
    metadata = state_v3.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["rebuilt_from_structured_at"] = datetime.now().isoformat(timespec="seconds")
    metadata["rebuilt_from_structured_count"] = len(rebuilt_state_items)
    state_v3["metadata"] = metadata
    payload["state_v3"] = state_v3

    corrected_numbers = sorted(
        {
            _parse_item_number(str(row.get("item_text", "") or ""))
            for row in rebuilt_state_items
            if bool(row.get("corrected", False))
        }
    )
    corrected_numbers = [n for n in corrected_numbers if n > 0]
    if "corrected_items" in payload or corrected_numbers:
        payload["corrected_items"] = corrected_numbers

    if isinstance(payload.get("session_bundle"), dict):
        payload["session_bundle"]["saved_at"] = datetime.now().isoformat(timespec="seconds")

    payload = enrich_session_payload_with_structure(payload, session_path=path)

    if write:
        if make_backup:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = path.with_name(f"{path.name}.bak_rebuild_{stamp}")
            shutil.copy2(path, backup_path)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    all_numbers = sorted({n for n in rebuilt_numbers if n > 0})
    missing = []
    if all_numbers:
        missing = [n for n in range(all_numbers[0], all_numbers[-1] + 1) if n not in set(all_numbers)]

    return {
        "session": str(path),
        "items": len(rebuilt_state_items),
        "min_n": all_numbers[0] if all_numbers else None,
        "max_n": all_numbers[-1] if all_numbers else None,
        "missing_numbers": missing,
        "skipped_labels": skipped_labels,
        "llm_format_model": str(hf_format_model or "").strip(),
        "llm_used": int(llm_used),
        "llm_fallback": int(llm_fallback),
        "written": bool(write),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconstruye sesiones del transcriptor desde ocr_structured_by_label.")
    parser.add_argument("sessions", nargs="+", type=Path, help="Rutas de archivos *.session.json")
    parser.add_argument("--write", action="store_true", help="Escribe cambios en disco")
    parser.add_argument("--no-backup", action="store_true", help="No crear backup antes de escribir")
    parser.add_argument("--hf-format-model", default="", help="Modelo HF para reformatear cada item antes del render final")
    parser.add_argument("--timeout-s", type=int, default=180, help="Timeout por item para la pasada de formateo")
    parser.add_argument("--retries", type=int, default=0, help="Reintentos por item para la pasada de formateo")
    args = parser.parse_args()

    exit_code = 0
    for raw_path in args.sessions:
        path = raw_path.expanduser().resolve()
        if not path.exists():
            print(f"ERROR missing: {path}")
            exit_code = 1
            continue
        try:
            summary = rebuild_session(
                path,
                write=bool(args.write),
                make_backup=not bool(args.no_backup),
                hf_format_model=str(args.hf_format_model or "").strip(),
                timeout_s=int(args.timeout_s),
                retries=int(args.retries),
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"ERROR rebuild failed: {path}\n{exc}")
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
