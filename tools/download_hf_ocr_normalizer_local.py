from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from huggingface_hub import snapshot_download


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_REPO = "Jhoan12/math-ocr-normalizer-qwen2.5-0.5b-lora-v1"
DEFAULT_BASE_REPO = "Qwen/Qwen2.5-0.5B-Instruct"
DEFAULT_TARGET_ROOT = REPO_ROOT / "models" / "ocr_normalizer_qwen2_5_0_5b_lora_v1"
ENV_FILE = REPO_ROOT / ".env.local"


def _upsert_env_var(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{key}="
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith(prefix):
            if not replaced:
                out.append(f"{key}={value}")
                replaced = True
            continue
        out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += int(item.stat().st_size)
        except OSError:
            continue
    return total


def _download_repo(repo_id: str, target: Path, *, token: str | None = None) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    return Path(
        snapshot_download(
            repo_id=repo_id,
            local_dir=str(target),
            token=token,
            local_dir_use_symlinks=False,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Descarga localmente el normalizador OCR y su modelo base.")
    parser.add_argument("--model-repo", default=DEFAULT_MODEL_REPO)
    parser.add_argument("--base-repo", default=DEFAULT_BASE_REPO)
    parser.add_argument("--target-root", default=str(DEFAULT_TARGET_ROOT))
    parser.add_argument("--skip-env", action="store_true", help="No modifica .env.local.")
    parser.add_argument("--token", default="", help="Token HF opcional; si se omite usa el cache/env de HF.")
    args = parser.parse_args()

    target_root = Path(args.target_root).expanduser().resolve()
    base_dir = target_root / "base"
    adapter_dir = target_root / "adapter"
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from modulos.instance_factory.runtime_env import load_factory_runtime_env

        load_factory_runtime_env(REPO_ROOT)
    except Exception:
        pass
    token = (
        str(
            args.token
            or os.getenv("HF_TOKEN", "")
            or os.getenv("HUGGINGFACEHUB_API_TOKEN", "")
            or ""
        ).strip()
        or None
    )

    base_path = _download_repo(args.base_repo, base_dir, token=token)
    adapter_path = _download_repo(args.model_repo, adapter_dir, token=token)

    manifest = {
        "schema_version": "local_ocr_normalizer_manifest_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model_repo": args.model_repo,
        "base_repo": args.base_repo,
        "base_dir": str(base_path),
        "adapter_dir": str(adapter_path),
        "base_size_bytes": _dir_size(base_dir),
        "adapter_size_bytes": _dir_size(adapter_dir),
        "usage": {
            "HF_OCR_NORMALIZER_PREFER_LOCAL": "1",
            "HF_OCR_NORMALIZER_BASE_MODEL_LOCAL_DIR": str(base_dir),
            "HF_OCR_NORMALIZER_LOCAL_DIR": str(adapter_dir),
        },
    }
    manifest_path = target_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.skip_env:
        _upsert_env_var(ENV_FILE, "HF_OCR_NORMALIZER_PREFER_LOCAL", "1")
        _upsert_env_var(ENV_FILE, "HF_OCR_NORMALIZER_BASE_MODEL_LOCAL_DIR", str(base_dir))
        _upsert_env_var(ENV_FILE, "HF_OCR_NORMALIZER_LOCAL_DIR", str(adapter_dir))

    print(json.dumps({**manifest, "manifest_path": str(manifest_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
