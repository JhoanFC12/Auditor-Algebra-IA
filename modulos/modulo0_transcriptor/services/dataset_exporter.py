from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from ..state import TranscriptorSessionState


@dataclass
class TrainingDatasetBundle:
    manifest: Dict[str, Any]
    text_rows: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ExportReport:
    output_dir: Path
    files_written: List[str] = field(default_factory=list)


class DatasetExporter:
    def build_training_dataset(self, state: TranscriptorSessionState) -> TrainingDatasetBundle:
        samples = [
            {
                "label": img.label,
                "path": img.path,
                "source_key": img.source_key,
                "reviewed": img.reviewed,
                "segment_detector_audit": dict(img.segment_detector_audit),
                "segments": [seg.to_dict() for seg in img.segments],
            }
            for img in state.source_images
        ]
        text_rows = [
            {
                "archivo_origen": item.archivo_origen,
                "item_text": item.item_text,
                "image_paths": list(item.image_paths),
                "corrected": bool(item.corrected),
            }
            for item in state.items
            if (item.item_text or "").strip()
        ]
        manifest = {
            "schema_version": 1,
            "dataset_kind": "transcriptor_training",
            "project_name": state.project_name,
            "source_count": len(samples),
            "item_count": len(text_rows),
            "samples": samples,
        }
        return TrainingDatasetBundle(manifest=manifest, text_rows=text_rows)

    def export_training_dataset(self, bundle: TrainingDatasetBundle, out_dir: Path) -> ExportReport:
        out_dir.mkdir(parents=True, exist_ok=True)
        files_written: List[str] = []
        manifest_path = out_dir / "manifest.json"
        manifest_path.write_text(json.dumps(bundle.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        files_written.append(str(manifest_path))
        pairs_path = out_dir / "pairs_texto.jsonl"
        with pairs_path.open("w", encoding="utf-8") as fh:
            for row in bundle.text_rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        files_written.append(str(pairs_path))
        return ExportReport(output_dir=out_dir, files_written=files_written)
