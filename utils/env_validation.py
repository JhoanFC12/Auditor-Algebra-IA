from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List


def load_env_file_if_present(path: str | Path | None = None) -> Path | None:
    candidates: List[Path] = []
    if path is not None:
        candidates.append(Path(path))
    else:
        cwd = Path.cwd()
        candidates.extend([cwd / ".env.local", cwd / ".env"])
    env_path = next((p for p in candidates if p.exists()), None)
    if env_path is None:
        return None

    try:
        text = env_path.read_text(encoding="utf-8")
    except Exception:
        text = env_path.read_text(encoding="utf-8-sig")
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if (not line) or line.startswith("#") or ("=" not in line):
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value
    return env_path


def validate_required_env(required_vars: Iterable[str], *, context: str) -> None:
    missing = [var for var in required_vars if not str(os.getenv(var, "") or "").strip()]
    if missing:
        names = ", ".join(sorted(set(missing)))
        raise RuntimeError(f"Missing ENV ({context}): {names}")


def validate_scan_provider_env(provider: str) -> None:
    p = (provider or "hf").strip().lower()
    if p == "hf":
        validate_required_env(["HF_TOKEN"], context="scan_provider_hf")
        return
    if p == "openai":
        validate_required_env(["OPENAI_API_KEY"], context="scan_provider_openai")
        return
    if p == "ocr":
        return
    raise RuntimeError(f"Unsupported provider: {provider}")

