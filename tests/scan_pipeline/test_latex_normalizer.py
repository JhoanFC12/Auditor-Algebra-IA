from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.modulo0_transcriptor.latex_normalizer import (
    normalize_option,
    normalize_plain_text_pdflatex,
    normalize_scan_item_text,
    normalize_statement,
)


class LatexNormalizerTests(unittest.TestCase):
    def test_degree_and_angle_to_latex(self) -> None:
        res = normalize_statement("Halla \u2220ABC = 30\u00b0")
        self.assertIn(r"\angle ABC", res.text)
        self.assertIn(r"30^\circ", res.text)
        self.assertNotIn("\u2220", res.text)
        self.assertNotIn("\u00b0", res.text)

    def test_greek_to_latex(self) -> None:
        res = normalize_statement("Si \u03b8 + \u03b1 = \u03c0")
        self.assertIn(r"\theta", res.text)
        self.assertIn(r"\alpha", res.text)
        self.assertIn(r"\pi", res.text)

    def test_spanish_macros_and_escape(self) -> None:
        out = normalize_plain_text_pdflatex("\u00bfQu\u00e9 es el \u00e1ngulo y #_ %?")
        self.assertIn(r"\textquestiondown{}", out)
        self.assertIn(r"\'e", out)
        self.assertIn(r"\'a", out)
        self.assertIn(r"\#", out)
        self.assertIn(r"\_", out)
        self.assertIn(r"\%", out)

    def test_option_is_wrapped_math(self) -> None:
        out = normalize_option("30\u00b0")
        self.assertEqual(out.text, r"$30^\circ$")

    def test_unbalanced_dollar_adds_warning(self) -> None:
        out = normalize_statement("x + $2 = 3")
        self.assertIn("unbalanced_math_delimiters", out.warnings)

    def test_scan_item_text_preserves_tags_and_avoids_double_dollars(self) -> None:
        raw = (
            r"\item[\textbf{3.}] [[curso=SIN_CURSO]] [[tema=SIN_TEMA]] "
            r"Si $\overline{OB}$ es bisectriz del $\angle AOC$ [[Imagen=img-3]]"
            "\u00a3A)$15\u00b0$\u00e6B)$27\u00b0$\u00e6C)$30\u00b0$\u00a3D)$18\u00b0$\u00e6\u00e6E)$25\u00b0$\u00a3"
        )
        out = normalize_scan_item_text(raw).text
        self.assertIn("[[curso=SIN_CURSO]]", out)
        self.assertIn("[[tema=SIN_TEMA]]", out)
        self.assertIn("[[Imagen=img-3]]", out)
        self.assertNotIn("[[$curso=", out)
        self.assertNotIn("$$", out)


if __name__ == "__main__":
    unittest.main()
