# /// script
# dependencies = ["ultralytics>=8.4.46", "huggingface_hub>=0.33.0"]
# ///
from __future__ import annotations

import argparse
import os
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download
from huggingface_hub.errors import HfHubHTTPError
from ultralytics import YOLO


def main() -> int:
    parser = argparse.ArgumentParser(description="Entrena un detector de gráficos YOLO en Hugging Face Jobs.")
    parser.add_argument("--dataset-repo-id", required=True, help="Repo dataset en HF, p.ej. usuario/dataset")
    parser.add_argument("--model-repo-id", required=True, help="Repo modelo destino en HF")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--imgsz", type=int, default=1024)
    parser.add_argument("--base-model", default="yolov8n.pt")
    parser.add_argument("--class-name", default="problema_segmentado")
    parser.add_argument("--project", default="/tmp/runs/graph_detector")
    parser.add_argument("--name", default="baseline")
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
    data_yaml = dataset_dir / "dataset.yaml"
    if not data_yaml.exists():
        raise FileNotFoundError(f"No se encontró dataset.yaml en {dataset_dir}")
    runtime_yaml = dataset_dir / "dataset.runtime.yaml"
    runtime_yaml.write_text(
        "\n".join(
            [
                f"path: {str(dataset_dir).replace(chr(92), '/')}",
                "train: images/train",
                "val: images/val",
                "test: images/test",
                "names:",
                f"  0: {args.class_name}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    model = YOLO(args.base_model)
    result = model.train(
        data=str(runtime_yaml),
        epochs=int(args.epochs),
        batch=int(args.batch),
        imgsz=int(args.imgsz),
        project=str(args.project),
        name=str(args.name),
    )

    save_dir = Path(str(result.save_dir)).resolve()
    weights_dir = save_dir / "weights"

    api = HfApi(token=token)
    try:
        api.create_repo(repo_id=args.model_repo_id, repo_type="model", private=True, exist_ok=True)
    except HfHubHTTPError as exc:
        print(f"[WARN] No se pudo crear el repo del modelo automáticamente: {exc}")
        print("[WARN] Continuaré asumiendo que el repo ya existe y tiene permisos de escritura.")
    api.upload_folder(
        repo_id=args.model_repo_id,
        repo_type="model",
        folder_path=str(save_dir),
        commit_message=f"Upload training run {args.name}",
    )
    print(f"[OK] Entrenamiento completo. Modelo subido a https://huggingface.co/{args.model_repo_id}")
    print(f"[INFO] save_dir={save_dir}")
    print(f"[INFO] best={weights_dir / 'best.pt'}")
    print(f"[INFO] last={weights_dir / 'last.pt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
