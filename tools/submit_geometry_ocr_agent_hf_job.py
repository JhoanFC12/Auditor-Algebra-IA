from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from huggingface_hub import HfApi, get_token


DEFAULT_DATASET_REPO_ID = "Jhoan12/math-ocr-geometry-agent-angle-policy-v1-dataset"
DEFAULT_MODEL_REPO_ID = "Jhoan12/math-ocr-qwen2.5-vl-3b-geometry-agent-angle-policy-lora-v1"
DEFAULT_TRAIN_SCRIPT = Path("E:/Github/Auditor-IA/submitted_jobs/train_geometry_ocr_agent_hf.py")


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


def _validate_token(token: str) -> dict[str, Any]:
    if not token:
        raise RuntimeError("No se encontro HF_TOKEN ni HUGGINGFACEHUB_API_TOKEN.")
    try:
        who = HfApi(token=token).whoami()
    except Exception as exc:  # noqa: BLE001 - CLI should print the auth failure exactly.
        raise RuntimeError(
            "Token Hugging Face invalido o sin permisos. Actualiza HF_TOKEN/HUGGINGFACEHUB_API_TOKEN "
            "y verifica con: python -c \"from huggingface_hub import HfApi; print(HfApi().whoami()['name'])\". "
            f"Detalle: {exc}"
        ) from exc
    return who


def _read_manifest(dataset_dir: Path) -> dict[str, Any]:
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Dataset invalido: falta manifest.json en {dataset_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for split_name in ("train", "validation", "test"):
        split_file = dataset_dir / f"{split_name}.jsonl"
        if not split_file.exists():
            raise FileNotFoundError(f"Dataset invalido: falta {split_file}")
    return manifest


def _build_remote_command(config: dict[str, Any], script_b64: str) -> list[str]:
    return [
        "bash",
        "-lc",
        "python -m pip install -U 'huggingface_hub>=0.33.0' 'transformers>=4.49,<5' "
        "'accelerate>=1.2' 'peft>=0.14' pillow sentencepiece && "
        "python - <<'PY'\n"
        "import base64, os\n"
        "open('train_geometry_ocr_agent_hf.py', 'wb').write(base64.b64decode(os.environ['TRAIN_SCRIPT_B64']))\n"
        "PY\n"
        "python train_geometry_ocr_agent_hf.py "
        f"--dataset-repo-id {config['dataset_repo_id']} "
        f"--model-repo-id {config['model_repo_id']} "
        f"--base-model {config['base_model']} "
        f"--epochs {config['epochs']} "
        f"--learning-rate {config['learning_rate']} "
        f"--batch {config['batch']} "
        f"--grad-accum {config['grad_accum']} "
        f"--max-train-samples {config['max_train_samples']} "
        f"--max-eval-samples {config['max_eval_samples']} "
        f"--min-side-tokens {config['min_side_tokens']} "
        f"--max-side-tokens {config['max_side_tokens']} "
        f"--lora-rank {config['lora_rank']} "
        f"--lora-alpha {config['lora_alpha']} "
        f"--lora-dropout {config['lora_dropout']} "
        f"--oversample-error-type {config['oversample_error_type']} "
        f"--oversample-factor {config['oversample_factor']} "
        f"--logging-steps {config['logging_steps']}",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sube el dataset geometry_ocr_v1 y lanza fine-tuning LoRA en Hugging Face Jobs."
    )
    parser.add_argument(
        "--dataset-dir",
        default=".cache/transcriptor_runs/datasets/local_ocr_geometry_agent_v1",
        help="Dataset local generado por prepare_local_ocr_lab_dataset.py.",
    )
    parser.add_argument("--dataset-repo-id", default=DEFAULT_DATASET_REPO_ID)
    parser.add_argument("--model-repo-id", default=DEFAULT_MODEL_REPO_ID)
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-VL-3B-Instruct")
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--max-train-samples", type=int, default=0)
    parser.add_argument("--max-eval-samples", type=int, default=0)
    parser.add_argument("--min-side-tokens", type=int, default=256)
    parser.add_argument("--max-side-tokens", type=int, default=768)
    parser.add_argument("--lora-rank", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--oversample-error-type", default="angle_symbol_confusion")
    parser.add_argument("--oversample-factor", type=int, default=4)
    parser.add_argument("--logging-steps", type=int, default=2)
    parser.add_argument("--image", default="pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime")
    parser.add_argument("--flavor", default="a10g-small")
    parser.add_argument("--timeout-seconds", type=int, default=21600)
    parser.add_argument("--private-dataset", action="store_true")
    parser.add_argument(
        "--env-file",
        default=".env.local",
        help="Archivo opcional con HF_TOKEN o HUGGINGFACEHUB_API_TOKEN. Tiene prioridad sobre variables del entorno.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Valida dataset/token y muestra plan sin subir ni lanzar job.")
    args = parser.parse_args()

    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    if not dataset_dir.exists():
        raise FileNotFoundError(f"Dataset no encontrado: {dataset_dir}")
    dataset_manifest = _read_manifest(dataset_dir)

    script_path = DEFAULT_TRAIN_SCRIPT.resolve()
    if not script_path.exists():
        raise FileNotFoundError(f"Script remoto no encontrado: {script_path}")
    script_b64 = base64.b64encode(script_path.read_bytes()).decode("ascii")

    token = _resolve_token(args.env_file)
    who = _validate_token(token)
    hf_user = str(who.get("name") or who.get("fullname") or "")

    config: dict[str, Any] = {
        "schema_version": "hf_geometry_ocr_agent_job_submission_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "hf_user": hf_user,
        "dataset_dir": str(dataset_dir),
        "dataset_manifest": dataset_manifest,
        "dataset_repo_id": args.dataset_repo_id,
        "model_repo_id": args.model_repo_id,
        "base_model": args.base_model,
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "batch": args.batch,
        "grad_accum": args.grad_accum,
        "max_train_samples": args.max_train_samples,
        "max_eval_samples": args.max_eval_samples,
        "min_side_tokens": args.min_side_tokens,
        "max_side_tokens": args.max_side_tokens,
        "lora_rank": args.lora_rank,
        "lora_alpha": args.lora_alpha,
        "lora_dropout": args.lora_dropout,
        "oversample_error_type": args.oversample_error_type,
        "oversample_factor": args.oversample_factor,
        "logging_steps": args.logging_steps,
        "image": args.image,
        "flavor": args.flavor,
        "timeout_seconds": args.timeout_seconds,
        "train_script": str(script_path),
    }

    if args.dry_run:
        print(json.dumps({**config, "dry_run": True}, ensure_ascii=False, indent=2))
        return 0

    api = HfApi(token=token)
    api.create_repo(
        repo_id=args.dataset_repo_id,
        repo_type="dataset",
        private=bool(args.private_dataset),
        exist_ok=True,
    )
    api.upload_folder(
        repo_id=args.dataset_repo_id,
        repo_type="dataset",
        folder_path=str(dataset_dir),
        commit_message="Upload Geometry OCR angle-policy dataset",
    )

    command = _build_remote_command(config, script_b64)
    job = api.run_job(
        image=str(args.image),
        command=command,
        flavor=str(args.flavor),
        timeout=int(args.timeout_seconds),
        env={"PYTHONUNBUFFERED": "1", "TRAIN_SCRIPT_B64": script_b64},
        secrets={"HF_TOKEN": token},
    )

    config["submitted_at"] = datetime.now().isoformat(timespec="seconds")
    config["hf_job_id"] = getattr(job, "id", None)
    output_path = Path("submitted_jobs") / f"geometry_ocr_agent_angle_policy_v1_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] Job enviado: {config['hf_job_id']}")
    print(f"[INFO] Dataset: https://huggingface.co/datasets/{args.dataset_repo_id}")
    print(f"[INFO] Modelo LoRA: https://huggingface.co/{args.model_repo_id}")
    print(f"[INFO] Manifest local: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CLI entrypoint should return a concise actionable error.
        print(f"[ERROR] {exc}")
        raise SystemExit(1)
