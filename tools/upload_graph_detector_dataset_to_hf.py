from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from huggingface_hub import HfApi, get_token


def _make_portable_copy(dataset_path: Path) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="hf_graph_detector_upload_"))
    portable_root = tmp_dir / dataset_path.name
    shutil.copytree(dataset_path, portable_root)
    dataset_yaml = portable_root / "dataset.yaml"
    if dataset_yaml.exists():
        lines = dataset_yaml.read_text(encoding="utf-8").splitlines()
        rewritten: list[str] = []
        path_rewritten = False
        for line in lines:
            if line.strip().startswith("path:"):
                rewritten.append("path: .")
                path_rewritten = True
            else:
                rewritten.append(line)
        if not path_rewritten:
            rewritten.insert(0, "path: .")
        dataset_yaml.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
    return portable_root


def main() -> int:
    parser = argparse.ArgumentParser(description="Sube un dataset detector de gráficos a Hugging Face Hub.")
    parser.add_argument("--dataset-path", required=True, help="Ruta local del dataset YOLO.")
    parser.add_argument("--repo-id", required=True, help="Repo destino en Hugging Face, p.ej. usuario/nombre.")
    parser.add_argument("--private", action="store_true", help="Crear el repo como privado.")
    parser.add_argument(
        "--commit-message",
        default="Upload graph detector dataset",
        help="Mensaje de commit para la subida.",
    )
    args = parser.parse_args()

    token = get_token()
    if not token:
        print("[ERROR] No se encontró token de Hugging Face. Ejecuta `hf auth login` primero.")
        return 1

    dataset_path = Path(args.dataset_path).expanduser().resolve()
    if not dataset_path.exists():
        print(f"[ERROR] Dataset no encontrado: {dataset_path}")
        return 1
    if not (dataset_path / "dataset.yaml").exists():
        print(f"[ERROR] No parece un dataset YOLO válido: falta dataset.yaml en {dataset_path}")
        return 1

    portable_dataset = _make_portable_copy(dataset_path)

    api = HfApi(token=token)
    api.create_repo(repo_id=args.repo_id, repo_type="dataset", private=bool(args.private), exist_ok=True)
    api.upload_folder(
        repo_id=args.repo_id,
        repo_type="dataset",
        folder_path=str(portable_dataset),
        commit_message=str(args.commit_message),
    )
    print(f"[OK] Dataset subido a https://huggingface.co/datasets/{args.repo_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
