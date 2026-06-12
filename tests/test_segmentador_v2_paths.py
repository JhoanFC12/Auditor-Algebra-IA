from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from modulos.modulo0_transcriptor.segmentador_v2 import SegmentadorProblemasV2


class SegmentadorV2PathTests(unittest.TestCase):
    def test_preserves_short_source_stem_for_legacy_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "segments"
            src = Path(tmp) / "crop_001.png"
            segmenter = SegmentadorProblemasV2(root)

            segmenter.persist_segments_manifest(src=src, segments=[])

            manifest = root / "crop_001" / "segments_manifest.json"
            self.assertTrue(manifest.exists())
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_stem"], "crop_001")
            self.assertEqual(payload["source_dir"], "crop_001")

    def test_compacts_long_source_stem_for_windows_safe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "segments"
            long_stem = "aseuni-semianual-geometria__semana_2_dc9b1f016c____ASEUNI_SEM_" + ("x" * 140)
            src = Path(tmp) / f"{long_stem}.png"
            segmenter = SegmentadorProblemasV2(root)

            segmenter.persist_segments_manifest(src=src, segments=[])

            dirs = [path for path in root.iterdir() if path.is_dir()]
            self.assertEqual(len(dirs), 1)
            self.assertNotEqual(dirs[0].name, long_stem)
            self.assertLessEqual(len(dirs[0].name), 32)
            manifest = dirs[0] / "segments_manifest.json"
            self.assertTrue(manifest.exists())
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(payload["source_stem"], long_stem)
            self.assertEqual(payload["source_dir"], dirs[0].name)


if __name__ == "__main__":
    unittest.main()
