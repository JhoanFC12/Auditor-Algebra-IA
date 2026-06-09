from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict

from ..domain.image_binding import (
    IMAGE_BINDING_STATUS_MANUAL_CONFIRMED,
    IMAGE_BINDING_STATUS_NEEDS_REVIEW,
    IMAGE_BINDING_STATUS_NONE,
)
from ..state import TranscriptorSessionState


_IMAGE_TAG_RE = re.compile(r"\[\[\s*Imagen\s*=\s*([^\]]+?)\s*\]\]", re.IGNORECASE)


class SessionStore:
    def dump(self, state: TranscriptorSessionState, path: Path) -> None:
        payload = state.to_dict()
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, path: Path) -> TranscriptorSessionState:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        migrated = self.migrate_legacy(raw)
        return TranscriptorSessionState.from_dict(migrated)

    def migrate_legacy(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = payload if isinstance(payload, dict) else {}
        if "session_schema_version" in data:
            return data
        ui = data.get("ui", {}) if isinstance(data.get("ui"), dict) else {}
        usage = data.get("usage", {}) if isinstance(data.get("usage"), dict) else {}
        files = data.get("files", []) if isinstance(data.get("files"), list) else []
        items = data.get("items", []) if isinstance(data.get("items"), list) else []
        source_images = []
        for raw in files:
            if not isinstance(raw, dict):
                continue
            source_images.append(
                {
                    "label": str(raw.get("label", "") or ""),
                    "path": str(raw.get("path", "") or ""),
                    "source_key": str(raw.get("label", "") or ""),
                    "reviewed": False,
                    "preview_markers": {},
                    "figure_boxes": [],
                    "ocr_exclusion_box": [],
                    "segments": [],
                }
            )
        new_items = []
        corrected_set = {int(v) for v in (data.get("corrected_items", []) or []) if str(v).isdigit()}
        for raw in items:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("item", "") or "")
            archivo = str(raw.get("archivo_origen", "") or "")
            image_paths = [str(v) for v in (raw.get("imagenes", []) or [])]
            marker_names = [
                str(match.group(1) or "").strip()
                for match in _IMAGE_TAG_RE.finditer(text)
                if str(match.group(1) or "").strip()
            ]
            binding_payload: Dict[str, Any] = {
                "marker_name": marker_names[0] if marker_names else "",
                "marker_names": marker_names,
                "segment_ids": [],
                "crop_paths": image_paths,
                "status": IMAGE_BINDING_STATUS_NONE,
                "origin": "",
                "needs_review": False,
            }
            if image_paths:
                binding_payload["status"] = IMAGE_BINDING_STATUS_MANUAL_CONFIRMED
                binding_payload["origin"] = "legacy_image_paths"
            elif marker_names:
                binding_payload["status"] = IMAGE_BINDING_STATUS_NEEDS_REVIEW
                binding_payload["origin"] = "legacy_marker_only"
                binding_payload["needs_review"] = True
            new_items.append(
                {
                    "archivo_origen": archivo,
                    "item_text": text,
                    "image_paths": image_paths,
                    "corrected": False,
                    "image_binding": binding_payload,
                }
            )
        return {
            "session_schema_version": 4,
            "project_name": str(ui.get("project_name", "Proyecto") or "Proyecto"),
            "ui_settings": ui,
            "source_images": source_images,
            "items": new_items,
            "output_text": str(data.get("output_text", "") or ""),
            "usage": {
                "input_tokens": int(usage.get("input_tokens", 0) or 0),
                "output_tokens": int(usage.get("output_tokens", 0) or 0),
                "total_tokens": int(usage.get("total_tokens", 0) or 0),
                "estimated_usd": float(usage.get("estimated_usd", 0.0) or 0.0),
            },
            "review": {
                "reviewed_sources": [str(v) for v in ((data.get("segmentation", {}) or {}).get("reviewed_sources", []) or [])],
                "review_done": bool(((data.get("segmentation", {}) or {}).get("review_done", False))),
                "route_active": False,
            },
            "logs": [],
            "metadata": {
                "legacy_payload": data,
                "corrected_items": sorted(corrected_set),
            },
        }
