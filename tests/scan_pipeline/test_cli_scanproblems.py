from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.modulo0_transcriptor.scan_pipeline.pipeline import PipelineItemResult, PipelineRunResult
from modulos.modulo0_transcriptor.scan_pipeline.renderer import render_document
from modulos.modulo0_transcriptor.scan_pipeline.schema import ScanItem
from tools.scanproblems import run_scan


class CliScanProblemsTests(unittest.TestCase):
    def _fake_result(self, *, needs_review: bool) -> PipelineRunResult:
        item = ScanItem.from_dict(
            {
                "schema": "ScanItemJSON-v1",
                "n": 1,
                "curso": "Algebra",
                "tema": "Ecuaciones",
                "has_figure": False,
                "figure_tag": "",
                "statement": "Resuelve x+1=2",
                "options": {"A": "0", "B": "1", "C": "2", "D": "3", "E": "4"},
                "needs_review": needs_review,
            },
            default_n=1,
            curso="Algebra",
            tema="Ecuaciones",
        )
        rendered_doc = render_document([item])
        row = PipelineItemResult(item=item, rendered=rendered_doc.splitlines()[1], source="image1.png")
        return PipelineRunResult(
            items=[row],
            rendered_document=rendered_doc,
            skipped_images=[{"source": "clave.png", "reason": "keyword", "confidence": 0.9}],
            needs_review_count=1 if needs_review else 0,
            diagnostics=[],
            parse_failures=[],
            json_parse_failed_count=0,
        )

    def _base_args(self, *, out_file: Path, input_dir: Path, fail_on_needs_review: bool) -> argparse.Namespace:
        return argparse.Namespace(
            input=str(input_dir),
            start=1,
            curso="Algebra",
            tema="Ecuaciones",
            out=str(out_file),
            provider="ocr",
            model="",
            max_retries=0,
            parse_max_retries=0,
            timeout=30,
            temperature=0.0,
            top_p=1.0,
            max_tokens=3200,
            seed=42,
            strict_json=True,
            debug_dir="",
            fail_on_needs_review=fail_on_needs_review,
        )

    def test_cli_writes_output_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "in"
            input_dir.mkdir(parents=True, exist_ok=True)
            out_file = tmp_path / "problemas.tex"
            args = self._base_args(out_file=out_file, input_dir=input_dir, fail_on_needs_review=False)
            fake = self._fake_result(needs_review=False)
            with patch("tools.scanproblems.ScanPipeline") as mocked:
                mocked.return_value.run_on_folder.return_value = fake
                code = run_scan(args)
            self.assertEqual(code, 0)
            self.assertTrue(out_file.exists())
            report = Path(str(out_file) + ".report.json")
            self.assertTrue(report.exists())
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["items_total"], 1)
            self.assertEqual(payload["json_parse_failed_count"], 0)

    def test_cli_fail_on_needs_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            input_dir = tmp_path / "in"
            input_dir.mkdir(parents=True, exist_ok=True)
            out_file = tmp_path / "problemas.tex"
            args = self._base_args(out_file=out_file, input_dir=input_dir, fail_on_needs_review=True)
            fake = self._fake_result(needs_review=True)
            with patch("tools.scanproblems.ScanPipeline") as mocked:
                mocked.return_value.run_on_folder.return_value = fake
                code = run_scan(args)
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()