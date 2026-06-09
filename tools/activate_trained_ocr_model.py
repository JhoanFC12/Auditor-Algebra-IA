from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path


def replace_or_append(lines: list[str], key: str, value: str) -> None:
    prefix = f"{key}="
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{prefix}{value}"
            return
    lines.append(f"{prefix}{value}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Activa un endpoint OCR entrenado en la app conservando respaldo.")
    parser.add_argument("--env-file", default=".env.local")
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint-url", required=True)
    parser.add_argument("--endpoint-name", default="")
    args = parser.parse_args()

    env_path = Path(args.env_file).expanduser().resolve()
    if not env_path.exists():
        raise FileNotFoundError(f"No existe el archivo de entorno: {env_path}")
    endpoint_url = args.endpoint_url.rstrip("/")
    if not endpoint_url.endswith("/v1"):
        endpoint_url += "/v1"

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = env_path.with_name(f"{env_path.name}.backup_{stamp}")
    shutil.copy2(env_path, backup_path)

    lines = env_path.read_text(encoding="utf-8").splitlines()
    replace_or_append(lines, "SCAN_PROVIDER", "hf")
    replace_or_append(lines, "HF_MODEL", args.model)
    replace_or_append(lines, "HF_TRAINED_OCR_BASE_URL", endpoint_url)
    if args.endpoint_name.strip():
        replace_or_append(lines, "HF_TRAINED_OCR_ENDPOINT_NAME", args.endpoint_name.strip())
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[OK] Modelo OCR activo: {args.model}")
    print(f"[OK] Endpoint OCR activo: {endpoint_url}")
    if args.endpoint_name.strip():
        print(f"[OK] Nombre endpoint OCR: {args.endpoint_name.strip()}")
    print(f"[OK] Respaldo: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
