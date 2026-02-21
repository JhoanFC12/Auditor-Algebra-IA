from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.modulo0_transcriptor.scan_pipeline.extractor import ScanExtractor
from modulos.modulo0_transcriptor.scan_pipeline.pipeline import ScanPipeline


class StrictJsonPipelineTests(unittest.TestCase):
    def test_extractor_strict_json_repairs_invalid_escape_sequences(self) -> None:
        extractor = ScanExtractor(provider="hf", strict_json=True)
        raw = (
            "```json\n"
            "{\n"
            '  "items": [\n'
            "    {\n"
            '      "schema": "ScanItemJSON-v1",\n'
            '      "n": 1,\n'
            '      "curso": "SIN_CURSO",\n'
            '      "tema": "SIN_TEMA",\n'
            '      "has_figure": true,\n'
            '      "figure_tag": "img-1",\n'
            '      "statement": "Determine gr\\\'afico si \\theta = 30",\n'
            '      "options": {"A":"1","B":"2","C":"3","D":"4","E":"5"},\n'
            '      "needs_review": false\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "```"
        )
        parsed = extractor.parse_raw_output(
            raw_output=raw,
            curso="SIN_CURSO",
            tema="SIN_TEMA",
            start_n=1,
        )
        self.assertEqual(len(parsed), 1)
        self.assertIn("statement", parsed[0])

    def test_extractor_strict_json_rejects_text_fallback_for_hf(self) -> None:
        extractor = ScanExtractor(provider="hf", strict_json=True)
        text_item = r"\item[\textbf{1.}] [[curso=A]] [[tema=B]] Enunciado £A)$1$æB)$2$æC)$3$£D)$4$ææE)$5$£"
        parsed = extractor.parse_raw_output(
            raw_output=text_item,
            curso="A",
            tema="B",
            start_n=1,
        )
        self.assertEqual(parsed, [])

    def test_extractor_ocr_keeps_text_fallback(self) -> None:
        extractor = ScanExtractor(provider="ocr", strict_json=True)
        text_item = r"\item[\textbf{1.}] [[curso=A]] [[tema=B]] Enunciado £A)$1$æB)$2$æC)$3$£D)$4$ææE)$5$£"
        parsed = extractor.parse_raw_output(
            raw_output=text_item,
            curso="A",
            tema="B",
            start_n=1,
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].get("n"), 1)

    def test_process_raw_output_marks_needs_review_after_parse_failures(self) -> None:
        pipeline = ScanPipeline(
            provider="hf",
            max_retries=0,
            parse_max_retries=1,
            strict_json=True,
        )

        def _repair_fail(**kwargs):
            return ([], "still_not_json")

        pipeline.extractor.repair_raw_output = _repair_fail  # type: ignore[assignment]

        result = pipeline.process_raw_output(
            raw_output="NO JSON RESPONSE",
            image_path=Path("problem_1.png"),
            start_n=1,
            curso="Algebra",
            tema="Ecuaciones",
            has_figure_hint=False,
        )

        self.assertEqual(result.json_parse_failed_count, 1)
        self.assertEqual(len(result.parse_failures), 1)
        self.assertEqual(len(result.items), 1)
        self.assertTrue(result.items[0].item.needs_review)

    def test_process_raw_output_parse_retry_recovers_item(self) -> None:
        pipeline = ScanPipeline(
            provider="hf",
            max_retries=0,
            parse_max_retries=1,
            strict_json=True,
        )

        def _repair_ok(**kwargs):
            repaired = {
                "schema": "ScanItemJSON-v1",
                "n": 9,
                "curso": "Algebra",
                "tema": "Ecuaciones",
                "has_figure": False,
                "figure_tag": "",
                "statement": "Resuelve x+1=2",
                "options": {"A": "0", "B": "1", "C": "2", "D": "3", "E": "4"},
                "needs_review": False,
            }
            return ([repaired], '{"items":[{"schema":"ScanItemJSON-v1"}]}')

        pipeline.extractor.repair_raw_output = _repair_ok  # type: ignore[assignment]

        result = pipeline.process_raw_output(
            raw_output="NO JSON RESPONSE",
            image_path=Path("problem_2.png"),
            start_n=1,
            curso="Algebra",
            tema="Ecuaciones",
            has_figure_hint=False,
        )

        self.assertEqual(result.json_parse_failed_count, 0)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].item.n, 9)
        self.assertFalse(result.items[0].item.needs_review)

    def test_pipeline_forces_figure_tag_img_n(self) -> None:
        pipeline = ScanPipeline(
            provider="hf",
            max_retries=0,
            parse_max_retries=1,
            strict_json=True,
        )

        def _repair_ok(**kwargs):
            repaired = {
                "schema": "ScanItemJSON-v1",
                "n": 4,
                "curso": "Geo",
                "tema": "Angulos",
                "has_figure": True,
                "figure_tag": "img-1",
                "statement": "Halle x",
                "options": {"A": "0", "B": "1", "C": "2", "D": "3", "E": "4"},
                "needs_review": False,
            }
            return ([repaired], '{"items":[{"schema":"ScanItemJSON-v1"}]}')

        pipeline.extractor.repair_raw_output = _repair_ok  # type: ignore[assignment]

        result = pipeline.process_raw_output(
            raw_output="NO JSON RESPONSE",
            image_path=Path("problem_4.png"),
            start_n=4,
            curso="Geo",
            tema="Angulos",
            has_figure_hint=False,
        )

        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].item.figure_tag, "img-4")


if __name__ == "__main__":
    unittest.main()
