from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from .normalizer_inference import HfOcrNormalizerClient
from .runtime_env import load_factory_runtime_env


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Worker aislado para normalizador OCR local.")
    parser.add_argument("--input", required=True, help="Ruta del JSON de entrada.")
    parser.add_argument("--output", required=True, help="Ruta donde escribir el resultado JSON.")
    args = parser.parse_args(argv)

    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ["HF_OCR_NORMALIZER_IN_WORKER"] = "1"
    load_factory_runtime_env(root)

    input_path = Path(args.input)
    output_path = Path(args.output)
    try:
        input_payload = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(input_payload, dict):
            raise ValueError("La entrada del normalizador debe ser un objeto JSON.")
        model = str(os.getenv("HF_OCR_NORMALIZER_MODEL", "") or "").strip()
        client = HfOcrNormalizerClient(model=model)
        result = client.generate_final_latex(input_payload)
        _write_json(output_path, {"ok": True, "result": result})
        return 0
    except BaseException as exc:  # noqa: BLE001 - the parent process must receive crashes as data.
        _write_json(
            output_path,
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
