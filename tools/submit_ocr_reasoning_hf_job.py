from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from huggingface_hub import HfApi, get_token


def main() -> int:
    parser = argparse.ArgumentParser(description="Lanza fine-tuning LoRA OCR multimodal en Hugging Face Jobs.")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    token = get_token()
    if not token:
        raise RuntimeError("No se encontro token Hugging Face.")
    config = json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    script = Path("E:/Github/Auditor-IA/submitted_jobs/train_ocr_reasoning_hf.py")
    script_b64 = base64.b64encode(script.read_bytes()).decode("ascii")
    command = [
        "bash",
        "-lc",
        "python -m pip install -U 'huggingface_hub>=0.33' 'transformers>=4.49,<5' "
        "'accelerate>=1.2' 'peft>=0.14' pillow sentencepiece && "
        "python - <<'PY'\n"
        "import base64, os\n"
        "open('train_ocr_reasoning_hf.py', 'wb').write(base64.b64decode(os.environ['TRAIN_SCRIPT_B64']))\n"
        "PY\n"
        "python train_ocr_reasoning_hf.py "
        f"--dataset-repo-id {config['dataset_repo_id']} "
        f"--model-repo-id {config['model_repo_id']} "
        f"--base-model {config.get('base_model', 'Qwen/Qwen2.5-VL-3B-Instruct')} "
        f"--epochs {config.get('epochs', 5)} "
        f"--learning-rate {config.get('learning_rate', 0.0002)} "
        f"--batch {config.get('batch', 1)} "
        f"--grad-accum {config.get('grad_accum', 8)}",
    ]
    job = HfApi(token=token).run_job(
        image=str(config.get("image", "pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime")),
        command=command,
        flavor=str(config.get("flavor", "a10g-small")),
        timeout=int(config.get("timeout_seconds", 14400)),
        env={"PYTHONUNBUFFERED": "1", "TRAIN_SCRIPT_B64": script_b64},
        secrets={"HF_TOKEN": token},
    )
    print(f"[OK] Job enviado: {job.id}")
    print(f"[INFO] Dataset: {config['dataset_repo_id']}")
    print(f"[INFO] Modelo: {config['model_repo_id']}")
    print(f"[INFO] Flavor: {config.get('flavor', 'a10g-small')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
