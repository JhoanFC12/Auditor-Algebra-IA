from __future__ import annotations

import os
from pathlib import Path


FACTORY_ENV_KEYS = {
    "SCAN_PROVIDER",
    "HF_TOKEN",
    "HUGGINGFACEHUB_API_TOKEN",
    "HF_BASE_URL",
    "HF_MODEL",
    "HF_OCR_ENSEMBLE",
    "HF_OCR_ENSEMBLE_MODELS",
    "HF_TRAINED_OCR_BASE_URL",
    "HF_TRAINED_OCR_ENDPOINT_NAME",
    "HF_TRAINED_OCR_MAX_TOKENS",
    "HF_TRAINED_OCR_IMAGE_MAX_SIDE",
    "HF_TRAINED_OCR_CONTEXT_FALLBACK_IMAGE_MAX_SIDE",
    "HF_TRAINED_OCR_CLIENT_CONCURRENCY",
    "HF_TRAINED_OCR_QUEUE_WAIT_TIMEOUT_SECONDS",
    "HF_TRAINED_OCR_QUEUE_POLL_SECONDS",
    "HF_TRAINED_OCR_REQUEST_LEASE_TTL_SECONDS",
    "HF_ENDPOINT_START_TIMEOUT",
    "HF_ENDPOINT_POLL_SECONDS",
    "HF_ENDPOINT_COLD_START_RETRIES",
    "HF_OCR_NORMALIZER_MODEL",
    "OPENAI_API_KEY",
    "OPENAI_SCAN_MODEL",
    "PDF_PROBLEM_MODEL",
    "PDF_PROBLEM_MODEL_REPO",
    "YOLO_FIGURE_SEGMENT_MODEL",
    "YOLO_FIGURE_MODEL",
    "FIGURE_DETECTOR_MODEL",
    "YOLO_SEGMENT_MODEL",
    "YOLO_DETECT_MODEL",
    "YOLO_FIGURE_SEGMENT_MODEL_REPO",
}

SECRET_KEYS = {
    "HF_TOKEN",
    "HUGGINGFACEHUB_API_TOKEN",
    "OPENAI_API_KEY",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception:
        text = path.read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = raw_value.strip().strip('"').strip("'")
    return values


def _looks_like_placeholder(value: str) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return True
    return any(marker in text for marker in ("replace_me", "xxx_replace", "tu_token_aqui"))


def load_factory_runtime_env(root: str | Path | None = None) -> dict[str, str]:
    """Load Fabrica runtime env with `.env.local` taking priority for model keys.

    The main app uses python-dotenv with override=False, which is correct for
    general settings but can leave a stale HF token in long-running GUI sessions.
    For the local Fabrica runtime, `.env.local` is the user's active model config.
    """
    base = Path(root).expanduser().resolve() if root is not None else _repo_root()
    merged: dict[str, str] = {}
    sources: dict[str, str] = {}
    for env_path in (base / ".env", base / ".env.local"):
        values = _read_env_file(env_path)
        for key, value in values.items():
            if key not in FACTORY_ENV_KEYS:
                continue
            if key in SECRET_KEYS and _looks_like_placeholder(value):
                continue
            if not str(value or "").strip():
                continue
            merged[key] = value
            sources[key] = str(env_path)

    for key, value in merged.items():
        current = str(os.getenv(key, "") or "")
        if key in SECRET_KEYS:
            if _looks_like_placeholder(current) or current != value:
                os.environ[key] = value
            continue
        os.environ[key] = value

    token = str(os.getenv("HF_TOKEN", "") or "").strip()
    alias = str(os.getenv("HUGGINGFACEHUB_API_TOKEN", "") or "").strip()
    if token and ("HF_TOKEN" in merged) and ("HUGGINGFACEHUB_API_TOKEN" not in merged):
        os.environ["HUGGINGFACEHUB_API_TOKEN"] = token
        sources.setdefault("HUGGINGFACEHUB_API_TOKEN", sources.get("HF_TOKEN", "runtime_alias"))
    elif token and (_looks_like_placeholder(alias) or not alias):
        os.environ["HUGGINGFACEHUB_API_TOKEN"] = token
        sources.setdefault("HUGGINGFACEHUB_API_TOKEN", sources.get("HF_TOKEN", "runtime_alias"))

    return sources
