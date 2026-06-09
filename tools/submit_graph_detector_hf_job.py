from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from huggingface_hub import HfApi, get_token


def main() -> int:
    parser = argparse.ArgumentParser(description="Lanza un job GPU en Hugging Face para entrenar el detector.")
    parser.add_argument("--config", required=True, help="Ruta al JSON de configuración.")
    args = parser.parse_args()

    token = get_token()
    if not token:
        print("[ERROR] No se encontró token de Hugging Face. Ejecuta `hf auth login` primero.")
        return 1

    config_path = Path(args.config).expanduser().resolve()
    if not config_path.exists():
        print(f"[ERROR] Config no encontrada: {config_path}")
        return 1

    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    script_path = Path("E:/Github/Auditor-IA/submitted_jobs/train_graph_detector_hf.py").resolve()
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
        "open('train_graph_detector_hf.py', 'wb').write(base64.b64decode(os.environ['TRAIN_SCRIPT_B64']))\n"
        "PY\n"
        "python train_graph_detector_hf.py "
        f"--dataset-repo-id {config['dataset_repo_id']} "
        f"--model-repo-id {config['model_repo_id']} "
        f"--epochs {config.get('epochs', 50)} "
        f"--batch {config.get('batch', 16)} "
        f"--imgsz {config.get('imgsz', 1024)} "
        f"--base-model {config.get('base_model', 'yolov8n.pt')} "
        f"--class-name {config.get('class_name', 'problema_segmentado')} "
        f"--name {config.get('job_name', 'graph_detector_job')}",
    ]

    api = HfApi(token=token)
    job = api.run_job(
        image=str(config.get("image", "ultralytics/ultralytics:latest")),
        command=command,
        flavor=str(config.get("flavor", "l4x1")),
        timeout=int(config.get("timeout_seconds", 14400)),
        env={"PYTHONUNBUFFERED": "1", "TRAIN_SCRIPT_B64": script_b64},
        secrets={"HF_TOKEN": token},
    )
    print(f"[OK] Job enviado: {job.id}")
    print(f"[INFO] Flavor: {config.get('flavor', 'l4x1')}")
    print(f"[INFO] Dataset repo: {config['dataset_repo_id']}")
    print(f"[INFO] Model repo: {config['model_repo_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
