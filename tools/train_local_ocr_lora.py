from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image


def _load_jsonl(path: Path, *, limit: int = 0) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[:limit] if limit and limit > 0 else rows


def inspect_dataset(dataset_dir: Path, *, max_train_samples: int = 0, max_eval_samples: int = 0) -> dict[str, Any]:
    dataset_dir = Path(dataset_dir).expanduser().resolve()
    train_rows = _load_jsonl(dataset_dir / "train.jsonl", limit=max_train_samples)
    validation_rows = _load_jsonl(dataset_dir / "validation.jsonl", limit=max_eval_samples)
    test_rows = _load_jsonl(dataset_dir / "test.jsonl")
    sample = train_rows[0] if train_rows else {}
    image_info: dict[str, Any] = {}
    if sample:
        image_path = dataset_dir / str(sample.get("image") or "")
        if image_path.exists():
            with Image.open(image_path) as image:
                image_info = {"path": str(image_path), "size": list(image.size), "mode": image.mode}
    return {
        "dataset_dir": str(dataset_dir),
        "train": len(train_rows),
        "validation": len(validation_rows),
        "test": len(test_rows),
        "sample_image": image_info,
        "sample_text_preview": str(sample.get("text") or "")[:240] if sample else "",
    }


def _import_training_stack():
    try:
        import torch
        from peft import LoraConfig, get_peft_model
        from torch.utils.data import Dataset
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration, Trainer, TrainingArguments
    except Exception as exc:  # pragma: no cover - optional heavy dependencies.
        raise RuntimeError(
            "Faltan dependencias de entrenamiento local. Instala el stack opcional, por ejemplo:\n"
            "python -m pip install -r requirements-local-ocr.txt\n"
            f"Detalle: {exc}"
        ) from exc
    return {
        "torch": torch,
        "LoraConfig": LoraConfig,
        "get_peft_model": get_peft_model,
        "Dataset": Dataset,
        "AutoProcessor": AutoProcessor,
        "Qwen2_5_VLForConditionalGeneration": Qwen2_5_VLForConditionalGeneration,
        "Trainer": Trainer,
        "TrainingArguments": TrainingArguments,
    }


class LocalOcrDataset:
    def __init__(self, dataset_cls: type, root: Path, split: str, *, limit: int = 0) -> None:
        class _Dataset(dataset_cls):
            def __init__(self, rows: list[dict[str, Any]]) -> None:
                self.rows = rows

            def __len__(self) -> int:
                return len(self.rows)

            def __getitem__(self, index: int) -> dict[str, Any]:
                return self.rows[index]

        self.dataset = _Dataset(_load_jsonl(root / f"{split}.jsonl", limit=limit))


def train(args: argparse.Namespace) -> dict[str, Any]:
    dataset_dir = Path(args.dataset_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    stack = _import_training_stack()
    torch = stack["torch"]
    Dataset = stack["Dataset"]
    AutoProcessor = stack["AutoProcessor"]
    Model = stack["Qwen2_5_VLForConditionalGeneration"]
    Trainer = stack["Trainer"]
    TrainingArguments = stack["TrainingArguments"]
    LoraConfig = stack["LoraConfig"]
    get_peft_model = stack["get_peft_model"]

    cuda_available = bool(torch.cuda.is_available())
    if not cuda_available and not args.allow_cpu:
        raise RuntimeError(
            "No se detecto GPU CUDA. Entrenar Qwen2.5-VL 3B en CPU no es practico. "
            "Usa --allow-cpu solo para pruebas muy pequenas."
        )

    train_dataset = LocalOcrDataset(Dataset, dataset_dir, "train", limit=args.max_train_samples).dataset
    eval_dataset = LocalOcrDataset(Dataset, dataset_dir, "validation", limit=args.max_eval_samples).dataset
    if len(train_dataset) == 0:
        raise ValueError("El dataset no tiene muestras train.")
    if len(eval_dataset) == 0:
        raise ValueError("El dataset no tiene muestras validation.")

    dtype = torch.float32
    use_bf16 = False
    use_fp16 = False
    if cuda_available:
        if torch.cuda.is_bf16_supported():
            dtype = torch.bfloat16
            use_bf16 = True
        else:
            dtype = torch.float16
            use_fp16 = True

    min_pixels = int(args.min_side_tokens) * 28 * 28
    max_pixels = int(args.max_side_tokens) * 28 * 28
    processor = AutoProcessor.from_pretrained(args.base_model, min_pixels=min_pixels, max_pixels=max_pixels)
    model = Model.from_pretrained(
        args.base_model,
        torch_dtype=dtype,
        device_map="auto" if cuda_available else None,
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
        images: list[Any] = []
        texts: list[str] = []
        prompt_texts: list[str] = []
        for row in rows:
            image = Image.open(dataset_dir / str(row["image"])).convert("RGB")
            images.append(image)
            prompt = str(row.get("prompt") or "")
            target = str(row.get("text") or "")
            prompt_messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": prompt}]}]
            full_messages = prompt_messages + [{"role": "assistant", "content": [{"type": "text", "text": target}]}]
            prompt_texts.append(processor.apply_chat_template(prompt_messages, tokenize=False, add_generation_prompt=True))
            texts.append(processor.apply_chat_template(full_messages, tokenize=False, add_generation_prompt=False))
        batch = processor(text=texts, images=images, padding=True, return_tensors="pt")
        prompt_batch = processor(text=prompt_texts, images=images, padding=True, return_tensors="pt")
        labels = batch["input_ids"].clone()
        for idx in range(labels.shape[0]):
            prompt_len = int(prompt_batch["attention_mask"][idx].sum().item())
            labels[idx, :prompt_len] = -100
        labels[batch["attention_mask"] == 0] = -100
        batch["labels"] = labels
        return batch

    output_dir.mkdir(parents=True, exist_ok=True)
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
    manifest = {
        "schema_version": "local_math_ocr_lora_run_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_dir": str(dataset_dir),
        "output_dir": str(output_dir),
        "base_model": args.base_model,
        "train_samples": len(train_dataset),
        "validation_samples": len(eval_dataset),
        "epochs": args.epochs,
        "learning_rate": args.learning_rate,
        "batch": args.batch,
        "grad_accum": args.grad_accum,
        "cuda": cuda_available,
        "dtype": "bf16" if use_bf16 else ("fp16" if use_fp16 else "fp32"),
    }
    (output_dir / "local_training_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Entrena un LoRA local para OCR matematico imagen->texto.")
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--output-dir", default="models/local_ocr/qwen2_5_vl_3b_lora_smoke")
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-VL-3B-Instruct")
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--max-train-samples", type=int, default=80)
    parser.add_argument("--max-eval-samples", type=int, default=20)
    parser.add_argument("--min-side-tokens", type=int, default=256)
    parser.add_argument("--max-side-tokens", type=int, default=768)
    parser.add_argument("--lora-rank", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--logging-steps", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true", help="Solo inspecciona dataset y GPU; no descarga modelo.")
    parser.add_argument("--allow-cpu", action="store_true")
    args = parser.parse_args()

    summary = inspect_dataset(
        Path(args.dataset_dir),
        max_train_samples=max(0, int(args.max_train_samples or 0)),
        max_eval_samples=max(0, int(args.max_eval_samples or 0)),
    )
    try:
        import torch

        summary["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            summary["cuda_device"] = torch.cuda.get_device_name(0)
            summary["cuda_memory_gb"] = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 2)
    except Exception as exc:
        summary["torch"] = f"no disponible: {exc}"
    if args.dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    manifest = train(args)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
