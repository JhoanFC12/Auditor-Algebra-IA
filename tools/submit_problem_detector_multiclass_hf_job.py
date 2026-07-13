from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from huggingface_hub import HfApi, get_token


def main() -> int:
    parser = argparse.ArgumentParser(description="Lanza un job HF para entrenar detector YOLO multiclass.")
    parser.add_argument("--config", required=True, help="Ruta al JSON de configuracion.")
    args = parser.parse_args()

    token = get_token()
    if not token:
        print("[ERROR] No se encontro token de Hugging Face. Ejecuta `hf auth login` primero.")
        return 1

    config_path = Path(args.config).expanduser().resolve()
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    script_path = Path("E:/Github/Auditor-IA/submitted_jobs/train_problem_detector_multiclass_hf.py").resolve()
    if not script_path.exists():
        print(f"[ERROR] Script de entrenamiento no encontrado: {script_path}")
        return 1

    script_b64 = base64.b64encode(script_path.read_bytes()).decode("ascii")
    command = [
        "bash",
        "-lc",
        "python -m pip install -U 'huggingface_hub>=0.33.0' && "
        "python - <<'PY'\n"
        "import base64, os\n"
        "open('train_problem_detector_multiclass_hf.py', 'wb').write(base64.b64decode(os.environ['TRAIN_SCRIPT_B64']))\n"
        "PY\n"
        "python train_problem_detector_multiclass_hf.py "
        f"--dataset-repo-id {config['dataset_repo_id']} "
        f"--model-repo-id {config['model_repo_id']} "
        f"--epochs {int(config.get('epochs', 30))} "
        f"--batch {int(config.get('batch', 4))} "
        f"--imgsz {int(config.get('imgsz', 768))} "
        f"--base-model {config.get('base_model', 'base/pdf_problem_detector_yolov8n_v4_best.pt')} "
        f"--name {config.get('job_name', 'pilot')} "
        f"--workers {int(config.get('workers', 2))}",
    ]

    api = HfApi(token=token)
    job = api.run_job(
        image=str(config.get("image", "ultralytics/ultralytics:latest")),
        command=command,
        flavor=str(config.get("flavor", "t4-small")),
        timeout=int(config.get("timeout_seconds", 7200)),
        env={"PYTHONUNBUFFERED": "1", "TRAIN_SCRIPT_B64": script_b64},
        secrets={"HF_TOKEN": token},
    )
    print(json.dumps(
        {
            "job_id": job.id,
            "flavor": str(config.get("flavor", "t4-small")),
            "dataset_repo_id": config["dataset_repo_id"],
            "model_repo_id": config["model_repo_id"],
            "epochs": int(config.get("epochs", 30)),
            "batch": int(config.get("batch", 4)),
            "imgsz": int(config.get("imgsz", 768)),
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
