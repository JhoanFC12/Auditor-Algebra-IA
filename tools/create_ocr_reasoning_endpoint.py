from __future__ import annotations

import argparse

from huggingface_hub import HfApi, constants, get_token
from huggingface_hub.utils import get_session, hf_raise_for_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Crea o reutiliza el endpoint OCR multimodal con scale-to-zero.")
    parser.add_argument("--name", default="math-ocr-lora-v1")
    parser.add_argument("--repository", default="Jhoan12/math-ocr-qwen2.5-vl-3b-merged-v1")
    parser.add_argument("--timeout", type=int, default=180, help="Segundos de inactividad antes de apagar la GPU.")
    args = parser.parse_args()
    token = get_token()
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
