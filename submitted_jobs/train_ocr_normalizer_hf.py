from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import torch
from huggingface_hub import HfApi, snapshot_download
from peft import LoraConfig, get_peft_model
from torch.utils.data import Dataset
from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments


class NormalizationDataset(Dataset):
    def __init__(self, root: Path, split: str, tokenizer: AutoTokenizer, max_length: int):
        path = root / f"{split}.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.examples: list[dict[str, torch.Tensor]] = []
        for row in rows:
            prompt_messages = row["messages"][:-1]
            full_messages = row["messages"]
            prompt = tokenizer.apply_chat_template(prompt_messages, tokenize=False, add_generation_prompt=True)
            full = tokenizer.apply_chat_template(full_messages, tokenize=False, add_generation_prompt=False)
            encoded = tokenizer(full, truncation=True, max_length=max_length)
            prompt_ids = tokenizer(prompt, truncation=True, max_length=max_length)["input_ids"]
            labels = list(encoded["input_ids"])
            for index in range(min(len(prompt_ids), len(labels))):
                labels[index] = -100
            encoded["labels"] = labels
            self.examples.append({key: torch.tensor(value) for key, value in encoded.items()})

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        return self.examples[index]


def main() -> int:
    parser = argparse.ArgumentParser()
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
    data_root = Path(snapshot_download(repo_id=args.dataset_repo_id, repo_type="dataset", token=token))
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, token=token)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        token=token,
        torch_dtype=torch.bfloat16,
        device_map="auto",
    )
    model.enable_input_require_grads()
    model.gradient_checkpointing_enable()
    model = get_peft_model(
        model,
        LoraConfig(
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        ),
    )

    def collate(rows: list[dict[str, torch.Tensor]]) -> dict[str, torch.Tensor]:
        return tokenizer.pad(rows, padding=True, return_tensors="pt")

    output_dir = Path("ocr_normalizer_output")
    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(output_dir),
            num_train_epochs=args.epochs,
            learning_rate=args.learning_rate,
            per_device_train_batch_size=args.batch,
            per_device_eval_batch_size=args.batch,
            gradient_accumulation_steps=args.grad_accum,
            logging_steps=2,
            eval_strategy="epoch",
            save_strategy="epoch",
            save_total_limit=2,
            bf16=True,
            report_to="none",
            remove_unused_columns=False,
            push_to_hub=False,
        ),
        train_dataset=NormalizationDataset(data_root, "train", tokenizer, args.max_length),
        eval_dataset=NormalizationDataset(data_root, "validation", tokenizer, args.max_length),
        data_collator=collate,
    )
    trainer.train()
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))
    api = HfApi(token=token)
    api.create_repo(repo_id=args.model_repo_id, private=True, exist_ok=True)
    api.upload_folder(repo_id=args.model_repo_id, folder_path=str(output_dir), commit_message="Upload OCR normalizer LoRA baseline")
    print(f"[OK] Modelo subido: https://huggingface.co/{args.model_repo_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
