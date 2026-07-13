from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from utils.project_layout import normalize_instance_name, slugify_name


DEFAULT_SERVER_STORAGE_ROOT = "/srv/mathcontentstudio"
SERVER_STORAGE_ROOT_ENV = "MCS_SERVER_STORAGE_ROOT"
SERVER_ASSET_NAMESPACE_ENV = "MCS_FACTORY_ASSET_NAMESPACE"
SERVER_PUBLIC_ASSET_BASE_URL_ENV = "MCS_PUBLIC_ASSET_BASE_URL"

WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
UNC_RE = re.compile(r"^\\\\")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def _clean_env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def is_windows_or_unc_path(value: str | Path) -> bool:
    text = str(value or "").strip()
    return bool(WINDOWS_DRIVE_RE.match(text) or UNC_RE.match(text))


def is_url(value: str | Path) -> bool:
    return bool(URL_RE.match(str(value or "").strip()))


def _safe_file_part(value: str, fallback: str = "artifact") -> str:
    text = str(value or "").strip().replace("\\", "/").split("/")[-1]
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip(".-")
    return text or fallback


def _safe_path_parts(values: tuple[str | Path, ...]) -> list[str]:
    parts: list[str] = []
    for raw in values:
        text = str(raw or "").strip().replace("\\", "/")
        for part in text.split("/"):
            clean = _safe_file_part(part, "part")
            if clean not in {"", ".", ".."}:
                parts.append(clean)
    return parts


def _public_url(base_url: str, asset_key: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/{asset_key.lstrip('/')}"


@dataclass(frozen=True)
class ServerStorageConfig:
    root: Path
    namespace: str = "factory"
    public_asset_base_url: str = ""

    @classmethod
    def from_env(cls) -> "ServerStorageConfig":
        return cls(
            root=Path(_clean_env(SERVER_STORAGE_ROOT_ENV, DEFAULT_SERVER_STORAGE_ROOT)).expanduser(),
            namespace=_clean_env(SERVER_ASSET_NAMESPACE_ENV, "factory") or "factory",
            public_asset_base_url=_clean_env(SERVER_PUBLIC_ASSET_BASE_URL_ENV, ""),
        )


class ServerStorageResolver:
    """Resolve server-side artifact paths without persisting Windows paths."""

    def __init__(self, config: ServerStorageConfig | None = None, *, root: str | Path | None = None) -> None:
        if config is None:
            config = ServerStorageConfig(root=Path(root).expanduser()) if root is not None else ServerStorageConfig.from_env()
        self.config = config

    @property
    def root(self) -> Path:
        return self.config.root

    @property
    def namespace_root(self) -> Path:
        return self.root / normalize_instance_name(self.config.namespace, "factory")

    def instance_root(self, *, book_code: str, instance_code: str) -> Path:
        safe_book = slugify_name(book_code, "libro")
        safe_instance = normalize_instance_name(instance_code, "instancia")
        return self.namespace_root / "instances" / safe_book / safe_instance

    def staging_root(self, *, book_code: str, instance_code: str) -> Path:
        return self.instance_root(book_code=book_code, instance_code=instance_code) / "staging"

    def jobs_root(self) -> Path:
        return self.namespace_root / "jobs"

    def training_root(self, model_family: str = "") -> Path:
        base = self.namespace_root / "training"
        return base / normalize_instance_name(model_family, "general") if model_family else base

    def artifact_path(
        self,
        *,
        book_code: str,
        instance_code: str,
        kind: str,
        parts: tuple[str | Path, ...] = (),
    ) -> Path:
        safe_kind = normalize_instance_name(kind, "artifacts")
        safe_parts = _safe_path_parts(parts)
        return self.staging_root(book_code=book_code, instance_code=instance_code).joinpath(safe_kind, *safe_parts)

    def ensure_parent(self, path: Path) -> Path:
        safe = self.ensure_under_root(path)
        safe.parent.mkdir(parents=True, exist_ok=True)
        return safe

    def ensure_under_root(self, path: str | Path) -> Path:
        candidate = Path(path).expanduser()
        try:
            candidate_resolved = candidate.resolve(strict=False)
            root_resolved = self.root.resolve(strict=False)
            candidate_resolved.relative_to(root_resolved)
        except Exception as exc:
            raise ValueError(f"Path is outside server storage root: {candidate}") from exc
        return candidate

    def asset_key_for_path(self, path: str | Path) -> str:
        safe = self.ensure_under_root(path)
        relative = safe.resolve(strict=False).relative_to(self.root.resolve(strict=False))
        return relative.as_posix()

    def resolve_asset_key(self, asset_key: str) -> Path:
        raw = str(asset_key or "").strip().replace("\\", "/")
        pure = PurePosixPath(raw)
        if raw.startswith("/") or pure.is_absolute() or ".." in pure.parts or ":" in raw:
            raise ValueError(f"Unsafe server asset key: {asset_key}")
        return self.ensure_under_root(self.root.joinpath(*pure.parts))

    def artifact_record(self, path: str | Path, *, kind: str = "", include_absolute_path: bool = False) -> dict[str, Any]:
        asset_key = self.asset_key_for_path(path)
        payload: dict[str, Any] = {
            "schema_version": "server_storage_artifact_v1",
            "kind": str(kind or ""),
            "asset_key": asset_key,
            "public_url": _public_url(self.config.public_asset_base_url, asset_key),
        }
        if include_absolute_path:
            payload["server_path"] = str(self.ensure_under_root(path))
        return payload

    def classify_reference(self, value: str | Path) -> str:
        text = str(value or "").strip()
        if not text:
            return "empty"
        if is_url(text):
            return "url"
        if ".." in PurePosixPath(text.replace("\\", "/")).parts:
            return "unsafe_parent_traversal"
        try:
            candidate = Path(text).expanduser()
            if candidate.is_absolute():
                self.ensure_under_root(candidate)
                return "server_storage_path"
        except ValueError:
            if is_windows_or_unc_path(text):
                return "windows_or_unc"
            return "absolute_outside_server_storage"
        except Exception:
            if is_windows_or_unc_path(text):
                return "windows_or_unc"
            return "absolute_outside_server_storage"
        if is_windows_or_unc_path(text):
            return "windows_or_unc"
        if "/" in text.replace("\\", "/"):
            return "server_asset_key"
        return "relative_or_identifier"

    def is_server_safe_reference(self, value: str | Path) -> bool:
        return self.classify_reference(value) in {"server_storage_path", "server_asset_key", "relative_or_identifier", "url"}


def default_server_storage() -> ServerStorageResolver:
    return ServerStorageResolver()
