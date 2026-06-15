from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi, get_token


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JOB_RECORD = REPO_ROOT / "submitted_jobs" / "hf_ocr_normalizer_job_last.json"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modulos.instance_factory.runtime_env import load_factory_runtime_env


def _load_job_id(raw: str) -> str:
    if raw:
        return raw
    if not DEFAULT_JOB_RECORD.exists():
        raise FileNotFoundError(f"No existe registro de job: {DEFAULT_JOB_RECORD}")
    payload = json.loads(DEFAULT_JOB_RECORD.read_text(encoding="utf-8"))
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise RuntimeError(f"Registro sin job_id: {DEFAULT_JOB_RECORD}")
    return job_id


def _job_status_payload(api: HfApi, job_id: str) -> dict[str, Any]:
    job = api.inspect_job(job_id=job_id)
    status = getattr(job, "status", None)
    return {
        "job_id": getattr(job, "id", job_id),
        "status": getattr(status, "stage", status),
        "message": getattr(status, "message", ""),
        "flavor": getattr(job, "flavor", ""),
        "created_at": str(getattr(job, "created_at", "")),
        "updated_at": str(getattr(job, "updated_at", "")),
        "docker_image": getattr(job, "docker_image", ""),
    }


def _tail_logs(api: HfApi, job_id: str, limit: int) -> list[str]:
    rows: list[str] = []
    for line in itertools.islice(api.fetch_job_logs(job_id=job_id, follow=False), max(0, int(limit))):
        rows.append(str(line).rstrip())
    return rows[-limit:] if limit > 0 else []


def main() -> int:
    parser = argparse.ArgumentParser(description="Consulta el ultimo job HF del normalizador OCR.")
    parser.add_argument("--job-id", default="")
    parser.add_argument("--logs", type=int, default=0, help="Numero de lineas de log a mostrar.")
    args = parser.parse_args()

    load_factory_runtime_env(REPO_ROOT)
    token = get_token()
    if not token:
        raise RuntimeError("No se encontro HF_TOKEN/HUGGINGFACEHUB_API_TOKEN.")
    job_id = _load_job_id(args.job_id)
    api = HfApi(token=token)
    payload = _job_status_payload(api, job_id)
    if args.logs:
        payload["logs_tail"] = _tail_logs(api, job_id, args.logs)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
