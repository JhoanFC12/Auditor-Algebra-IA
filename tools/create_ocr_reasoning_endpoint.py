from __future__ import annotations

import argparse
import os
from pathlib import Path

from huggingface_hub import HfApi, constants, get_token
from huggingface_hub.utils import get_session, hf_raise_for_status


def _read_env_file_token(path: Path) -> str:
    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in {"HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN"}:
            return value.strip().strip('"').strip("'")
    return ""


def _resolve_token(env_file: str = "") -> str:
    if env_file:
        token = _read_env_file_token(Path(env_file).expanduser().resolve())
        if token:
            return token
    for name in ("HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return (get_token() or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Crea o reutiliza el endpoint OCR multimodal con scale-to-zero.")
    parser.add_argument("--name", default="math-ocr-lora-v1")
    parser.add_argument("--repository", default="Jhoan12/math-ocr-qwen2.5-vl-3b-merged-v1")
    parser.add_argument("--timeout", type=int, default=180, help="Segundos de inactividad antes de apagar la GPU.")
    parser.add_argument("--env-file", default=".env.local")
    args = parser.parse_args()
    token = _resolve_token(args.env_file)
    if not token:
        raise RuntimeError("No se encontro token Hugging Face.")
    api = HfApi(token=token)
    existing = {endpoint.name: endpoint for endpoint in api.list_inference_endpoints()}
    endpoint = existing.get(args.name)
    if endpoint is None:
        namespace = api.whoami()["name"]
        response = get_session().post(
            f"{constants.INFERENCE_ENDPOINTS_ENDPOINT}/endpoint/{namespace}",
            headers=api._build_hf_headers(token=token),
            json={
                "name": args.name,
                "type": "protected",
                "provider": {"vendor": "aws", "region": "us-east-1"},
                "compute": {
                    "accelerator": "gpu",
                    "instanceType": "nvidia-l4",
                    "instanceSize": "x1",
                    "scaling": {
                        "minReplica": 0,
                        "maxReplica": 1,
                        "scaleToZeroTimeout": args.timeout,
                    },
                },
                "model": {
                    "repository": args.repository,
                    "framework": "pytorch",
                    "task": "image-text-to-text",
                    "image": {
                        "vLLM": {
                            "url": "vllm/vllm-openai:v0.14.1",
                            "healthRoute": "/health",
                            "port": 8000,
                            "tensorParallelSize": 1,
                            "maxNumSeqs": 1,
                        }
                    },
                    "args": ["--max-model-len", "4096", "--enforce-eager"],
                    "env": {},
                    "secrets": {},
                },
            },
        )
        hf_raise_for_status(response)
        endpoint = api.get_inference_endpoint(args.name)
    print(f"name={endpoint.name}")
    print(f"status={endpoint.status}")
    print(f"url={endpoint.url}")
    print(f"repository={endpoint.repository}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
