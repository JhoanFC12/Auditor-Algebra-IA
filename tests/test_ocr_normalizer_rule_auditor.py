from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from modulos.instance_factory.rule_auditor import audit_row, write_audit_report
from tools.audit_ocr_normalizer_rules import collect_rows


GOOD_FINAL = (
    "\\item[\\textbf{12.}] [[curso=Geometria]] [[tema=Angulos]] "
    "[[Estado=sin_revisar]] [[Clave=-]] Halle $m\\sphericalangle ABC=40^\\circ$ y $AB=12\\,cm$. "
    "\u00a3A) $4$\u00e6B) $5$\u00e6C) $6$\u00a3D) $7$\u00e6\u00e6E) $8$\u00a3"
)


class OcrNormalizerRuleAuditorTests(unittest.TestCase):
    def test_flags_geometry_ocr_rule_failures(self) -> None:
        row = {
            "record_id": "r1",
            "raw_ocr": "<12.> Halle \\angle ABC = 40° y AB=12,cm A)$4$ B)$5$ C)$6$ D)$7$ E)$8$",
        }

        audit = audit_row(row, mode="ocr")

        ocr = audit["audits"]["ocr"]
        self.assertFalse(ocr["eligible_for_training"])
        self.assertIn("angle_symbol", ocr["failed_rules"])
        self.assertIn("degree_format", ocr["failed_rules"])
        self.assertIn("unit_spacing", ocr["failed_rules"])
        self.assertIn("option_spacing", ocr["failed_rules"])

    def test_flags_unit_left_outside_math(self) -> None:
        row = {
            "record_id": "unit-outside",
            "raw_ocr": (
                r"<01.> Mide $4$\,u, $14$~u y $AB=9$ dm. "
                "\u00a3"
                r"A) $1$~u"
                "\u00e6"
                r"B) $2$ m"
                "\u00a3"
            ),
        }

        audit = audit_row(row, mode="ocr")

        ocr = audit["audits"]["ocr"]
        self.assertIn("unit_spacing", ocr["failed_rules"])
        violations = ocr["metrics"]["unit_spacing"]["violations"]
        self.assertIn("unit_outside_math", violations)
        self.assertIn("unit_outside_math_plain_space", violations)

    def test_accepts_clean_normalizer_final_format(self) -> None:
        row = {"record_id": "r2", "raw_ocr": "<12.> Halle...", "final_latex": GOOD_FINAL}

        audit = audit_row(row, mode="normalizer")

        normalizer = audit["audits"]["normalizer"]
        self.assertTrue(normalizer["eligible_for_training"])
        self.assertEqual(normalizer["failed_rules"], [])
        self.assertEqual(normalizer["metrics"]["final_format_valid"]["status"], "pass")
        self.assertEqual(normalizer["metrics"]["alternatives_complete"]["status"], "pass")

    def test_blocks_bad_final_format_and_duplicate_alternatives(self) -> None:
        row = {
            "record_id": "r3",
            "final_latex": (
                "\\item[\\textbf{12.}] [[curso=Geometria]] [[tema=Angulos]] [[Clave=-]] "
                "Halle x. A)$4$ A)$5$ B)$6$ C)$7$ D)$8$"
            ),
        }

        audit = audit_row(row, mode="normalizer")

        normalizer = audit["audits"]["normalizer"]
        self.assertFalse(normalizer["eligible_for_training"])
        self.assertIn("final_format_valid", normalizer["failed_rules"])
        self.assertIn("alternatives_complete", normalizer["failed_rules"])

    def test_accepts_image_from_fused_continuation(self) -> None:
        row = {
            "record_id": "r4",
            "final_latex": (
                "\\item[\\textbf{11.}] [[curso=Geometria]] [[tema=Triangulos]] [[Estado=sin_revisar]] "
                "[[Clave=-]] Calcular $x$. [[Imagen=img-11]] "
                "\u00a3A) $30^\circ$\u00e6B) $10^\circ$\u00e6C) $18^\circ$\u00a3D) $72^\circ$\u00e6\u00e6E) $36^\circ$\u00a3"
            ),
            "normalized": {
                "continuaciones_fusionadas": [
                    {"record_id": "cont", "has_figure": True, "segments_total": 1}
                ]
            },
        }

        audit = audit_row(row, mode="normalizer")

        normalizer = audit["audits"]["normalizer"]
        self.assertNotIn("hallucination_risk", normalizer["failed_rules"])

    def test_accepts_continuation_marker_as_non_final_normalizer_item(self) -> None:
        row = {"record_id": "cont-child", "final_latex": "[CONT.]"}

        audit = audit_row(row, mode="normalizer")

        normalizer = audit["audits"]["normalizer"]
        self.assertTrue(normalizer["eligible_for_training"])
        self.assertEqual(normalizer["failed_rules"], [])
        self.assertEqual(normalizer["metrics"]["continuation_marker"]["status"], "pass")
        self.assertEqual(normalizer["metrics"]["final_format_valid"]["status"], "not_applicable")
        self.assertEqual(normalizer["metrics"]["alternatives_complete"]["status"], "not_applicable")

    def test_segment_rule_does_not_flag_natural_segmento_de_recta(self) -> None:
        row = {
            "record_id": "segment-text",
            "final_latex": (
                "\\item[\\textbf{112.}] [[curso=Geometria]] [[tema=Areas]] [[Estado=sin_revisar]] "
                "[[Clave=-]] El segmento de recta que une los puntos medios de las bases. "
                "\u00a3A) VFV\u00e6B) VVF\u00e6C) FFV\u00a3D) VVV\u00e6\u00e6E) FVV\u00a3"
            ),
        }

        audit = audit_row(row, mode="normalizer")

        normalizer = audit["audits"]["normalizer"]
        self.assertNotIn("segment_vs_length", normalizer["failed_rules"])

    def test_segment_rule_flags_unmarked_segment_object(self) -> None:
        row = {
            "record_id": "segment-object",
            "raw_ocr": "<01.> En el segmento AB se ubica P.",
        }

        audit = audit_row(row, mode="ocr")

        ocr = audit["audits"]["ocr"]
        self.assertIn("segment_vs_length", ocr["failed_rules"])

    def test_writes_summary_records_and_eligible_samples(self) -> None:
        rows = [
            {"record_id": "ok", "final_latex": GOOD_FINAL},
            {"record_id": "bad", "raw_ocr": "<01.> A)$4$"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "audit"

            summary = write_audit_report(rows, out_dir=out, mode="auto")

            self.assertEqual(summary["records_total"], 2)
            self.assertTrue((out / "summary.json").exists())
            self.assertTrue((out / "records.jsonl").exists())
            eligible = [
                json.loads(line)
                for line in (out / "eligible_samples.jsonl").read_text(encoding="utf-8").splitlines()
                if line
            ]
            self.assertEqual([row["sample"]["record_id"] for row in eligible], ["ok"])

    def test_collects_jsonl_inputs_and_staging_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "inputs.jsonl"
            source.write_text(json.dumps({"record_id": "jsonl", "raw_ocr": "<01.> Texto"}) + "\n", encoding="utf-8")
            records = root / "staging" / "records"
            records.mkdir(parents=True)
            (records / "r1.json").write_text(json.dumps({"record_id": "staging", "raw_ocr": "<02.> Texto"}), encoding="utf-8")

            rows = collect_rows(input_paths=[source], staging_roots=[root / "staging"])

            self.assertEqual([row["record_id"] for row in rows], ["jsonl", "staging"])


if __name__ == "__main__":
    unittest.main()
