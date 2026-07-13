from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from modulos.instance_factory.model_inventory import build_model_inventory_manifest
from modulos.instance_factory.runtime_env import load_factory_runtime_env


WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
SECRET_KEY_RE = re.compile(r"(^|_)(TOKEN|API_KEY|SECRET|PASSWORD)($|_)", re.IGNORECASE)


@dataclass(frozen=True)
class ModelStageAudit:
    stage: str
    model_id: str
    provider: str
    source: str
    resolved_path: str
    exists_locally: bool | None
    server_ready: bool
    server_action: str
    note: str = ""


@dataclass(frozen=True)
class ModelInventoryAudit:
    generated_at: str
    env_sources_loaded: dict[str, str]
    stages: list[ModelStageAudit]
    candidates_total: int
    warnings: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only inventory for PDF Factory models before server migration."
    )
    parser.add_argument(
        "--json-output",
        default=str(ROOT_DIR / "tmp" / "server_factory_inventory" / "model_inventory.json"),
        help="Where to write the machine-readable model inventory audit.",
    )
    parser.add_argument(
        "--markdown-output",
        default=str(ROOT_DIR / "docs" / "server_factory_inventory.md"),
        help="Where to write the human-readable model inventory audit.",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="Do not load .env/.env.local before resolving model defaults.",
    )
    return parser.parse_args()


def is_windows_or_unc_path(value: str) -> bool:
    text = str(value or "").strip()
    return bool(WINDOWS_DRIVE_RE.match(text) or text.startswith("\\\\") or text.startswith("//"))


def is_server_path(value: str) -> bool:
    return str(value or "").strip().replace("\\", "/").startswith("/srv/mathcontentstudio/")


def is_probable_local_path(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if is_windows_or_unc_path(text) or is_server_path(text):
        return True
    if text.startswith("/") and "://" not in text:
        return True
    return text.lower().endswith((".pt", ".onnx", ".safetensors", ".bin"))


def local_exists(value: str) -> bool | None:
    text = str(value or "").strip()
    if not text or not is_probable_local_path(text):
        return None
    try:
        return Path(text).expanduser().exists()
    except OSError:
        return False


def redact_env_sources(sources: dict[str, str]) -> dict[str, str]:
    return {key: ("<secret>" if SECRET_KEY_RE.search(key) else value) for key, value in sorted(sources.items())}


def classify_stage(stage: dict[str, Any]) -> ModelStageAudit:
    stage_name = str(stage.get("stage") or "").strip()
    model_id = str(stage.get("model_id") or "").strip()
    provider = str(stage.get("provider") or "").strip()
    source = str(stage.get("source") or "").strip()
    resolved_path = str(stage.get("resolved_path") or "").strip()
    path_to_check = resolved_path or (model_id if provider == "local" or is_probable_local_path(model_id) else "")
    exists = local_exists(path_to_check)

    if stage_name == "ocr":
        return ModelStageAudit(
            stage=stage_name,
            model_id=model_id,
            provider=provider,
            source=source,
            resolved_path=resolved_path,
            exists_locally=exists,
            server_ready=provider == "huggingface" and bool(model_id),
            server_action="keep_hugging_face_endpoint",
            note="OCR remains remote in this feature.",
        )

    if stage_name == "normalizer" and provider == "local_passthrough":
        return ModelStageAudit(
            stage=stage_name,
            model_id=model_id,
            provider=provider,
            source=source,
            resolved_path=resolved_path,
            exists_locally=exists,
            server_ready=True,
            server_action="deferred_passthrough",
            note="Normalizer model is outside the current server-factory MVP.",
        )

    if is_server_path(path_to_check):
        return ModelStageAudit(
            stage=stage_name,
            model_id=model_id,
            provider=provider,
            source=source,
            resolved_path=resolved_path,
            exists_locally=exists,
            server_ready=True,
            server_action="ready_on_server_storage",
        )

    if provider == "huggingface":
        return ModelStageAudit(
            stage=stage_name,
            model_id=model_id,
            provider=provider,
            source=source,
            resolved_path=resolved_path,
            exists_locally=exists,
            server_ready=False,
            server_action="download_or_mount_model_on_server_then_set_env",
            note="Detector/segmenter stages should run server-local, not as live HF inference.",
        )

    if is_windows_or_unc_path(path_to_check):
        return ModelStageAudit(
            stage=stage_name,
            model_id=model_id,
            provider=provider,
            source=source,
            resolved_path=resolved_path,
            exists_locally=exists,
            server_ready=False,
            server_action="copy_model_to_server_storage_and_repoint_env",
            note="Windows/UNC model path is not portable to Linux server.",
        )

    if provider == "local" and path_to_check:
        return ModelStageAudit(
            stage=stage_name,
            model_id=model_id,
            provider=provider,
            source=source,
            resolved_path=resolved_path,
            exists_locally=exists,
            server_ready=False,
            server_action="verify_or_copy_local_model_to_server_storage",
        )

    return ModelStageAudit(
        stage=stage_name,
        model_id=model_id,
        provider=provider,
        source=source,
        resolved_path=resolved_path,
        exists_locally=exists,
        server_ready=False,
        server_action="review_configuration",
    )


def build_audit(*, load_env_file: bool = True) -> ModelInventoryAudit:
    env_sources = load_factory_runtime_env(ROOT_DIR) if load_env_file else {}
    manifest = build_model_inventory_manifest()
    defaults = manifest.get("current_defaults") if isinstance(manifest.get("current_defaults"), dict) else {}
    stages_payload = defaults.get("stages") if isinstance(defaults.get("stages"), dict) else {}
    stages = [classify_stage(dict(row)) for _name, row in sorted(stages_payload.items()) if isinstance(row, dict)]
    warnings: list[str] = []
    for row in stages:
        if not row.model_id:
            warnings.append(f"{row.stage}: missing model_id")
        if row.provider == "local" and row.exists_locally is False:
            warnings.append(f"{row.stage}: local model path does not exist locally")
    return ModelInventoryAudit(
        generated_at=datetime.now(timezone.utc).isoformat(),
        env_sources_loaded=redact_env_sources(env_sources),
        stages=stages,
        candidates_total=len(manifest.get("candidates_from_config") or []),
        warnings=warnings,
    )


def render_markdown(audit: ModelInventoryAudit) -> str:
    lines: list[str] = []
    lines.append("# Server Factory Model Inventory")
    lines.append("")
    lines.append(f"Generated at: `{audit.generated_at}`")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Stages checked: `{len(audit.stages)}`")
    lines.append(f"- Config candidates found: `{audit.candidates_total}`")
    lines.append(f"- Warnings: `{len(audit.warnings)}`")
    lines.append("")
    lines.append("## Active Stages")
    lines.append("")
    lines.append("| Stage | Provider | Server ready | Action | Exists locally | Source | Model |")
    lines.append("|---|---|---:|---|---|---|---|")
    for row in audit.stages:
        exists = "n/a" if row.exists_locally is None else ("yes" if row.exists_locally else "no")
        model = (row.resolved_path or row.model_id).replace("|", "\\|")
        lines.append(
            f"| `{row.stage}` | `{row.provider}` | {'yes' if row.server_ready else 'no'} | "
            f"`{row.server_action}` | {exists} | `{row.source}` | `{model}` |"
        )
    lines.append("")
    if audit.warnings:
        lines.append("## Warnings")
        lines.append("")
        for warning in audit.warnings:
            lines.append(f"- {warning}")
        lines.append("")
    lines.append("## Environment Sources Loaded")
    lines.append("")
    if audit.env_sources_loaded:
        for key, source in audit.env_sources_loaded.items():
            lines.append(f"- `{key}`: `{source}`")
    else:
        lines.append("No environment file values were loaded.")
    lines.append("")
    lines.append("## Required Server Actions")
    lines.append("")
    lines.append("1. Copy detector and segmenter model files to server storage.")
    lines.append("2. Point server environment variables to `/srv/mathcontentstudio/...` paths.")
    lines.append("3. Keep OCR configured through the Hugging Face endpoint.")
    lines.append("4. Do not expose model paths or tokens in public API responses.")
    lines.append("")
    return "\n".join(lines)


def write_outputs(audit: ModelInventoryAudit, *, json_output: Path, markdown_output: Path) -> None:
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(asdict(audit), ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_output.write_text(render_markdown(audit), encoding="utf-8")


def main() -> None:
    args = parse_args()
    audit = build_audit(load_env_file=not args.no_env_file)
    write_outputs(audit, json_output=Path(args.json_output), markdown_output=Path(args.markdown_output))
    print(f"[ok] json: {Path(args.json_output)}")
    print(f"[ok] markdown: {Path(args.markdown_output)}")
    print(f"[ok] stages: {len(audit.stages)}")
    print(f"[ok] warnings: {len(audit.warnings)}")


if __name__ == "__main__":
    main()
