from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from tools.prepare_problem_detector_reviewed_dataset import build_dataset, iter_dataset_roots


class PrepareProblemDetectorReviewedDatasetTests(unittest.TestCase):
    @staticmethod
    def _write_sample(root: Path, sample_id: str) -> None:
        for name in ("images", "labels", "metadata"):
            (root / name).mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 80), "white").save(root / "images" / f"{sample_id}.png")
        (root / "labels" / f"{sample_id}.txt").write_text(
            "0 0.5 0.5 0.8 0.8\n1 0.2 0.2 0.1 0.1\n2 0.5 0.7 0.5 0.2\n",
            encoding="utf-8",
        )
        (root / "metadata" / f"{sample_id}.json").write_text(
            json.dumps(
                {
                    "schema_version": "problem_detector_correction_v1",
                    "correction_id": sample_id,
                    "forced_training_capture": True,
                }
            ),
            encoding="utf-8",
        )

    def test_discovers_per_instance_correction_datasets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "problem_detector_corrections_live"
            self._write_sample(root / "instancia_1", "page_001")
            self._write_sample(root / "instancia_2", "page_002")

            discovered = iter_dataset_roots(root)
            self.assertEqual(len(discovered), 2)

            output = build_dataset(source_roots=[root], out_root=Path(tmp), val_ratio=0.2, seed=42)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["samples_total"], 2)
            classes = {
                key: manifest["splits"]["train"]["classes"].get(key, 0)
                + manifest["splits"]["val"]["classes"].get(key, 0)
                for key in ("problem", "problem_number", "answer_block")
            }
            self.assertEqual(classes, {"problem": 2, "problem_number": 2, "answer_block": 2})

    def test_accepts_an_already_consolidated_split_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "previous_dataset"
            sample_root = root / "source"
            self._write_sample(sample_root, "page_001")
            for kind, suffix in (("images", ".png"), ("labels", ".txt"), ("metadata", ".json")):
                target = root / kind / "train"
                target.mkdir(parents=True, exist_ok=True)
                (sample_root / kind / f"page_001{suffix}").replace(target / f"hashed_page{suffix}")

            output = build_dataset(source_roots=[root], out_root=Path(tmp), val_ratio=0.2, seed=42)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["samples_total"], 1)


if __name__ == "__main__":
    unittest.main()
