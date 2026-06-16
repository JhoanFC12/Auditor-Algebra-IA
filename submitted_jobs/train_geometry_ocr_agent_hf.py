# /// script
# dependencies = [
#   "accelerate>=1.2",
#   "huggingface_hub>=0.33.0",
#   "peft>=0.14",
#   "pillow",
#   "sentencepiece",
#   "torch",
#   "transformers>=4.49,<5",
# ]
# ///
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from huggingface_hub import HfApi, snapshot_download
from PIL import Image
from peft import LoraConfig, get_peft_model
from torch.utils.data import Dataset
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration, Trainer, TrainingArguments


def _load_jsonl(path: Path, *, limit: int = 0) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[:limit] if limit and limit > 0 else rows


def _oversample_rows(rows: list[dict[str, Any]], *, error_type: str = "", factor: int = 1) -> list[dict[str, Any]]:
    selected_error = str(error_type or "").strip()
    repeat_factor = max(1, int(factor or 1))
    if not selected_error or repeat_factor <= 1:
        return list(rows)
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(row)
        error_types = row.get("error_types") if isinstance(row.get("error_types"), list) else []
        if selected_error in {str(item) for item in error_types}:
            out.extend(row for _ in range(repeat_factor - 1))
    return out


class GeometryOcrDataset(Dataset):
    def __init__(
        self,
        root: Path,
        split: str,
        *,
        limit: int = 0,
        oversample_error_type: str = "",
        oversample_factor: int = 1,
    ) -> None:
        rows = _load_jsonl(root / f"{split}.jsonl")
        if split == "train":
            rows = _oversample_rows(
                rows,
                error_type=oversample_error_type,
                factor=oversample_factor,
            )
        if limit and limit > 0:
            rows = rows[:limit]
        self.root = root
        self.rows = rows

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self.rows[index]


def _dtype_for_device() -> tuple[Any, bool, bool]:
    if not torch.cuda.is_available():
        return torch.float32, False, False
    if torch.cuda.is_bf16_supported():
        return torch.bfloat16, True, False
    return torch.float16, False, True


def main() -> int:
    parser = argparse.ArgumentParser(description="Train Geometry OCR LoRA on Hugging Face Jobs.")
    parser.add_argument("--dataset-repo-id", required=True)
    parser.add_argument("--model-repo-id", required=True)
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
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise RuntimeError("HF_TOKEN no encontrado en variables de entorno.")

    data_root = Path(snapshot_download(repo_id=args.dataset_repo_id, repo_type="dataset", token=token)).resolve()
    manifest_path = data_root / "manifest.json"
    dataset_manifest = {}
    if manifest_path.exists():
        dataset_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    train_dataset = GeometryOcrDataset(
        data_root,
        "train",
        limit=args.max_train_samples,
        oversample_error_type=args.oversample_error_type,
        oversample_factor=args.oversample_factor,
    )
    eval_dataset = GeometryOcrDataset(data_root, "validation", limit=args.max_eval_samples)
    test_rows = _load_jsonl(data_root / "test.jsonl")
    if len(train_dataset) == 0:
        raise ValueError("Dataset sin muestras train.")
    if len(eval_dataset) == 0:
        raise ValueError("Dataset sin muestras validation.")

    min_pixels = int(args.min_side_tokens) * 28 * 28
    max_pixels = int(args.max_side_tokens) * 28 * 28
    processor = AutoProcessor.from_pretrained(args.base_model, token=token, min_pixels=min_pixels, max_pixels=max_pixels)
    dtype, use_bf16, use_fp16 = _dtype_for_device()
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        args.base_model,
        token=token,
        torch_dtype=dtype,
        device_map="auto" if torch.cuda.is_available() else None,
    )
    model.enable_input_require_grads()
    model.gradient_checkpointing_enable()
    model = get_peft_model(
        model,
        LoraConfig(
            r=int(args.lora_rank),
            lora_alpha=int(args.lora_alpha),
            lora_dropout=float(args.lora_dropout),
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        ),
    )

    def collate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        images: list[Image.Image] = []
        full_texts: list[str] = []
        prompt_texts: list[str] = []
        for row in rows:
            image = Image.open(data_root / str(row["image"])).convert("RGB")
            prompt = str(row.get("prompt") or "")
            target = str(row.get("text") or "")
            prompt_messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": prompt}]}]
            full_messages = prompt_messages + [{"role": "assistant", "content": [{"type": "text", "text": target}]}]
            images.append(image)
            prompt_texts.append(processor.apply_chat_template(prompt_messages, tokenize=False, add_generation_prompt=True))
            full_texts.append(processor.apply_chat_template(full_messages, tokenize=False, add_generation_prompt=False))
        batch = processor(text=full_texts, images=images, padding=True, return_tensors="pt")
        prompt_batch = processor(text=prompt_texts, images=images, padding=True, return_tensors="pt")
        labels = batch["input_ids"].clone()
        for idx in range(labels.shape[0]):
            prompt_len = int(prompt_batch["attention_mask"][idx].sum().item())
            labels[idx, :prompt_len] = -100
        labels[batch["attention_mask"] == 0] = -100
        batch["labels"] = labels
        return batch

    output_dir = Path("geometry_ocr_agent_lora_output").resolve()
    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=float(args.epochs),
        learning_rate=float(args.learning_rate),
        per_device_train_batch_size=int(args.batch),
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=int(args.grad_accum),
        logging_steps=max(1, int(args.logging_steps)),
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        bf16=use_bf16,
        fp16=use_fp16,
        report_to="none",
        remove_unused_columns=False,
        push_to_hub=False,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=collate,
    )
    trainer.train()
    trainer.save_model(str(output_dir))
    processor.save_pretrained(str(output_dir))
    training_manifest = {
        "schema_version": "hf_geometry_ocr_agent_lora_run_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_repo_id": args.dataset_repo_id,
        "model_repo_id": args.model_repo_id,
        "base_model": args.base_model,
        "dataset_manifest": dataset_manifest,
        "train_samples": len(train_dataset),
        "validation_samples": len(eval_dataset),
        "test_samples": len(test_rows),
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "batch": args.batch,
        "grad_accum": args.grad_accum,
        "oversample_error_type": args.oversample_error_type,
        "oversample_factor": args.oversample_factor,
        "dtype": "bf16" if use_bf16 else ("fp16" if use_fp16 else "fp32"),
        "cuda": bool(torch.cuda.is_available()),
        "angle_policy": "Usar \\sphericalangle y m\\sphericalangle; no usar <, \\lt ni \\leq como simbolo de angulo.",
    }
    (output_dir / "training_manifest.json").write_text(
        json.dumps(training_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    api = HfApi(token=token)
    api.create_repo(repo_id=args.model_repo_id, repo_type="model", private=True, exist_ok=True)
    api.upload_folder(
        repo_id=args.model_repo_id,
        repo_type="model",
        folder_path=str(output_dir),
        commit_message="Upload Geometry OCR angle-policy LoRA",
    )
    print(f"[OK] Modelo subido: https://huggingface.co/{args.model_repo_id}")
    print(json.dumps(training_manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
