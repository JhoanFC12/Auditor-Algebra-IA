from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.prepare_local_ocr_lab_dataset import export_dataset


def _write_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 24), "white").save(path)


class LocalOcrLabDatasetTests(unittest.TestCase):
    def test_exports_corrected_golden_records_with_splits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden = root / "ocr_geometry_golden_live"
            image = golden / "images" / "sample.png"
            _write_png(image)
            records = golden / "records"
            records.mkdir(parents=True)
            (records / "abc.json").write_text(
                json.dumps(
                    {
                        "record_id": "abc",
                        "status": "corrected",
                        "copied_image_rel": "images/sample.png",
                        "corrected_text": "<01.> Halle $x$. A) 1 B) 2 C) 3 D) 4 E) 5",
                        "book_code": "geometria",
                        "instance_type": "semana_1",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            out = root / "out"
            manifest = export_dataset(out_dir=out, golden_dirs=[golden])

            self.assertEqual(manifest["total"], 1)
            rows = []
            for split in ("train", "validation", "test"):
                rows.extend(json.loads(line) for line in (out / f"{split}.jsonl").read_text(encoding="utf-8").splitlines() if line)
            self.assertEqual(rows[0]["text"], "<01.> Halle $x$. A) 1 B) 2 C) 3 D) 4 E) 5")
            self.assertTrue((out / rows[0]["image"]).exists())
            self.assertIn("OCR", rows[0]["prompt"])

    def test_skips_unreviewed_staging_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            _write_png(crop)
            staging = root / "staging" / "book__inst"
            records = staging / "records"
            records.mkdir(parents=True)
            (records / "r1.json").write_text(
                json.dumps(
                    {
                        "record_id": "r1",
                        "crop_id": "r1",
                        "crop_path": str(crop),
                        "raw_ocr": "<01.> Texto sin revisar",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            manifest = export_dataset(out_dir=root / "out", golden_dirs=[], staging_roots=[root / "staging"])

            self.assertEqual(manifest["total"], 0)
            skipped = (root / "out" / "skipped_records.jsonl").read_text(encoding="utf-8")
            self.assertIn("raw_ocr_not_human_reviewed", skipped)

    def test_exports_human_reviewed_staging_raw_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            crop = root / "crop.png"
            _write_png(crop)
            records = root / "staging" / "records"
            records.mkdir(parents=True)
            (records / "r1.json").write_text(
                json.dumps(
                    {
                        "record_id": "r1",
                        "crop_id": "r1",
                        "crop_path": str(crop),
                        "raw_ocr": "<01.> OCR corregido",
                        "trace": {"last_raw_ocr_review": {"source": "human_raw_ocr_editor"}},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            manifest = export_dataset(out_dir=root / "out", golden_dirs=[], staging_roots=[root / "staging"])

            self.assertEqual(manifest["total"], 1)


if __name__ == "__main__":
    unittest.main()
