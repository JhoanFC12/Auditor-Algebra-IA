from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List


def _normalize(normalize_text: Callable[[str], str], value: Any) -> str:
    return normalize_text(str(value or ""))


def extract_chat_text(content: Any, *, include_reasoning: bool = True) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    attrs = ("text", "content", "output_text", "reasoning_content") if include_reasoning else ("text", "content", "output_text")
    for attr in attrs:
        try:
            if hasattr(content, attr):
                txt = extract_chat_text(getattr(content, attr), include_reasoning=include_reasoning)
                if txt:
                    return txt
        except Exception:
            pass
    if hasattr(content, "model_dump"):
        try:
            dumped = content.model_dump()
            txt = extract_chat_text(dumped, include_reasoning=include_reasoning)
            if txt:
                return txt
        except Exception:
            pass
    if isinstance(content, dict):
        keys = ("text", "content", "output_text", "reasoning_content") if include_reasoning else ("text", "content", "output_text")
        for key in keys:
            txt = extract_chat_text(content.get(key), include_reasoning=include_reasoning)
            if txt:
                return txt
        return str(content).strip() if include_reasoning else ""
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                block_type = str(block.get("type", "") or "").strip().lower()
                if (not include_reasoning) and block_type in {"reasoning", "reasoning_content", "thinking"}:
                    continue
            txt = extract_chat_text(block, include_reasoning=include_reasoning)
            if txt:
                parts.append(str(txt))
        return "\n".join(parts).strip()
    return str(content).strip()


def extract_formatted_item(text: str, *, normalize_text: Callable[[str], str], extract_first_item: Callable[[str], str]) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"<think>.*?</think>", " ", raw, flags=re.IGNORECASE | re.DOTALL).strip()
    if not raw:
        return ""

    def _item_from_payload(payload: str) -> str:
        try:
            data = json.loads(payload)
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        item = str(data.get("item", "") or "").strip()
        if not item:
            return ""
        return extract_first_item(item) or normalize_text(item)

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.IGNORECASE | re.DOTALL)
    if fenced:
        parsed = _item_from_payload((fenced.group(1) or "").strip())
        if parsed:
            return parsed

    candidates: List[str] = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(raw):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidates.append(raw[start : i + 1])
                    start = -1
    for payload in reversed(candidates):
        parsed = _item_from_payload(payload)
        if parsed:
            return parsed

    compact = normalize_text(raw)
    if compact.startswith("\\item") and ("i need to" not in compact.lower()):
        return extract_first_item(compact)
    return ""


def _extract_json_text_field(text: str, field: str, *, normalize_text: Callable[[str], str]) -> str:
    m = re.search(rf'(?is)"{re.escape(field)}"\s*:\s*"((?:\\.|[^"\\])*)"', str(text or ""))
    if m:
        try:
            value = json.loads(f"\"{m.group(1)}\"")
        except Exception:
            value = m.group(1)
        return normalize_text(str(value or ""))
    return ""


def _extract_json_list_field(data: Dict[str, Any], field: str, *, normalize_text: Callable[[str], str]) -> List[str]:
    if not isinstance(data, dict):
        return []
    value = data.get(field, [])
    out: List[str] = []
    if isinstance(value, list):
        for entry in value:
            txt = normalize_text(str(entry or ""))
            if txt:
                out.append(txt)
    elif isinstance(value, str):
        for part in re.split(r"[;\n]+", value):
            txt = normalize_text(str(part or ""))
            if txt:
                out.append(txt)
    return out


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.IGNORECASE | re.DOTALL)
    attempts = [fenced.group(1).strip()] if fenced else []
    attempts.append(raw)
    m = re.search(r"(\{.*\})", raw, re.DOTALL)
    if m:
        attempts.append((m.group(1) or "").strip())
    for cand in attempts:
        try:
            data = json.loads(cand)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}


def extract_reasoning_payload(text: str, *, normalize_text: Callable[[str], str]) -> Dict[str, Any]:
    data = _extract_json_object(text or "")
    if not data:
        data = extract_reasoning_payload_loose(text or "", normalize_text=normalize_text)
    if not data:
        return {
            "razonamiento_es": "",
            "elementos_geometricos": [],
            "expresiones_sin_dolares": [],
            "alertas": ["salida_no_json_valida"],
        }
    payload = {
        "razonamiento_es": normalize_text(str(data.get("razonamiento_es", "") or "")),
        "elementos_geometricos": _extract_json_list_field(data, "elementos_geometricos", normalize_text=normalize_text),
        "expresiones_sin_dolares": _extract_json_list_field(data, "expresiones_sin_dolares", normalize_text=normalize_text),
        "alertas": _extract_json_list_field(data, "alertas", normalize_text=normalize_text),
    }
    if not payload["alertas"] and (not payload["razonamiento_es"]) and (not payload["elementos_geometricos"]) and (not payload["expresiones_sin_dolares"]):
        payload["alertas"] = ["salida_json_sin_campos"]
    return payload


def extract_reasoning_payload_loose(text: str, *, normalize_text: Callable[[str], str]) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    raw = re.sub(r"<think>.*?</think>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    raw = re.sub(r"```(?:json)?", " ", raw, flags=re.IGNORECASE)
    raw = raw.replace("```", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return {}

    def _json_unescape(value: str) -> str:
        v = str(value or "").strip()
        if not v:
            return ""
        try:
            return str(json.loads(f"\"{v}\""))
        except Exception:
            return v

    key_names = ("razonamiento_es", "elementos_geometricos", "expresiones_sin_dolares", "alertas")

    def _segment_for_key(key: str) -> str:
        m = re.search(rf"(?is)(?:\"{re.escape(key)}\"|{re.escape(key)})\s*:\s*", raw)
        if not m:
            return ""
        start = m.end()
        tail = raw[start:]
        next_hits: List[int] = []
        for other in key_names:
            if other == key:
                continue
            m2 = re.search(rf"(?is)(?:\"{re.escape(other)}\"|{re.escape(other)})\s*:\s*", tail)
            if m2:
                next_hits.append(m2.start())
        if next_hits:
            tail = tail[: min(next_hits)]
        return tail.strip(" ,")

    def _extract_string_field(key: str) -> str:
        seg = _segment_for_key(key)
        if not seg:
            return ""
        m = re.search(r'^"((?:\\.|[^"\\])*)"', seg)
        if m:
            return normalize_text(_json_unescape(m.group(1)))
        m = re.search(r"^'([^']*)'", seg)
        if m:
            return normalize_text(str(m.group(1) or ""))
        plain = re.split(r"[,\}]", seg, maxsplit=1)[0]
        return normalize_text(plain.strip(" \"'"))

    def _extract_list_field(key: str) -> List[str]:
        seg = _segment_for_key(key)
        if not seg:
            return []
        items: List[str] = []
        for quoted in re.findall(r'"((?:\\.|[^"\\])*)"', seg):
            txt = normalize_text(_json_unescape(quoted))
            if txt:
                items.append(txt)
        if not items:
            for quoted in re.findall(r"'([^']*)'", seg):
                txt = normalize_text(str(quoted or ""))
                if txt:
                    items.append(txt)
        if not items:
            buf = seg.split("[", 1)[1] if "[" in seg else seg
            buf = buf.split("]", 1)[0] if "]" in buf else buf
            for part in buf.split(","):
                txt = normalize_text(part.strip(" \"'"))
                if txt:
                    items.append(txt)
        return items

    razonamiento = _extract_string_field("razonamiento_es")
    elementos = _extract_list_field("elementos_geometricos")
    expresiones = _extract_list_field("expresiones_sin_dolares")
    alertas = _extract_list_field("alertas")
    if not razonamiento and not elementos and not expresiones and not alertas:
        return {}
    return {
        "razonamiento_es": razonamiento,
        "elementos_geometricos": elementos,
        "expresiones_sin_dolares": expresiones,
        "alertas": alertas,
    }


def sanitize_reasoning_payload(
    payload: Dict[str, Any],
    *,
    normalize_text: Callable[[str], str],
    norm_key: Callable[[str], str],
    clip_debug_text: Callable[[str, int], str],
) -> Dict[str, Any]:
    src = payload if isinstance(payload, dict) else {}
    src_meta = src.get("__meta", {}) if isinstance(src, dict) else {}
    if not isinstance(src_meta, dict):
        src_meta = {}
    prev_retry_count = int(src_meta.get("retry_json_count", 0) or 0)
    prev_json_invalid_count = int(src_meta.get("json_invalid_count", 0) or 0)
    prev_filtered_figure_count = int(src_meta.get("filtered_figure_alert_count", 0) or 0)
    prev_local_fallback_count = int(src_meta.get("local_fallback_count", 0) or 0)
    prev_raw_model_text = clip_debug_text(str(src_meta.get("raw_model_text", "") or ""), 4000)
    prev_raw_retry_text = clip_debug_text(str(src_meta.get("raw_retry_text", "") or ""), 4000)
    razonamiento = normalize_text(str(src.get("razonamiento_es", "") or ""))
    if len(razonamiento) > 180:
        razonamiento = razonamiento[:180].rstrip()

    def _dedup_limited(values: List[str], limit: int) -> List[str]:
        out: List[str] = []
        seen: set[str] = set()
        for val in values:
            txt = normalize_text(str(val or ""))
            if not txt:
                continue
            key = norm_key(txt)
            if key in seen:
                continue
            seen.add(key)
            out.append(txt)
            if len(out) >= limit:
                break
        return out

    elementos = _dedup_limited(_extract_json_list_field(src, "elementos_geometricos", normalize_text=normalize_text), 5)
    expresiones = _dedup_limited(_extract_json_list_field(src, "expresiones_sin_dolares", normalize_text=normalize_text), 5)
    raw_alertas = _extract_json_list_field(src, "alertas", normalize_text=normalize_text)
    figure_terms = (
        "figura",
        "grafico",
        "gráfico",
        "falta figura",
        "no se proporciona figura",
        "grafico no visible",
        "gráfico no visible",
        "corte de enunciado",
        "corte visual",
    )
    filtered_figure_alert_count = 0
    alertas_tmp: List[str] = []
    for alert in raw_alertas:
        norm = norm_key(alert)
        if any(term in norm for term in figure_terms):
            filtered_figure_alert_count += 1
            continue
        alertas_tmp.append(alert)
    alertas = _dedup_limited(alertas_tmp, 2)

    def _json_invalid(data: Dict[str, Any]) -> bool:
        vals = [norm_key(v) for v in _extract_json_list_field(data, "alertas", normalize_text=normalize_text)]
        return "salida_no_json_valida" in vals

    if not alertas and (not razonamiento) and (not elementos) and (not expresiones):
        alertas = ["salida_json_sin_campos"]
    return {
        "razonamiento_es": razonamiento,
        "elementos_geometricos": elementos,
        "expresiones_sin_dolares": expresiones,
        "alertas": alertas,
        "__meta": {
            "json_invalid_count": max(int(prev_json_invalid_count), 1 if _json_invalid(src) else 0),
            "filtered_figure_alert_count": int(prev_filtered_figure_count + filtered_figure_alert_count),
            "retry_json_count": int(prev_retry_count),
            "local_fallback_count": int(prev_local_fallback_count),
            "raw_model_text": prev_raw_model_text,
            "raw_retry_text": prev_raw_retry_text,
        },
    }
