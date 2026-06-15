from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from huggingface_hub import HfApi, get_token


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOB_RECORD = REPO_ROOT / "submitted_jobs" / "hf_ocr_normalizer_job_last.json"
ENV_FILE = REPO_ROOT / ".env.local"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modulos.instance_factory.runtime_env import load_factory_runtime_env


def _model_repo_from_record(raw: str) -> str:
    if raw:
        return raw
    if not DEFAULT_JOB_RECORD.exists():
        raise FileNotFoundError(f"No existe registro de job: {DEFAULT_JOB_RECORD}")
    payload = json.loads(DEFAULT_JOB_RECORD.read_text(encoding="utf-8"))
    repo_id = str(payload.get("model_repo_id") or "").strip()
    if not repo_id:
        raise RuntimeError(f"Registro sin model_repo_id: {DEFAULT_JOB_RECORD}")
    return repo_id


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Activa el modelo HF del normalizador OCR en .env.local.")
    parser.add_argument("--model-repo-id", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_factory_runtime_env(REPO_ROOT)
    token = get_token()
    if not token:
        raise RuntimeError("No se encontro HF_TOKEN/HUGGINGFACEHUB_API_TOKEN.")
    repo_id = _model_repo_from_record(args.model_repo_id)
    api = HfApi(token=token)
    info = api.model_info(repo_id=repo_id, token=token)
    payload = {
        "schema_version": "hf_ocr_normalizer_activation_v1",
        "model_repo_id": repo_id,
        "sha": getattr(info, "sha", ""),
        "env_file": str(ENV_FILE),
        "env_key": "HF_OCR_NORMALIZER_MODEL",
        "dry_run": bool(args.dry_run),
    }
    if not args.dry_run:
        _upsert_env_var(ENV_FILE, "HF_OCR_NORMALIZER_MODEL", repo_id)
        payload["updated"] = True
    else:
        payload["updated"] = False
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
