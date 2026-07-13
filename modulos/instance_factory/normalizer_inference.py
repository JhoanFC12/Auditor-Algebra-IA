from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

try:  # pragma: no cover - covered through service tests with a fake client.
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment]

from .models import InstancePipelineContext, StagingProblemRecord


NORMALIZER_SYSTEM_PROMPT = (
    "Eres un normalizador fiel de OCR matematico para Auditor-IA. "
    "Recibes un JSON de staging con raw_ocr, metadata y segmentacion grafica. "
    "Devuelve solamente un item LaTeX final, sin JSON, sin explicaciones y sin inventar contenido. "
    "Usa el formato: \\item[\\textbf{n.}] [[curso=...]] [[tema=...]] "
    "[[Estado=sin_revisar]] [[Clave=...]] enunciado [[Imagen=img-n]] "
    "\u00a3A)...\u00e6B)...\u00e6C)...\u00a3D)...\u00e6\u00e6E)...\u00a3. "
    "Respeta exactamente los separadores de alternativas \u00a3 y \u00e6; no los cambies por listas A) B) C) D) E). "
    "Usa [[Imagen=img-n]] solo cuando la segmentacion indique grafico o el humano lo haya marcado. "
    "No describas graficos. Si el JSON trae continuations o imagen OCR fusionada, integra ese contenido en el problema padre. "
    "No uses [CONT.] como contrato: puede no existir, no debes pedirlo y nunca debe aparecer en la salida final. "
    "Si procesas un lote externo, conserva cada separador ----nombre_imagen.png----- antes de su item LaTeX."
)

MOJIBAKE_RE = re.compile(r"(?:Ã.|Â.|â.|Ð.|�)")


_LOCAL_MODEL_CACHE: dict[tuple[str, str, str], tuple[Any, Any, str]] = {}
_LOCAL_MODEL_LOCK = threading.Lock()


def _repair_mojibake_text(text: str) -> str:
    raw = str(text or "")
    if not raw or not MOJIBAKE_RE.search(raw):
        return raw
    current = raw
    for _ in range(2):
        if not MOJIBAKE_RE.search(current):
            break
        try:
            repaired = current.encode("latin-1", errors="strict").decode("utf-8", errors="strict")
        except Exception:
            try:
                repaired = current.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
            except Exception:
                break
        if repaired == current:
            break
        current = repaired
    return current


def _extract_chat_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        rows: list[str] = []
        for chunk in content:
            if isinstance(chunk, dict):
                text = str(chunk.get("text") or "")
                if text:
                    rows.append(text)
            elif isinstance(chunk, str):
                rows.append(chunk)
        return "\n".join(rows)
    return str(content or "")


def _compact_figure(figure: dict[str, Any]) -> dict[str, Any]:
    segments = figure.get("segments") if isinstance(figure.get("segments"), list) else []
    detector = figure.get("detector") if isinstance(figure.get("detector"), dict) else {}
    total = int(figure.get("segments_total") or len(segments) or 0)
    return {
        "status": str(figure.get("status") or ""),
        "has_figure": total > 0,
        "segments_total": total,
        "detector_source": str(detector.get("detector_source") or ""),
        "review_status": str(detector.get("review_status") or ""),
        "segments": [
            {
                "idx": int(item.get("idx") or index + 1),
                "bbox_px": [int(v) for v in list(item.get("bbox_px") or [])[:4]],
                "image_name": Path(str(item.get("image_path") or item.get("image_name") or "")).name,
                "reviewed": bool(item.get("reviewed")),
            }
            for index, item in enumerate(segments)
            if isinstance(item, dict)
        ],
    }


def _strip_continuation_marker(value: str) -> str:
    return re.sub(r"^\s*\[CONT(?:\.\s*|\s+\d+\s*|\s*\.\s*\d+\s*)?\]\s*", "", str(value or ""), flags=re.IGNORECASE).strip()


def _record_ocr_text_for_normalizer(record: StagingProblemRecord) -> str:
    normalized = dict(record.normalized or {})
    candidates = [
        record.raw_ocr,
        normalized.get("enunciado_latex"),
        normalized.get("latex_rendered_item"),
    ]
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def normalizer_input_from_record(
    context: InstancePipelineContext,
    record: StagingProblemRecord,
    *,
    continuations: list[StagingProblemRecord] | None = None,
) -> dict[str, Any]:
    source = dict(record.source or {})
    normalized = dict(record.normalized or {})
    figure = dict(record.figure_segmentation or {})
    crop_path = Path(str(record.crop_path or ""))
    continuation_entries: list[dict[str, Any]] = []
    continuation_images: list[dict[str, str]] = []
    for index, row in enumerate(list(continuations or []), start=1):
        row_source = dict(row.source or {})
        row_crop_path = Path(str(row.crop_path or ""))
        row_raw_ocr = _strip_continuation_marker(_record_ocr_text_for_normalizer(row))
        continuation_entries.append(
            {
                "record_id": str(row.record_id or ""),
                "crop_id": str(row.crop_id or row.record_id or ""),
                "raw_ocr": row_raw_ocr,
                "source": {
                    "page_number": row_source.get("page_number")
                    if row_source.get("page_number") is not None
                    else row_source.get("source_page_number"),
                    "problem_number": row_source.get("problem_number")
                    if row_source.get("problem_number") is not None
                    else row_source.get("n"),
                    "box_index": row_source.get("box_index")
                    if row_source.get("box_index") is not None
                    else row_source.get("page_problem_index"),
                    "crop_name": row_crop_path.name,
                },
                "figure_segmentation": _compact_figure(dict(row.figure_segmentation or {})),
            }
        )
        continuation_images.append(
            {
                "role": f"continuation_{index:02d}",
                "crop_id": str(row.crop_id or row.record_id or ""),
                "file_name": row_crop_path.name,
            }
        )
    raw_parts = [str(record.raw_ocr or "").strip()]
    raw_parts.extend(
        f"Continuacion fusionada {index}: {entry['raw_ocr']}".strip()
        for index, entry in enumerate(continuation_entries, start=1)
        if str(entry.get("raw_ocr") or "").strip()
    )
    has_figure_hint = (
        bool(normalized.get("tiene_grafico"))
        or int(figure.get("segments_total") or 0) > 0
        or any(bool(dict(entry.get("figure_segmentation") or {}).get("has_figure")) for entry in continuation_entries)
    )
    return {
        "schema_version": "normalizer_training_input_v1",
        "record_id": str(record.record_id or ""),
        "raw_ocr": "\n\n".join(part for part in raw_parts if part),
        "main_raw_ocr": str(record.raw_ocr or ""),
        "source": {
            "book_code": str(source.get("book_code") or context.book_code or ""),
            "instance_type": str(source.get("instance_type") or context.instance_type or ""),
            "page_number": source.get("page_number") if source.get("page_number") is not None else source.get("source_page_number"),
            "problem_number": source.get("problem_number") if source.get("problem_number") is not None else source.get("n"),
            "box_index": source.get("box_index") if source.get("box_index") is not None else source.get("page_problem_index"),
            "crop_name": crop_path.name,
        },
        "figure_segmentation": _compact_figure(figure),
        "human_hints": {
            "curso": str(normalized.get("curso") or ""),
            "tema": str(normalized.get("tema") or ""),
            "has_figure": has_figure_hint,
            "figure_tag": str(normalized.get("figure_tag") or ""),
        },
        "continuations": continuation_entries,
        "images": [
            {
                "role": "main",
                "crop_id": str(record.crop_id or record.record_id or ""),
                "file_name": crop_path.name,
            }
        ] + continuation_images,
    }


def sanitize_final_latex(value: str) -> str:
    text = _repair_mojibake_text(str(value or "")).strip()
    text = text.translate(str.maketrans({chr(0x0141): chr(0x00A3), chr(0x0142): chr(0x00A3), chr(0x0106): chr(0x00E6), chr(0x0107): chr(0x00E6)}))
    if text.startswith("```"):
        text = re.sub(r"^```(?:latex|tex|text)?\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*```$", "", text).strip()
    marker = text.find("\\item")
    if marker > 0:
        text = text[marker:].strip()
    return text


_OPTION_LABEL_RE = re.compile(r"(?<![A-Za-z])([A-E])\)")
_FINAL_OPTION_RE = re.compile(r"(?:\u00a3|\u00e6)[A-E]\)")


def _extract_ocr_options(text: str) -> dict[str, str]:
    matches = list(_OPTION_LABEL_RE.finditer(str(text or "")))
    if not matches:
        return {}
    options: dict[str, str] = {}
    for index, match in enumerate(matches):
        label = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = str(text[start:end]).strip()
        if label in "ABCDE" and value:
            options[label] = value
    return options


def _text_before_options(text: str) -> str:
    clean = _strip_continuation_marker(str(text or "")).strip()
    match = _OPTION_LABEL_RE.search(clean)
    if match:
        clean = clean[: match.start()].strip()
    return clean


def _item_number_from_final(final_latex: str) -> str:
    match = re.search(r"\\item\s*\[\s*\\textbf\{\s*([0-9]{1,4})\s*\.\s*\}\s*\]", str(final_latex or ""))
    return match.group(1) if match else ""


def _insert_before_options_or_end(final_latex: str, value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return final_latex
    match = _FINAL_OPTION_RE.search(final_latex)
    if match:
        prefix = final_latex[: match.start()].rstrip()
        suffix = final_latex[match.start() :].lstrip()
        return f"{prefix} {value} {suffix}".strip()
    return f"{final_latex.rstrip()} {value}".strip()


def _option_block(options: dict[str, str]) -> str:
    if any(not str(options.get(label) or "").strip() for label in "ABCDE"):
        return ""
    return (
        f"\u00a3A) {options['A'].strip()}"
        f"\u00e6B) {options['B'].strip()}"
        f"\u00e6C) {options['C'].strip()}"
        f"\u00a3D) {options['D'].strip()}"
        f"\u00e6\u00e6E) {options['E'].strip()}\u00a3"
    )


def repair_final_latex_with_normalizer_input(final_latex: str, input_payload: dict[str, Any]) -> str:
    """Deterministic guardrail for content already present in continuation OCR."""
    text = sanitize_final_latex(final_latex)
    if not text or not isinstance(input_payload, dict):
        return text

    continuation_texts = [
        _strip_continuation_marker(str(item.get("raw_ocr") or ""))
        for item in list(input_payload.get("continuations") or [])
        if isinstance(item, dict) and str(item.get("raw_ocr") or "").strip()
    ]
    raw_ocr = str(input_payload.get("raw_ocr") or "")

    for continuation_text in continuation_texts:
        prefix = _text_before_options(continuation_text)
        if prefix and prefix not in text:
            text = _insert_before_options_or_end(text, prefix)

    options = _extract_ocr_options("\n".join([raw_ocr, *continuation_texts]))
    if options and not _FINAL_OPTION_RE.search(text):
        block = _option_block(options)
        if block:
            text = f"{text.rstrip()} {block}".strip()

    human_hints = input_payload.get("human_hints") if isinstance(input_payload.get("human_hints"), dict) else {}
    has_figure = bool(human_hints.get("has_figure"))
    if not has_figure:
        main_figure = input_payload.get("figure_segmentation") if isinstance(input_payload.get("figure_segmentation"), dict) else {}
        has_figure = bool(main_figure.get("has_figure")) or int(main_figure.get("segments_total") or 0) > 0
    if not has_figure:
        for item in list(input_payload.get("continuations") or []):
            if not isinstance(item, dict):
                continue
            figure = item.get("figure_segmentation") if isinstance(item.get("figure_segmentation"), dict) else {}
            if bool(figure.get("has_figure")) or int(figure.get("segments_total") or 0) > 0:
                has_figure = True
                break
    if has_figure and "[[Imagen=" not in text:
        number = _item_number_from_final(text) or str(
            dict(input_payload.get("source") or {}).get("problem_number") or ""
        ).strip()
        figure_tag = str(human_hints.get("figure_tag") or "").strip() or (f"img-{number}" if number else "img-n")
        text = _insert_before_options_or_end(text, f"[[Imagen={figure_tag}]]")

    return text.strip()


class HfOcrNormalizerClient:
    def __init__(
        self,
        *,
        model: str,
        token: str = "",
        base_url: str = "",
        timeout_s: int | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> None:
        self.model = str(model or "").strip()
        self.token = str(token or os.getenv("HF_TOKEN", "") or os.getenv("HUGGINGFACEHUB_API_TOKEN", "") or "").strip()
        self.base_url = (
            str(base_url or os.getenv("HF_OCR_NORMALIZER_BASE_URL", "") or os.getenv("HF_BASE_URL", "") or "")
            .strip()
            .rstrip("/")
            or "https://router.huggingface.co/v1"
        )
        self.timeout_s = int(timeout_s or int(os.getenv("HF_OCR_NORMALIZER_TIMEOUT", "180") or "180"))
        self.max_tokens = int(max_tokens or int(os.getenv("HF_OCR_NORMALIZER_MAX_TOKENS", "900") or "900"))
        self.temperature = float(
            temperature
            if temperature is not None
            else float(os.getenv("HF_OCR_NORMALIZER_TEMPERATURE", "0.0") or "0.0")
        )
        self.local_adapter_dir = str(os.getenv("HF_OCR_NORMALIZER_LOCAL_DIR", "") or "").strip()
        self.local_base_dir = str(os.getenv("HF_OCR_NORMALIZER_BASE_MODEL_LOCAL_DIR", "") or "").strip()
        self.prefer_local = str(os.getenv("HF_OCR_NORMALIZER_PREFER_LOCAL", "") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "si",
        }
        self.local_device = str(os.getenv("HF_OCR_NORMALIZER_DEVICE", "auto") or "auto").strip().lower()

    def generate_final_latex(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.model or self.model == "normalizer_v0_passthrough":
            raise RuntimeError("No hay modelo normalizador IA configurado en HF_OCR_NORMALIZER_MODEL.")
        if self._should_use_local():
            return self._generate_final_latex_local(input_payload)
        if not self.token:
            raise RuntimeError("Falta HF_TOKEN para usar el normalizador IA.")
        if OpenAI is None:
            raise RuntimeError("La libreria openai no esta disponible para llamar el normalizador IA.")
        client = OpenAI(base_url=self.base_url, api_key=self.token, timeout=self.timeout_s)
        user_payload = json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": NORMALIZER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_payload},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except Exception as exc:
            raise RuntimeError(self._friendly_error(exc)) from exc
        content = response.choices[0].message.content if response and response.choices else ""
        final_latex = sanitize_final_latex(_extract_chat_text(content))
        if not final_latex:
            raise RuntimeError("El normalizador IA devolvio una respuesta vacia.")
        return {
            "schema_version": "hf_ocr_normalizer_prediction_v1",
            "model": self.model,
            "base_url": self.base_url,
            "final_latex": final_latex,
            "input": input_payload,
        }

    def _should_use_local(self) -> bool:
        return bool(self.prefer_local and self.local_adapter_dir and self.local_base_dir)

    def _generate_final_latex_local(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        adapter_dir = Path(self.local_adapter_dir).expanduser().resolve()
        base_dir = Path(self.local_base_dir).expanduser().resolve()
        if not adapter_dir.exists():
            raise RuntimeError(f"No existe HF_OCR_NORMALIZER_LOCAL_DIR: {adapter_dir}")
        if not base_dir.exists():
            raise RuntimeError(f"No existe HF_OCR_NORMALIZER_BASE_MODEL_LOCAL_DIR: {base_dir}")
        if self._should_run_local_in_subprocess():
            return self._generate_final_latex_local_subprocess(input_payload)

        tokenizer, model, device = self._load_local_model(base_dir, adapter_dir)
        user_payload = json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))
        messages = [
            {"role": "system", "content": NORMALIZER_SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ]
        try:
            prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        except Exception:
            prompt = f"{NORMALIZER_SYSTEM_PROMPT}\n\n{user_payload}\n"

        inputs = tokenizer(prompt, return_tensors="pt")
        if device != "cpu":
            inputs = {key: value.to(device) for key, value in inputs.items()}

        try:
            import torch

            with torch.inference_mode():
                generation_kwargs: dict[str, Any] = {
                    "max_new_tokens": self.max_tokens,
                    "do_sample": self.temperature > 0,
                    "pad_token_id": getattr(tokenizer, "eos_token_id", None),
                }
                if self.temperature > 0:
                    generation_kwargs["temperature"] = max(float(self.temperature), 1e-5)
                output_ids = model.generate(**inputs, **generation_kwargs)
        except Exception as exc:
            raise RuntimeError(f"Error ejecutando normalizador local: {exc}") from exc

        input_len = int(inputs["input_ids"].shape[-1])
        generated = output_ids[0][input_len:]
        final_latex = sanitize_final_latex(tokenizer.decode(generated, skip_special_tokens=True))
        if not final_latex:
            raise RuntimeError("El normalizador local devolvio una respuesta vacia.")
        return {
            "schema_version": "local_ocr_normalizer_prediction_v1",
            "model": self.model,
            "base_url": "local",
            "local_adapter_dir": str(adapter_dir),
            "local_base_dir": str(base_dir),
            "device": device,
            "final_latex": final_latex,
            "input": input_payload,
        }

    def _should_run_local_in_subprocess(self) -> bool:
        if str(os.getenv("HF_OCR_NORMALIZER_IN_WORKER", "") or "").strip() == "1":
            return False
        raw = str(os.getenv("HF_OCR_NORMALIZER_LOCAL_SUBPROCESS", "1") or "1").strip().lower()
        return raw not in {"0", "false", "no", "off"}

    def _generate_final_latex_local_subprocess(self, input_payload: dict[str, Any]) -> dict[str, Any]:
        root = Path(__file__).resolve().parents[2]
        python_exe = Path(sys.executable)
        if python_exe.name.lower() == "pythonw.exe":
            candidate = python_exe.with_name("python.exe")
            if candidate.exists():
                python_exe = candidate
        timeout_s = max(30, int(self.timeout_s or 180) + 60)
        with tempfile.TemporaryDirectory(prefix="auditor_normalizer_") as tmp:
            tmp_dir = Path(tmp)
            input_path = tmp_dir / "input.json"
            output_path = tmp_dir / "output.json"
            input_path.write_text(json.dumps(input_payload, ensure_ascii=False), encoding="utf-8")
            env = os.environ.copy()
            env["HF_OCR_NORMALIZER_IN_WORKER"] = "1"
            env.setdefault("PYTHONIOENCODING", "utf-8")
            cmd = [
                str(python_exe),
                "-m",
                "modulos.instance_factory.normalizer_worker",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ]
            try:
                completed = subprocess.run(
                    cmd,
                    cwd=str(root),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"El normalizador local excedio {timeout_s}s en el proceso aislado."
                ) from exc
            if output_path.exists():
                try:
                    payload = json.loads(output_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    raise RuntimeError(f"El normalizador local devolvio JSON invalido: {exc}") from exc
                if payload.get("ok"):
                    result = payload.get("result")
                    if isinstance(result, dict):
                        return result
                    raise RuntimeError("El normalizador local no devolvio un resultado valido.")
                detail = str(payload.get("error") or "error desconocido").strip()
                trace = str(payload.get("traceback") or "").strip()
                if trace:
                    detail = f"{detail}\n{trace}"
                raise RuntimeError(f"Error en normalizador local aislado: {detail}")
            stderr = str(completed.stderr or "").strip()
            stdout = str(completed.stdout or "").strip()
            detail = stderr or stdout or "sin salida"
            raise RuntimeError(
                f"El proceso aislado del normalizador termino sin respuesta "
                f"(exit {completed.returncode}). Detalle: {detail}"
            )

    def _load_local_model(self, base_dir: Path, adapter_dir: Path) -> tuple[Any, Any, str]:
        cache_key = (str(base_dir), str(adapter_dir), self.local_device)
        with _LOCAL_MODEL_LOCK:
            cached = _LOCAL_MODEL_CACHE.get(cache_key)
            if cached is not None:
                return cached
            try:
                import torch
                from peft import PeftModel
                from transformers import AutoModelForCausalLM, AutoTokenizer
            except Exception as exc:
                raise RuntimeError(
                    "Faltan dependencias para usar el normalizador local. "
                    "Instala: python -m pip install -r requirements-local-ocr.txt"
                ) from exc

            device = self._resolve_local_device(torch)
            tokenizer_source = adapter_dir if (adapter_dir / "tokenizer_config.json").exists() else base_dir
            tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_source), local_files_only=True)
            dtype = torch.float32 if device == "cpu" else torch.float16
            base_model = AutoModelForCausalLM.from_pretrained(
                str(base_dir),
                local_files_only=True,
                dtype=dtype,
                low_cpu_mem_usage=True,
            )
            model = PeftModel.from_pretrained(base_model, str(adapter_dir), local_files_only=True)
            if device != "cpu":
                model = model.to(device)
            model.eval()
            payload = (tokenizer, model, device)
            _LOCAL_MODEL_CACHE[cache_key] = payload
            return payload

    def _resolve_local_device(self, torch: Any) -> str:
        requested = self.local_device
        if requested and requested not in {"auto", "cuda", "cpu"}:
            return requested
        if requested == "cpu":
            return "cpu"
        if requested in {"auto", "cuda"} and bool(torch.cuda.is_available()):
            return "cuda"
        return "cpu"

    def _friendly_error(self, exc: Exception) -> str:
        raw = str(exc or "")
        lowered = raw.lower()
        if "403" in lowered or "permission" in lowered or "forbidden" in lowered:
            return (
                "Hugging Face rechazo el normalizador IA por permisos. "
                "Activa 'Make calls to Inference Providers' o usa HF_OCR_NORMALIZER_BASE_URL "
                f"con un endpoint dedicado. Detalle: {raw}"
            )
        if "404" in lowered or "not found" in lowered:
            return (
                "No se encontro el modelo normalizador en Hugging Face o no esta disponible para inferencia. "
                f"Modelo: {self.model}. Detalle: {raw}"
            )
        return raw
