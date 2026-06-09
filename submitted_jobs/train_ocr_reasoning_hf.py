from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import torch
from huggingface_hub import HfApi, snapshot_download
from PIL import Image
from peft import LoraConfig, get_peft_model
from torch.utils.data import Dataset
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration, Trainer, TrainingArguments

GENERAL_OCR_PROMPT = (
    "Transcribe fielmente todo el contenido visible de la imagen como OCR matematico final. "
    "No resuelvas, no expliques, no resumas y no inventes texto. "
    "Usa exactamente este formato: si la imagen muestra numero de problema, inicia con <n.>. "
    "Si no aparece numero y parece continuacion del problema anterior, inicia con [CONT.]. "
    "Conserva alternativas A), B), C), D), E) en el orden visible. "
    "Usa LaTeX entre $...$ para expresiones matematicas y notacion geometrica. "
    "No insertes etiquetas de imagen, no uses [[Imagen=...]] y no describas graficos; solo transcribe el texto visible."
)

GEOMETRY_OCR_PROMPT = (
    "Reglas geometricas: escribe puntos entre $...$, por ejemplo $A$, $B$, $C$. "
    "Usa $\\overline{AB}$ solo para el segmento; para medida de segmento usa $AB$ sin overline. "
    "Usa arcos como $\\overparen{AB}$. "
    "Usa $\\sphericalangle ABC$ para angulos y $m\\sphericalangle ABC$ para medidas de angulos. "
    "Las medidas sexagesimales van como $50^\\circ$. "
    "Usa $\\Delta ABC$ para triangulos. "
    "Usa $\\dfrac{...}{...}$ para fracciones y no uses \\displaystyle. "
    "No uses \\angle para angulos ni \\overline para medidas de segmentos."
)


def build_prompt_for_row(row: dict[str, str]) -> str:
    section = str(row.get("training_section") or row.get("section") or "").strip().casefold()
    if "geometr" in section:
        return f"{GENERAL_OCR_PROMPT} {GEOMETRY_OCR_PROMPT}"
    return GENERAL_OCR_PROMPT


class OcrDataset(Dataset):
    def __init__(self, root: Path, split: str):
        self.root = root
        path = root / f"{split}.jsonl"
        self.rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, str]:
        return self.rows[index]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-repo-id", required=True)
    parser.add_argument("--model-repo-id", required=True)
    parser.add_argument("--base-model", default="Qwen/Qwen2.5-VL-3B-Instruct")
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--grad-accum", type=int, default=8)
    args = parser.parse_args()

    token = os.environ["HF_TOKEN"]
    data_root = Path(snapshot_download(repo_id=args.dataset_repo_id, repo_type="dataset", token=token))
    processor = AutoProcessor.from_pretrained(args.base_model, token=token, min_pixels=256 * 28 * 28, max_pixels=768 * 28 * 28)
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
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

    def collate(rows: list[dict[str, str]]) -> dict[str, torch.Tensor]:
        images: list[Image.Image] = []
        texts: list[str] = []
        prompt_texts: list[str] = []
        for row in rows:
            image = Image.open(data_root / row["image"]).convert("RGB")
            images.append(image)
            user = [{"type": "image"}, {"type": "text", "text": build_prompt_for_row(row)}]
            prompt_messages = [{"role": "user", "content": user}]
            full_messages = prompt_messages + [{"role": "assistant", "content": [{"type": "text", "text": row["text"]}]}]
            prompt_texts.append(processor.apply_chat_template(prompt_messages, tokenize=False, add_generation_prompt=True))
            texts.append(processor.apply_chat_template(full_messages, tokenize=False, add_generation_prompt=False))
        batch = processor(text=texts, images=images, padding=True, return_tensors="pt")
        prompt_batch = processor(text=prompt_texts, images=images, padding=True, return_tensors="pt")
        labels = batch["input_ids"].clone()
        for idx, prompt_ids in enumerate(prompt_batch["input_ids"]):
            prompt_len = int(prompt_batch["attention_mask"][idx].sum().item())
            labels[idx, :prompt_len] = -100
        labels[batch["attention_mask"] == 0] = -100
        batch["labels"] = labels
        return batch

    output_dir = Path("ocr_reasoning_output")
    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=args.epochs,
        learning_rate=args.learning_rate,
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.grad_accum,
        logging_steps=2,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        bf16=True,
        report_to="none",
        remove_unused_columns=False,
        push_to_hub=False,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=OcrDataset(data_root, "train"),
        eval_dataset=OcrDataset(data_root, "validation"),
        data_collator=collate,
    )
    trainer.train()
    trainer.save_model(str(output_dir))
    processor.save_pretrained(str(output_dir))
    api = HfApi(token=token)
    api.create_repo(repo_id=args.model_repo_id, private=True, exist_ok=True)
    api.upload_folder(repo_id=args.model_repo_id, folder_path=str(output_dir), commit_message="Upload math OCR LoRA baseline")
    print(f"[OK] Modelo subido: https://huggingface.co/{args.model_repo_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
