from __future__ import annotations

import argparse
import os

import torch
from datasets import load_dataset
from huggingface_hub import HfApi
from peft import LoraConfig
from trl import SFTConfig, SFTTrainer


def main() -> int:
    parser = argparse.ArgumentParser(description="Fine-tuning LoRA para normalizador OCR -> LaTeX final.")
    parser.add_argument("--dataset-repo-id", required=True)
    parser.add_argument("--model-repo-id", required=True)
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--batch", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=2048)
    args = parser.parse_args()

    token = os.environ["HF_TOKEN"]
    HfApi(token=token).create_repo(repo_id=args.model_repo_id, private=True, exist_ok=True)
    dataset = load_dataset(args.dataset_repo_id, token=token)
    if "train" not in dataset:
        raise RuntimeError(f"Dataset sin split train: {args.dataset_repo_id}")
    eval_dataset = dataset["validation"] if "validation" in dataset else None

    peft_config = LoraConfig(
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    train_args = SFTConfig(
        output_dir="ocr_normalizer_output",
        max_length=args.max_length,
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        gradient_checkpointing=True,
        logging_steps=2,
        eval_strategy="epoch" if eval_dataset is not None else "no",
        save_strategy="epoch",
        save_total_limit=2,
        fp16=torch.cuda.is_available(),
        bf16=False,
        report_to=["trackio"],
        project="auditor-ia-normalizer",
        run_name="normalizer-v1-300-qwen25-05b-lora",
        push_to_hub=True,
        hub_model_id=args.model_repo_id,
        hub_private_repo=True,
        hub_strategy="every_save",
    )
    trainer = SFTTrainer(
        model=args.base_model,
        args=train_args,
        train_dataset=dataset["train"],
        eval_dataset=eval_dataset,
        peft_config=peft_config,
    )
    trainer.train()
    trainer.push_to_hub(commit_message="Upload OCR normalizer LoRA v1")
    print(f"[OK] Modelo subido: https://huggingface.co/{args.model_repo_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
