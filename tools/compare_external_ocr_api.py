from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

import httpx
from dotenv import load_dotenv
from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGES_DIR = Path(
    r"E:\Banco de Preguntas\1. ALGEBRA\19. Vesalius\vesalius-algebra-temas\temporales\s01_teoria_de_exponentes\sources"
)
DEFAULT_NANONETS_URL = "https://extraction-api.nanonets.com/api/v1/extract/sync"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
DEFAULT_PROMPT = (
    "Transcribe fielmente el contenido matematico de la imagen. "
    "Conserva numeracion, alternativas A-E, fracciones, raices, potencias y expresiones en LaTeX cuando sea posible. "
    "No resuelvas los problemas y no expliques."
)


@dataclass
class ApiResult:
    provider: str
    image: str
    ok: bool
    elapsed_s: float
    text: str = ""
    error: str = ""
    status_code: int | None = None
    raw_response: Any = None


def _load_env() -> None:
    load_dotenv(ROOT / ".env.local", override=False)
    load_dotenv(ROOT / ".env", override=False)


def _natural_key(path: Path) -> List[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def _iter_images(images_dir: Path, limit: int = 0) -> List[Path]:
    if not images_dir.exists():
        raise FileNotFoundError(f"No existe la carpeta de imagenes: {images_dir}")
    images = sorted(
        [path for path in images_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS],
        key=_natural_key,
    )
    if limit > 0:
        return images[:limit]
    return images


def _guess_mime(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    if ext == ".bmp":
        return "image/bmp"
    if ext in {".tif", ".tiff"}:
        return "image/tiff"
    return "image/png"


def _image_data_url(path: Path) -> str:
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{_guess_mime(path)};base64,{payload}"


def _env_any(*names: str) -> str:
    for name in names:
        value = (os.getenv(name, "") or "").strip()
        if value:
            return value
    return ""


def _redact(text: str) -> str:
    msg = str(text or "")
    for name in (
        "NANONETS_API_KEY",
        "DOCSTRANGE_API_KEY",
        "EXTERNAL_OCR_API_KEY",
        "HF_TOKEN",
        "OPENAI_API_KEY",
    ):
        secret = (os.getenv(name, "") or "").strip()
        if secret and secret in msg:
            msg = msg.replace(secret, f"{secret[:4]}...redacted")
    return msg


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def _find_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        texts = [_find_text(item) for item in payload]
        return "\n".join(text for text in texts if text).strip()
    if not isinstance(payload, dict):
        return str(payload).strip()

    direct_keys = ("content", "markdown", "text", "output", "value")
    for key in direct_keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    result = payload.get("result")
    if isinstance(result, dict):
        markdown = result.get("markdown")
        if isinstance(markdown, dict):
            content = markdown.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
        found = _find_text(result)
        if found:
            return found
    elif isinstance(result, str) and result.strip():
        return result.strip()

    for key in ("data", "results", "pages", "documents"):
        found = _find_text(payload.get(key))
        if found:
            return found
    return ""


def _call_nanonets(image_path: Path, *, timeout_s: int) -> ApiResult:
    key = _env_any("NANONETS_API_KEY", "DOCSTRANGE_API_KEY")
    provider = "nanonets-docstrange"
    if not key:
        return ApiResult(provider=provider, image=image_path.name, ok=False, elapsed_s=0.0, error="Falta NANONETS_API_KEY o DOCSTRANGE_API_KEY.")

    url = _env_any("NANONETS_OCR_URL") or DEFAULT_NANONETS_URL
    output_format = _env_any("NANONETS_OUTPUT_FORMAT") or "markdown"
    started = time.perf_counter()
    try:
        with image_path.open("rb") as file_handle:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (image_path.name, file_handle, _guess_mime(image_path))},
                data={"output_format": output_format},
                timeout=timeout_s,
            )
        elapsed = time.perf_counter() - started
        raw_text = response.text
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw_text": raw_text}
        if not response.is_success:
            return ApiResult(
                provider=provider,
                image=image_path.name,
                ok=False,
                elapsed_s=elapsed,
                error=_redact(f"HTTP {response.status_code}: {raw_text[:800]}"),
                status_code=response.status_code,
                raw_response=payload,
            )
        text = _find_text(payload)
        return ApiResult(
            provider=provider,
            image=image_path.name,
            ok=bool(text),
            elapsed_s=elapsed,
            text=text,
            error="" if text else "Respuesta sin texto OCR reconocible.",
            status_code=response.status_code,
            raw_response=payload,
        )
    except Exception as exc:
        return ApiResult(
            provider=provider,
            image=image_path.name,
            ok=False,
            elapsed_s=time.perf_counter() - started,
            error=_redact(str(exc)),
        )


def _call_openai_compatible(image_path: Path, *, timeout_s: int) -> ApiResult:
    provider = _env_any("EXTERNAL_OCR_PROVIDER_NAME") or "openai-compatible-ocr"
    base_url = _env_any("EXTERNAL_OCR_BASE_URL")
    api_key = _env_any("EXTERNAL_OCR_API_KEY")
    model = _env_any("EXTERNAL_OCR_MODEL")
    if not base_url or not api_key or not model:
        return ApiResult(
            provider=provider,
            image=image_path.name,
            ok=False,
            elapsed_s=0.0,
            error="Faltan EXTERNAL_OCR_BASE_URL, EXTERNAL_OCR_API_KEY o EXTERNAL_OCR_MODEL.",
        )

    prompt = _env_any("EXTERNAL_OCR_PROMPT") or DEFAULT_PROMPT
    max_tokens = int(_env_any("EXTERNAL_OCR_MAX_TOKENS") or "3200")
    started = time.perf_counter()
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_s)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _image_data_url(image_path)}},
                    ],
                }
            ],
            temperature=0,
            max_tokens=max_tokens,
        )
        elapsed = time.perf_counter() - started
        content = response.choices[0].message.content if response and response.choices else ""
        text = str(content or "").strip()
        return ApiResult(
            provider=provider,
            image=image_path.name,
            ok=bool(text),
            elapsed_s=elapsed,
            text=text,
            error="" if text else "Respuesta sin texto OCR reconocible.",
            raw_response=_jsonable(response),
        )
    except Exception as exc:
        return ApiResult(
            provider=provider,
            image=image_path.name,
            ok=False,
            elapsed_s=time.perf_counter() - started,
            error=_redact(str(exc)),
        )


def _build_provider_calls(timeout_s: int) -> Dict[str, Callable[[Path], ApiResult]]:
    return {
        "nanonets": lambda path: _call_nanonets(path, timeout_s=timeout_s),
        "openai-compatible": lambda path: _call_openai_compatible(path, timeout_s=timeout_s),
    }


def _requested_providers(raw: str, available: Iterable[str]) -> List[str]:
    available_list = list(available)
    available_set = set(available_list)
    names = [item.strip().lower() for item in str(raw or "").split(",") if item.strip()]
    if not names or names == ["all"]:
        return available_list
    unknown = [name for name in names if name not in available_set]
    if unknown:
        raise ValueError(f"Proveedor externo no soportado: {', '.join(unknown)}")
    return names


def _preview(text: str, limit: int = 180) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit - 3]}..."


def _md_cell(text: str) -> str:
    return str(text or "").replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def _write_outputs(records: List[ApiResult], *, out_dir: Path, include_full_text: bool) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = out_dir / f"ocr_external_api_compare_{stamp}"
    json_path = base.with_suffix(".json")
    csv_path = base.with_suffix(".csv")
    md_path = base.with_suffix(".md")

    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "records": [_jsonable(record.__dict__) for record in records],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("image", "provider", "ok", "elapsed_s", "status_code", "chars", "error", "text"),
        )
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "image": record.image,
                    "provider": record.provider,
                    "ok": "SI" if record.ok else "NO",
                    "elapsed_s": f"{record.elapsed_s:.2f}",
                    "status_code": record.status_code or "",
                    "chars": len(record.text or ""),
                    "error": record.error,
                    "text": record.text,
                }
            )

    lines = [
        "# OCR externo por API",
        "",
        "| Imagen | Proveedor | OK | Segundos | Chars | Error | Vista previa |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for record in records:
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_cell(record.image),
                    _md_cell(record.provider),
                    "SI" if record.ok else "NO",
                    f"{record.elapsed_s:.2f}",
                    str(len(record.text or "")),
                    _md_cell(record.error),
                    _md_cell(_preview(record.text)),
                ]
            )
            + " |"
        )

    if include_full_text:
        lines.extend(["", "## Respuestas completas", ""])
        for record in records:
            lines.extend(
                [
                    f"### {record.image} - {record.provider}",
                    "",
                    f"- OK: {'SI' if record.ok else 'NO'}",
                    f"- Segundos: {record.elapsed_s:.2f}",
                    f"- Error: {record.error or 'ninguno'}",
                    "",
                    "```text",
                    record.text or "",
                    "```",
                    "",
                ]
            )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": json_path, "csv": csv_path, "md": md_path}


def compare_external_ocr(
    *,
    images_dir: Path,
    out_dir: Path,
    provider_names: List[str],
    timeout_s: int,
    limit: int,
    include_full_text: bool,
) -> Dict[str, Any]:
    provider_calls = _build_provider_calls(timeout_s)
    images = _iter_images(images_dir, limit=limit)
    records: List[ApiResult] = []
    for image_path in images:
        for provider_name in provider_names:
            records.append(provider_calls[provider_name](image_path))
    paths = _write_outputs(records, out_dir=out_dir, include_full_text=include_full_text)
    ok_count = sum(1 for record in records if record.ok)
    return {
        "images": len(images),
        "calls": len(records),
        "ok": ok_count,
        "failed": len(records) - ok_count,
        "outputs": {name: str(path) for name, path in paths.items()},
    }


def main(argv: List[str] | None = None) -> int:
    _load_env()
    parser = argparse.ArgumentParser(description="Compara OCR por APIs externas sobre imagenes del escaneo.")
    parser.add_argument("--images-dir", default=str(DEFAULT_IMAGES_DIR), help="Carpeta de imagenes fuente.")
    parser.add_argument("--out-dir", default="", help="Carpeta de salida. Por defecto: diagnostics junto a sources.")
    parser.add_argument(
        "--providers",
        default="nanonets,openai-compatible",
        help="Lista separada por comas: nanonets,openai-compatible o all.",
    )
    parser.add_argument("--timeout", type=int, default=120, help="Timeout por llamada en segundos.")
    parser.add_argument("--limit", type=int, default=0, help="Limita la cantidad de imagenes. 0 = todas.")
    parser.add_argument("--no-full-text", action="store_true", help="No incluir respuestas completas en el Markdown.")
    args = parser.parse_args(argv)

    provider_calls = _build_provider_calls(args.timeout)
    provider_names = _requested_providers(args.providers, provider_calls.keys())
    images_dir = Path(args.images_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else images_dir.parent / "diagnostics"

    summary = compare_external_ocr(
        images_dir=images_dir,
        out_dir=out_dir,
        provider_names=provider_names,
        timeout_s=args.timeout,
        limit=max(0, int(args.limit or 0)),
        include_full_text=not args.no_full_text,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
