from __future__ import annotations

import argparse
import base64
from pathlib import Path

from huggingface_hub import HfApi, get_token


def main() -> int:
    parser = argparse.ArgumentParser(description="Fusiona un adaptador LoRA OCR visual y publica el modelo completo.")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-VL-3B-Instruct")
    parser.add_argument("--adapter-model", required=True)
    parser.add_argument("--merged-model", required=True)
    parser.add_argument("--flavor", default="a10g-small")
    parser.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    token = get_token()
    if not token:
        raise RuntimeError("No se encontro token Hugging Face.")
    script = Path("E:/Github/Auditor-IA/submitted_jobs/merge_ocr_reasoning_lora_hf.py")
    script_b64 = base64.b64encode(script.read_bytes()).decode("ascii")
    command = [
        "bash",
        "-lc",
        "python -m pip install -U 'huggingface_hub>=0.33' 'transformers>=4.49,<5' "
        "'accelerate>=1.2' 'peft>=0.14' sentencepiece && "
        "python - <<'PY'\n"
        "import base64, os\n"
        "open('merge_ocr_reasoning_lora_hf.py', 'wb').write(base64.b64decode(os.environ['MERGE_SCRIPT_B64']))\n"
        "PY\n"
        "python merge_ocr_reasoning_lora_hf.py "
        f"--base-model {args.base_model} "
        f"--adapter-model {args.adapter_model} "
        f"--merged-model {args.merged_model}",
    ]
    job = HfApi(token=token).run_job(
        image="pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime",
        command=command,
        flavor=args.flavor,
        timeout=args.timeout,
        env={"PYTHONUNBUFFERED": "1", "MERGE_SCRIPT_B64": script_b64},
        secrets={"HF_TOKEN": token},
    )
    print(f"[OK] Job enviado: {job.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
