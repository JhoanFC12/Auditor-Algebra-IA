# /// script
# dependencies = ["ultralytics>=8.3.0", "huggingface_hub>=0.33.0"]
# ///
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download
from huggingface_hub.errors import HfHubHTTPError
from ultralytics import YOLO


CLASS_NAMES = {
    0: "problem",
    1: "problem_number",
    2: "answer_block",
}


def _resolve_base_model(dataset_dir: Path, raw: str) -> str:
    value = str(raw or "").strip() or "yolov8n.pt"
    relative = dataset_dir / value
    if relative.exists():
        return str(relative)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Entrena detector YOLO multiclass para problemas/numeracion/alternativas.")
    parser.add_argument("--dataset-repo-id", required=True)
    parser.add_argument("--model-repo-id", required=True)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--imgsz", type=int, default=768)
    parser.add_argument("--base-model", default="base/pdf_problem_detector_yolov8n_v4_best.pt")
    parser.add_argument("--project", default="/tmp/runs/problem_detector_multiclass")
    parser.add_argument("--name", default="pilot")
    parser.add_argument("--device", default="0")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise RuntimeError("HF_TOKEN no encontrado en variables de entorno.")

    dataset_dir = Path(
        snapshot_download(
            repo_id=args.dataset_repo_id,
            repo_type="dataset",
            token=token,
        )
    ).resolve()

    runtime_yaml = dataset_dir / "dataset.runtime.yaml"
    runtime_yaml.write_text(
        "\n".join(
            [
                f"path: {dataset_dir.as_posix()}",
                "train: images/train",
                "val: images/val",
                "names:",
                "  0: problem",
                "  1: problem_number",
                "  2: answer_block",
                "",
            ]
        ),
        encoding="utf-8",
    )

    base_model = _resolve_base_model(dataset_dir, args.base_model)
    print(f"[INFO] dataset_dir={dataset_dir}", flush=True)
    print(f"[INFO] runtime_yaml={runtime_yaml}", flush=True)
    print(f"[INFO] base_model={base_model}", flush=True)

    model = YOLO(base_model)
    result = model.train(
        data=str(runtime_yaml),
        epochs=int(args.epochs),
        batch=int(args.batch),
        imgsz=int(args.imgsz),
        project=str(args.project),
        name=str(args.name),
        device=str(args.device),
        workers=int(args.workers),
        plots=True,
        seed=42,
        exist_ok=True,
    )

    save_dir = Path(str(result.save_dir)).resolve()
    weights_dir = save_dir / "weights"
    summary = {
        "schema_version": "problem_detector_multiclass_hf_training_v1",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "dataset_repo_id": args.dataset_repo_id,
        "model_repo_id": args.model_repo_id,
        "base_model": base_model,
        "epochs": int(args.epochs),
        "batch": int(args.batch),
        "imgsz": int(args.imgsz),
        "classes": CLASS_NAMES,
        "save_dir": str(save_dir),
        "best": str(weights_dir / "best.pt"),
        "last": str(weights_dir / "last.pt"),
    }
    (save_dir / "training_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    api = HfApi(token=token)
    try:
        api.create_repo(repo_id=args.model_repo_id, repo_type="model", private=True, exist_ok=True)
    except HfHubHTTPError as exc:
        print(f"[WARN] No se pudo crear repo de modelo automaticamente: {exc}", flush=True)
        print("[WARN] Continuo asumiendo que ya existe y tienes permisos.", flush=True)

    api.upload_folder(
        repo_id=args.model_repo_id,
        repo_type="model",
        folder_path=str(save_dir),
        commit_message=f"Upload problem detector multiclass run {args.name}",
    )
    print(f"[OK] Modelo subido: https://huggingface.co/{args.model_repo_id}", flush=True)
    print(f"[OK] best.pt: {weights_dir / 'best.pt'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
