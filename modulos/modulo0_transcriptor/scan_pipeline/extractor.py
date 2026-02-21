from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from openai import OpenAI

from .prompts import (
    SYSTEM_PROMPT_EXTRACT,
    build_correction_prompt,
    build_extract_prompt,
    build_parse_retry_prompt,
)
from .schema import OPTION_LABELS
from .tokens import SEP_LINE, SEP_OPT


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

ITEM_BLOCK_RE = re.compile(
    r"(\\item\s*\[\s*\\textbf\{\s*\d+\.?\s*\}\s*\].*?)(?=\s*\\item\s*\[\s*\\textbf|\Z)",
    re.IGNORECASE | re.DOTALL,
)
ITEM_NUM_RE = re.compile(r"\\item\s*\[\s*\\textbf\{\s*(\d+)\.?\s*\}\s*\]", re.IGNORECASE)
IMAGE_TAG_RE = re.compile(r"\[\[\s*Imagen\s*=\s*(img-\d+)\s*\]\]", re.IGNORECASE)

STRUCTURED_RE = re.compile(
    r"(?is)\bITEM\s*:\s*(?P<num>\d+)\s*"
    r"ENUNCIADO\s*:\s*(?P<enu>.*?)\s*"
    r"(?:FIGURA\s*:\s*(?P<fig>SI|NO)\s*)?"
    r"OPCIONES\s*:\s*"
    r"A\)\s*(?P<A>.*?)\s*"
    r"B\)\s*(?P<B>.*?)\s*"
    r"C\)\s*(?P<C>.*?)\s*"
    r"D\)\s*(?P<D>.*?)\s*"
    r"E\)\s*(?P<E>.*?)\s*"
    r"ENDITEM"
)


def _safe_text(value: Any) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _extract_chat_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for chunk in content:
            if isinstance(chunk, dict):
                txt = chunk.get("text", "")
                if txt:
                    out.append(str(txt))
            elif isinstance(chunk, str):
                out.append(chunk)
        return "\n".join(out)
    return str(content or "")


def _decode_scan_escapes(text: str) -> str:
    out = _safe_text(text)
    out = out.replace(r"\u00a3", SEP_LINE)
    out = out.replace(r"\u00e6", SEP_OPT)
    out = out.replace("Â£", SEP_LINE)
    out = out.replace("Ã¦", SEP_OPT)
    out = out.replace("Ã‚Â£", SEP_LINE)
    out = out.replace("ÃƒÂ¦", SEP_OPT)
    return out


def _strip_fence(text: str) -> str:
    txt = _safe_text(text)
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json|JSON)?", "", txt).strip()
        txt = re.sub(r"```$", "", txt).strip()
    return txt


def _repair_common_json_issues(raw: str) -> str:
    txt = str(raw or "")
    # Qwen/LLM outputs occasionally include JS-style apostrophe escapes inside JSON strings.
    txt = re.sub(r"(?<!\\)\\'", r"\\\\'", txt)
    # Convert single backslashes in LaTeX commands (\theta, \frac, \parallel)
    # into valid JSON escaped backslashes.
    txt = re.sub(r"(?<!\\)\\(?![\"\\/bfnrtu])", r"\\\\", txt)
    # Tolerate trailing commas from model output.
    txt = re.sub(r",(\s*[}\]])", r"\1", txt)
    return txt


def _loads_json_candidates(raw: str) -> Any:
    candidates: List[str] = [str(raw or "")]
    match = re.search(r"(\{.*\}|\[.*\])", str(raw or ""), re.DOTALL)
    if match:
        candidates.append(match.group(1))

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except Exception:
            pass
        repaired = _repair_common_json_issues(candidate)
        if repaired != candidate:
            try:
                return json.loads(repaired)
            except Exception:
                pass
    return None


def _first_json_payload(text: str) -> Any:
    raw = _strip_fence(text)
    if not raw:
        return None
    return _loads_json_candidates(raw)


def _split_options(body: str) -> tuple[str, Dict[str, str]]:
    raw = _decode_scan_escapes(body or "")
    start = raw.find(f"{SEP_LINE}A)")
    if start < 0:
        plain = re.search(r"(?<![A-Za-z0-9])A\)\s*", raw)
        if plain is not None:
            start = plain.start()

    enu_src = raw if start < 0 else raw[:start]
    opt_src = "" if start < 0 else raw[start:]
    if not opt_src:
        return (re.sub(r"\s+", " ", enu_src).strip(), {label: "..." for label in OPTION_LABELS})

    label_re = re.compile(r"(?<![A-Za-z0-9])([A-Ea-e])\)\s*")
    matches = list(label_re.finditer(opt_src))
    options: Dict[str, str] = {label: "..." for label in OPTION_LABELS}
    for idx, m in enumerate(matches):
        label = (m.group(1) or "").upper()
        seg_start = m.end()
        seg_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(opt_src)
        chunk = _decode_scan_escapes(opt_src[seg_start:seg_end])
        chunk = chunk.replace(SEP_LINE, " ").replace(SEP_OPT, " ")
        chunk = re.sub(r"\s+", " ", chunk).strip()
        if chunk:
            options[label] = chunk
    enu = re.sub(r"\s+", " ", enu_src).strip()
    return (enu, options)


def parse_items_from_text(
    text: str,
    *,
    curso: str,
    tema: str,
    start_n: int,
) -> List[Dict[str, Any]]:
    raw = _decode_scan_escapes(text)
    out: List[Dict[str, Any]] = []

    for m in STRUCTURED_RE.finditer(raw):
        n = _safe_int(m.group("num"), default=start_n + len(out))
        has_figure = str(m.group("fig") or "").strip().upper() == "SI"
        options = {label: _safe_text(m.group(label)) or "..." for label in OPTION_LABELS}
        out.append(
            {
                "schema": "ScanItemJSON-v1",
                "n": max(1, n),
                "curso": _safe_text(curso),
                "tema": _safe_text(tema),
                "has_figure": bool(has_figure),
                "figure_tag": f"img-{max(1, n)}" if has_figure else "",
                "statement": _safe_text(m.group("enu")) or "[[ocr_sin_texto]]",
                "options": options,
                "needs_review": False,
            }
        )
    if out:
        return out

    blocks = [b.strip() for b in ITEM_BLOCK_RE.findall(raw)]
    if raw.startswith("\\item") and not blocks:
        blocks = [raw]

    for idx, block in enumerate(blocks):
        num_match = ITEM_NUM_RE.search(block)
        n = _safe_int(num_match.group(1) if num_match else (start_n + idx), default=start_n + idx)
        body = ITEM_NUM_RE.sub("", block, count=1).strip()
        has_figure = bool(IMAGE_TAG_RE.search(body))
        body_clean = IMAGE_TAG_RE.sub(" ", body)
        body_clean = re.sub(r"\[\[\s*(?:curso|tema)\s*=[^\]]+\]\]", " ", body_clean, flags=re.IGNORECASE)
        body_clean = re.sub(r"\s+", " ", body_clean).strip()
        statement, options = _split_options(body_clean)
        out.append(
            {
                "schema": "ScanItemJSON-v1",
                "n": max(1, n),
                "curso": _safe_text(curso),
                "tema": _safe_text(tema),
                "has_figure": bool(has_figure),
                "figure_tag": f"img-{max(1, n)}" if has_figure else "",
                "statement": statement or "[[ocr_sin_texto]]",
                "options": options,
                "needs_review": False,
            }
        )

    return out


class ScanExtractor:
    def __init__(
        self,
        *,
        provider: str = "hf",
        model: str = "",
        timeout_s: int = 180,
        temperature: float = 0.0,
        top_p: float = 1.0,
        max_tokens: int = 3200,
        seed: int | None = 42,
        strict_json: bool = True,
    ) -> None:
        self.provider = (provider or "hf").strip().lower()
        self.model = (model or "").strip()
        self.timeout_s = int(timeout_s)
        self.temperature = float(temperature)
        self.top_p = float(top_p)
        self.max_tokens = int(max_tokens)
        self.seed = int(seed) if seed is not None else None
        self.strict_json = bool(strict_json) if self.provider != "ocr" else False

    def _encode_image(self, image_path: Path) -> str:
        mime = "image/png"
        if image_path.suffix.lower() in {".jpg", ".jpeg"}:
            mime = "image/jpeg"
        elif image_path.suffix.lower() == ".webp":
            mime = "image/webp"
        elif image_path.suffix.lower() == ".bmp":
            mime = "image/bmp"
        raw = image_path.read_bytes()
        return f"data:{mime};base64,{base64.b64encode(raw).decode('utf-8')}"

    def _get_hf_client(self) -> OpenAI:
        token = (os.getenv("HF_TOKEN", "") or os.getenv("HUGGINGFACEHUB_API_TOKEN", "")).strip()
        if not token:
            raise RuntimeError("Missing ENV: HF_TOKEN")
        base_url = (os.getenv("HF_BASE_URL", "https://router.huggingface.co/v1") or "").strip()
        return OpenAI(base_url=base_url, api_key=token, timeout=self.timeout_s)

    def _get_openai_client(self) -> OpenAI:
        api_key = (os.getenv("OPENAI_API_KEY", "") or "").strip()
        if not api_key:
            raise RuntimeError("Missing ENV: OPENAI_API_KEY")
        return OpenAI(api_key=api_key, timeout=self.timeout_s)

    def _resolve_model(self) -> str:
        if self.model:
            return self.model
        if self.provider == "openai":
            return (os.getenv("OPENAI_SCAN_MODEL", "gpt-4o-mini") or "gpt-4o-mini").strip()
        return (os.getenv("HF_MODEL", "Qwen/Qwen2.5-VL-72B-Instruct") or "Qwen/Qwen2.5-VL-72B-Instruct").strip()

    def _call_vision_chat(self, client: OpenAI, payload: Dict[str, Any]) -> str:
        resp = client.chat.completions.create(**payload)
        content = resp.choices[0].message.content if resp and resp.choices else ""
        return _extract_chat_text(content)

    def _vision_chat(self, *, prompt: str, image_path: Path) -> str:
        model = self._resolve_model()
        client = self._get_openai_client() if self.provider == "openai" else self._get_hf_client()
        img_url = self._encode_image(image_path)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT_EXTRACT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": img_url}},
                    ],
                },
            ],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        if self.strict_json and self.provider in {"hf", "openai"}:
            payload["response_format"] = {"type": "json_object"}

        try:
            return self._call_vision_chat(client, payload)
        except Exception as exc:
            err = str(exc).lower()
            fallback = dict(payload)
            changed = False
            if "response_format" in fallback and (
                "response_format" in err
                or "json_object" in err
                or "json schema" in err
                or "unsupported" in err
            ):
                fallback.pop("response_format", None)
                changed = True
            if "seed" in fallback and "seed" in err:
                fallback.pop("seed", None)
                changed = True
            if changed:
                return self._call_vision_chat(client, fallback)
            raise

    def _ocr_local(self, image_path: Path, *, lang: str = "spa+eng") -> str:
        try:
            import pytesseract  # type: ignore
            from PIL import Image, ImageOps  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"OCR provider requires pytesseract + tesseract. {exc}")
        with Image.open(image_path) as im:
            gray = ImageOps.grayscale(im)
            return _safe_text(pytesseract.image_to_string(gray, lang=lang))

    def parse_raw_output(
        self,
        *,
        raw_output: str,
        curso: str,
        tema: str,
        start_n: int,
        allow_text_fallback: bool | None = None,
    ) -> List[Dict[str, Any]]:
        if allow_text_fallback is None:
            allow_text_fallback = (self.provider == "ocr") or (not self.strict_json)

        payload = _first_json_payload(raw_output)
        if isinstance(payload, dict):
            items = payload.get("items")
            if isinstance(items, list):
                return [dict(it) for it in items if isinstance(it, dict)]
            if payload.get("schema") == "ScanItemJSON-v1":
                return [dict(payload)]
        if isinstance(payload, list):
            out = [dict(it) for it in payload if isinstance(it, dict)]
            if out:
                return out

        if allow_text_fallback:
            return parse_items_from_text(raw_output, curso=curso, tema=tema, start_n=start_n)
        return []

    def extract_from_image(
        self,
        *,
        image_path: Path,
        curso: str,
        tema: str,
        start_n: int,
    ) -> tuple[List[Dict[str, Any]], str]:
        if self.provider == "ocr":
            raw = self._ocr_local(image_path)
            return (
                parse_items_from_text(raw, curso=curso, tema=tema, start_n=start_n),
                raw,
            )
        prompt = build_extract_prompt(curso=curso, tema=tema, start_n=start_n)
        raw = self._vision_chat(prompt=prompt, image_path=image_path)
        items = self.parse_raw_output(
            raw_output=raw,
            curso=curso,
            tema=tema,
            start_n=start_n,
            allow_text_fallback=False,
        )
        return (items, raw)

    def repair_raw_output(
        self,
        *,
        image_path: Path,
        raw_output: str,
        errors: Iterable[str],
        curso: str,
        tema: str,
        start_n: int,
    ) -> tuple[List[Dict[str, Any]], str]:
        if self.provider == "ocr":
            parsed = self.parse_raw_output(
                raw_output=raw_output,
                curso=curso,
                tema=tema,
                start_n=start_n,
                allow_text_fallback=True,
            )
            return (parsed, raw_output)

        prompt = build_parse_retry_prompt(
            raw_output=raw_output,
            errors=errors,
            curso=curso,
            tema=tema,
            start_n=start_n,
        )
        repaired_raw = self._vision_chat(prompt=prompt, image_path=image_path)
        repaired_items = self.parse_raw_output(
            raw_output=repaired_raw,
            curso=curso,
            tema=tema,
            start_n=start_n,
            allow_text_fallback=False,
        )
        return (repaired_items, repaired_raw)

    def correct_item(
        self,
        *,
        image_path: Path,
        item: Dict[str, Any],
        errors: Iterable[str],
        curso: str,
        tema: str,
    ) -> Dict[str, Any]:
        if self.provider == "ocr":
            return dict(item)
        prompt = build_correction_prompt(bad_item=item, errors=errors, curso=curso, tema=tema)
        raw = self._vision_chat(prompt=prompt, image_path=image_path)
        payload = _first_json_payload(raw)
        if isinstance(payload, dict):
            return dict(payload)
        return dict(item)
