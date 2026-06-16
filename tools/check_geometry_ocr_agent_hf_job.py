from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from huggingface_hub import HfApi, get_token


def _read_env_file_token(path: Path) -> str:
    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in {"HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN"}:
            return value.strip().strip('"').strip("'")
    return ""


def _resolve_token(env_file: str = "") -> str:
    if env_file:
        token = _read_env_file_token(Path(env_file).expanduser().resolve())
        if token:
            return token
    for name in ("HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return (get_token() or "").strip()


def _latest_manifest() -> Path:
    candidates = sorted(Path("submitted_jobs").glob("geometry_ocr_agent_angle_policy_v1_*.json"))
    if not candidates:
        raise FileNotFoundError("No encontre manifiestos geometry_ocr_agent_angle_policy_v1_*.json en submitted_jobs.")
    return candidates[-1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Consulta estado/logs del job HF Geometry OCR Agent.")
    parser.add_argument("--manifest", default="", help="Manifest JSON del job. Por defecto usa el ultimo.")
    parser.add_argument("--env-file", default=".env.local")
    parser.add_argument("--tail", type=int, default=0, help="Muestra las ultimas N lineas de log.")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).expanduser().resolve() if args.manifest else _latest_manifest().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    job_id = str(manifest.get("hf_job_id") or "").strip()
    namespace = str(manifest.get("hf_user") or "Jhoan12").strip()
    if not job_id:
        raise ValueError(f"Manifest sin hf_job_id: {manifest_path}")

    token = _resolve_token(args.env_file)
    if not token:
        raise RuntimeError("No se encontro token Hugging Face.")
    api = HfApi(token=token)
    job = api.inspect_job(job_id=job_id, namespace=namespace, token=token)
    print(f"[INFO] job={job_id}")
    print(f"[INFO] url={getattr(job, 'url', '')}")
    print(f"[INFO] status={getattr(getattr(job, 'status', None), 'stage', None)}")
    message = getattr(getattr(job, "status", None), "message", None)
    if message:
        print(f"[INFO] message={message}")
    terminal_stages = {"COMPLETED", "FAILED", "ERROR", "CANCELED", "CANCELLED", "STOPPED", "SUCCESS"}
    stage = str(getattr(getattr(job, "status", None), "stage", "") or "").upper()
    if args.tail and args.tail > 0 and stage not in terminal_stages:
        print("[INFO] logs=omitidos; el job aun no esta terminado y fetch_job_logs puede bloquear.")
    elif args.tail and args.tail > 0:
        lines = list(api.fetch_job_logs(job_id=job_id, namespace=namespace, token=token))
        print(f"[INFO] log_tail={min(len(lines), args.tail)}")
        for line in lines[-args.tail :]:
            print(line.rstrip())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CLI entrypoint.
        print(f"[ERROR] {exc}")
        raise SystemExit(1)
