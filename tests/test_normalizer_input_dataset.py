from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from modulos.instance_factory.models import PipelineStep, StageStatus
from tools.prepare_normalizer_input_dataset import SCHEMA_VERSION, export_normalizer_inputs


def _write_record(records_dir: Path, name: str, payload: dict) -> None:
    records_dir.mkdir(parents=True, exist_ok=True)
    (records_dir / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _base_record(crop: Path, *, record_id: str = "r1", page: int = 1, box: int = 1) -> dict:
    return {
        "record_id": record_id,
        "crop_id": record_id,
        "crop_path": str(crop),
        "status": StageStatus.NEEDS_REVIEW,
        "raw_ocr": f"<{box:02d}.> Texto",
        "structured_ocr": {},
        "figure_segmentation": {"segments_total": 0, "segments": []},
        "source": {
            "book_code": "ALG01",
            "instance_type": "semana_1",
            "pdf_path": "E:/Banco/libro.pdf",
            "page_number": page,
            "source_order": box,
            "box_index": box,
            "bbox_px": [10, 20 * box, 80, 20 * box + 15],
            "crop_id": record_id,
        },
        "models": {"stages": {"ocr": {"model_id": "test"}}},
        "confidence": {"ocr_quality_proxy": 1.0},
        "steps": {
            PipelineStep.CROPS: {"status": StageStatus.READY},
            PipelineStep.OCR: {"status": StageStatus.READY},
            PipelineStep.SEGMENTATION: {"status": StageStatus.READY},
        },
        "errors": [],
    }


class NormalizerInputDatasetTests(unittest.TestCase):
    def test_exports_valid_staging_records_in_page_and_box_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            records = staging / "records"
            crop_a = root / "a.png"
            crop_b = root / "b.png"
            crop_a.write_bytes(b"png")
            crop_b.write_bytes(b"png")
            _write_record(records, "z_last", _base_record(crop_b, record_id="z_last", page=2, box=1))
            _write_record(records, "a_first", _base_record(crop_a, record_id="a_first", page=1, box=1))

            result = export_normalizer_inputs(staging_roots=[staging], out_dir=root / "out")

            self.assertEqual(result.manifest["total"], 2)
            self.assertEqual(result.manifest["skipped_total"], 0)
            self.assertEqual([row["record_id"] for row in result.rows], ["a_first", "z_last"])
            self.assertEqual(result.rows[0]["schema_version"], SCHEMA_VERSION)
            self.assertEqual(result.rows[0]["raw_ocr"], "<01.> Texto")
            self.assertEqual(result.rows[0]["structured_ocr"], {})
            self.assertEqual(result.rows[0]["source"]["page_number"], 1)
            written = [
                json.loads(line)
                for line in (root / "out" / "inputs.jsonl").read_text(encoding="utf-8").splitlines()
                if line
            ]
            self.assertEqual([row["record_id"] for row in written], ["a_first", "z_last"])
            self.assertTrue((root / "out" / "manifest.json").exists())

    def test_skips_records_with_missing_crop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = root / "staging" / "records"
            missing_crop = root / "missing.png"
            _write_record(records, "r1", _base_record(missing_crop))

            result = export_normalizer_inputs(staging_roots=[root / "staging"], out_dir=root / "out")

            self.assertEqual(result.manifest["total"], 0)
            self.assertEqual(result.skipped[0]["reason"], "missing_crop")
            self.assertIn("missing_crop", (root / "out" / "skipped.jsonl").read_text(encoding="utf-8"))

    def test_skips_stale_or_invalidated_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            records = root / "staging" / "records"
            payload = _base_record(crop)
            payload["audit"] = {"downstream_state": {"status": "invalidated", "reason": "page_boxes_changed"}}
            _write_record(records, "r1", payload)

            result = export_normalizer_inputs(staging_roots=[root / "staging"], out_dir=root / "out")

            self.assertEqual(result.manifest["total"], 0)
            self.assertEqual(result.skipped[0]["reason"], "source_stale")

    def test_skips_records_without_page_box_crop_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            records = root / "staging" / "records"
            payload = _base_record(crop)
            payload["source"].pop("bbox_px")
            _write_record(records, "r1", payload)

            result = export_normalizer_inputs(staging_roots=[root / "staging"], out_dir=root / "out")

            self.assertEqual(result.manifest["total"], 0)
            self.assertEqual(result.skipped[0]["reason"], "metadata_incomplete")
            self.assertIn("invalid:source.bbox_px", result.skipped[0]["issues"])

    def test_requires_raw_ocr_even_if_legacy_structured_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            records = root / "staging" / "records"
            payload = _base_record(crop)
            payload["raw_ocr"] = ""
            payload["structured_ocr"] = {"items_total": 1, "items": [{"item": {"n": 1}}]}
            _write_record(records, "r1", payload)

            result = export_normalizer_inputs(staging_roots=[root / "staging"], out_dir=root / "out")

            self.assertEqual(result.manifest["total"], 0)
            self.assertEqual(result.skipped[0]["reason"], "missing_raw_ocr")

    def test_exports_graph_segmentation_as_normalizer_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            records = root / "staging" / "records"
            payload = _base_record(crop)
            payload["figure_segmentation"] = {
                "segments_total": 1,
                "segments": [{"idx": 1, "bbox_px": [10, 20, 100, 120], "image_path": "seg_01.png"}],
            }
            _write_record(records, "r1", payload)

            result = export_normalizer_inputs(staging_roots=[root / "staging"], out_dir=root / "out")

            self.assertEqual(result.manifest["total"], 1)
            self.assertEqual(result.rows[0]["figure_segmentation"]["segments_total"], 1)
            self.assertEqual(result.rows[0]["figure_segmentation"]["segments"][0]["bbox_px"], [10, 20, 100, 120])

    def test_exports_from_parent_staging_root_with_multiple_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            crop.write_bytes(b"png")
            records = root / "staging" / "book__inst" / "records"
            _write_record(records, "r1", _base_record(crop))

            result = export_normalizer_inputs(staging_roots=[root / "staging"], out_dir=root / "out")

            self.assertEqual(result.manifest["total"], 1)
            self.assertEqual(result.rows[0]["record_id"], "r1")


if __name__ == "__main__":
    unittest.main()
