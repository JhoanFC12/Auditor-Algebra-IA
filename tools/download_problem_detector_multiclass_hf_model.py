from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from huggingface_hub import hf_hub_download


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Descarga best.pt entrenado en HF para detector multiclass.")
    parser.add_argument("--model-repo-id", default="Jhoan12/pdf-problem-detector-multiclass-pilot-v1")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=repo_root() / "models" / "pdf_problem_detector_multiclass_pilot_v1",
    )
    parser.add_argument("--weights-path", default="weights/best.pt")
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    weights_dir = out_dir / "weights"
    weights_dir.mkdir(parents=True, exist_ok=True)

    downloaded = Path(
        hf_hub_download(
            repo_id=args.model_repo_id,
            filename=args.weights_path,
            repo_type="model",
        )
    )
    target = weights_dir / "best.pt"
    shutil.copy2(downloaded, target)

    metadata = {
        "schema_version": "problem_detector_multiclass_local_model_v1",
        "downloaded_at": datetime.now().isoformat(timespec="seconds"),
        "model_repo_id": args.model_repo_id,
        "hub_weights_path": args.weights_path,
        "local_weights_path": str(target),
        "classes": {
            "0": "problem",
            "1": "problem_number",
            "2": "answer_block",
        },
    }
    (out_dir / "model_card_local.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
