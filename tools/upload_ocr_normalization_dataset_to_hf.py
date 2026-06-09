from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import HfApi, get_token


def main() -> int:
    parser = argparse.ArgumentParser(description="Sube el dataset textual de normalizacion OCR.")
    parser.add_argument("--dataset-path", required=True)
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--private", action="store_true")
    args = parser.parse_args()
    token = get_token()
    if not token:
        raise RuntimeError("No se encontro token Hugging Face.")
    dataset_path = Path(args.dataset_path).expanduser().resolve()
    if not (dataset_path / "manifest.json").exists():
        raise FileNotFoundError(f"Dataset OCR invalido: {dataset_path}")
    api = HfApi(token=token)
    api.create_repo(repo_id=args.repo_id, repo_type="dataset", private=bool(args.private), exist_ok=True)
    api.upload_folder(
        repo_id=args.repo_id,
        repo_type="dataset",
        folder_path=str(dataset_path),
        commit_message="Upload raw-to-normalized math OCR dataset",
    )
    print(f"[OK] Dataset subido: https://huggingface.co/datasets/{args.repo_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
