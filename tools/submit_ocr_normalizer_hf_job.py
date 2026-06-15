from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime
from pathlib import Path

from huggingface_hub import HfApi, get_token

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modulos.instance_factory.runtime_env import load_factory_runtime_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Lanza fine-tuning LoRA del normalizador OCR textual.")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    load_factory_runtime_env(REPO_ROOT)
    token = get_token()
    if not token:
        raise RuntimeError("No se encontro token Hugging Face.")
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    script = Path("E:/Github/Auditor-IA/submitted_jobs/train_ocr_normalizer_hf.py")
    script_b64 = base64.b64encode(script.read_bytes()).decode("ascii")
    command = [
        "bash",
        "-lc",
        "python -m pip install -U 'huggingface_hub>=0.33' 'transformers>=4.49,<5' "
        "'datasets>=2.20' 'accelerate>=1.2' 'peft>=0.14' 'trl>=0.12' trackio sentencepiece && "
        "python - <<'PY'\n"
        "import base64, os\n"
        "open('train_ocr_normalizer_hf.py', 'wb').write(base64.b64decode(os.environ['TRAIN_SCRIPT_B64']))\n"
        "PY\n"
        "python train_ocr_normalizer_hf.py "
        f"--dataset-repo-id {config['dataset_repo_id']} "
        f"--model-repo-id {config['model_repo_id']} "
        f"--base-model {config.get('base_model', 'Qwen/Qwen2.5-0.5B-Instruct')} "
        f"--epochs {config.get('epochs', 8)} "
        f"--learning-rate {config.get('learning_rate', 0.0002)} "
        f"--batch {config.get('batch', 2)} "
        f"--grad-accum {config.get('grad_accum', 4)} "
        f"--max-length {config.get('max_length', 2048)}",
    ]
    job = HfApi(token=token).run_job(
        image=str(config.get("image", "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime")),
        command=command,
        flavor=str(config.get("flavor", "t4-small")),
        timeout=int(config.get("timeout_seconds", 7200)),
        env={"PYTHONUNBUFFERED": "1", "TRAIN_SCRIPT_B64": script_b64},
        secrets={"HF_TOKEN": token},
    )
    print(f"[OK] Job enviado: {job.id}")
    print(f"[INFO] Dataset: {config['dataset_repo_id']}")
    print(f"[INFO] Modelo: {config['model_repo_id']}")
    print(f"[INFO] Flavor: {config.get('flavor', 't4-small')}")
    job_record = {
        "schema_version": "hf_ocr_normalizer_job_submission_v1",
        "submitted_at": datetime.now().isoformat(timespec="seconds"),
        "job_id": str(job.id),
        "dataset_repo_id": config["dataset_repo_id"],
        "model_repo_id": config["model_repo_id"],
        "base_model": config.get("base_model", "Qwen/Qwen2.5-0.5B-Instruct"),
        "flavor": str(config.get("flavor", "t4-small")),
        "timeout_seconds": int(config.get("timeout_seconds", 7200)),
        "config_path": str(Path(args.config).expanduser().resolve()),
    }
    out_path = REPO_ROOT / "submitted_jobs" / "hf_ocr_normalizer_job_last.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(job_record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[INFO] Registro local: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
