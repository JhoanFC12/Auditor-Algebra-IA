from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.modulo0_transcriptor.scan_pipeline.prompts import (
    build_extract_prompt,
    build_faithful_ocr_prompt,
    build_parse_retry_prompt,
    build_structure_prompt,
)


class PromptRegressionTests(unittest.TestCase):
    def test_faithful_prompt_mentions_numbering_and_false_new_item_guards(self) -> None:
        prompt = build_faithful_ocr_prompt()
        self.assertIn("nunca <93.>, <103.> o <108.>", prompt)
        self.assertIn("no abras un problema nuevo", prompt.lower())
        self.assertIn("'<2.> x^3 + ... A)...'", prompt)

    def test_extract_prompt_mentions_spurious_93_and_ax_plus_b_case(self) -> None:
        prompt = build_extract_prompt(curso="Algebra", tema="Division", start_n=2)
        self.assertIn("nunca 93, 103 o 108", prompt)
        self.assertIn("(Ax + B), calcule AB", prompt)
        self.assertIn("bloque '<2.>' se trata como continuidad del 7", prompt)

    def test_structure_prompt_embeds_raw_ocr_and_conflict_rule(self) -> None:
        raw = "<1.> ...\n\n<93.> ..."
        prompt = build_structure_prompt(raw_ocr_text=raw, curso="Algebra", tema="Division", start_n=2)
        self.assertIn("<<<OCR_BRUTO_INICIO>>>", prompt)
        self.assertIn(raw, prompt)
        self.assertIn("evita saltos absurdos como 93 o 108", prompt)

    def test_parse_retry_prompt_keeps_prompt_regressions(self) -> None:
        prompt = build_parse_retry_prompt(
            raw_output="<7.> ...\n\n<2.> x^3 + ... A)...",
            errors=["salida_no_json"],
            curso="Algebra",
            tema="Division",
            start_n=7,
        )
        self.assertIn("Corrige especificamente errores de numeracion fantasma", prompt)
        self.assertIn("bloque '<2.>' se trata como continuidad del 7", prompt)
        self.assertIn("Errores detectados:", prompt)


if __name__ == "__main__":
    unittest.main()
