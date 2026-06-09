from __future__ import annotations

import argparse
import os
from pathlib import Path

import torch
from huggingface_hub import HfApi
from peft import PeftModel
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration


def main() -> int:
    parser = argparse.ArgumentParser(description="Fusiona el LoRA OCR con su modelo visual base.")
    parser.add_argument("--base-model", required=True)
    parser.add_argument("--adapter-model", required=True)
    parser.add_argument("--merged-model", required=True)
    args = parser.parse_args()

    token = os.environ["HF_TOKEN"]
    out_dir = Path("merged_math_ocr_model")
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        args.base_model,
        token=token,
        torch_dtype=torch.float16,
        low_cpu_mem_usage=True,
        device_map="auto",
    )
    model = PeftModel.from_pretrained(model, args.adapter_model, token=token)
    model = model.merge_and_unload()
    model.save_pretrained(str(out_dir), safe_serialization=True, max_shard_size="4GB")
    AutoProcessor.from_pretrained(args.base_model, token=token).save_pretrained(str(out_dir))

    api = HfApi(token=token)
    api.create_repo(repo_id=args.merged_model, private=True, exist_ok=True)
    api.upload_folder(
        repo_id=args.merged_model,
        folder_path=str(out_dir),
        commit_message="Upload merged math OCR vision model",
    )
    print(f"[OK] Modelo fusionado: https://huggingface.co/{args.merged_model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
