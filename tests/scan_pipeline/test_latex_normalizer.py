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
    normalize_scan_json_display_text,
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

    def test_existing_latex_accent_is_kept_canonical(self) -> None:
        out = normalize_plain_text_pdflatex(r"ecuaci\'on")
        self.assertEqual(out, r"ecuaci\'on")

    def test_double_escaped_accent_is_fixed(self) -> None:
        res = normalize_statement(r"De la siguiente ecuaci\\'on, halle x^1")
        self.assertIn(r"ecuaci\'on", res.text)
        self.assertIn(r"$x^1$", res.text)
        self.assertIn("accent_escape_canonicalized", res.warnings)

    def test_mojibake_accent_is_fixed(self) -> None:
        res = normalize_statement("De la siguiente ecuaciÃ³n")
        self.assertIn(r"ecuaci\'on", res.text)
        self.assertIn("accent_mojibake_fixed", res.warnings)

    def test_braced_latex_accent_is_canonicalized(self) -> None:
        out = normalize_plain_text_pdflatex(r"ecuaci\'{o}n")
        self.assertEqual(out, r"ecuaci\'on")

    def test_angle_word_is_restored_in_plain_prose(self) -> None:
        res = normalize_statement(r"Siendo S y C los valores conocidos para un mismo \angle, calcule")
        self.assertIn(r"un mismo \'angulo", res.text)
        self.assertIn("angle_word_restored", res.warnings)

    def test_article_angle_word_is_restored(self) -> None:
        res = normalize_statement(r"Halle el \angle formado por dos rectas")
        self.assertIn(r"el \'angulo formado", res.text)
        self.assertIn("angle_word_restored", res.warnings)

    def test_symbolic_angle_notation_is_preserved(self) -> None:
        res = normalize_statement(r"Halle \angle ABC")
        self.assertIn(r"\angle ABC", res.text)
        self.assertNotIn("angle_word_restored", res.warnings)

    def test_symbolic_m_angle_notation_is_preserved(self) -> None:
        res = normalize_statement(r"Calcule m\angle AOB")
        self.assertIn(r"\angle AOB", res.text)
        self.assertNotIn("angle_word_restored", res.warnings)

    def test_unicode_angle_symbol_stays_symbolic(self) -> None:
        res = normalize_statement("Halle ∠ABC")
        self.assertIn(r"\angle ABC", res.text)
        self.assertNotIn("angle_word_restored", res.warnings)

    def test_option_is_wrapped_math(self) -> None:
        out = normalize_option("30\u00b0")
        self.assertEqual(out.text, r"$30^\circ$")

    def test_scan_json_display_text_keeps_angle_word_readable(self) -> None:
        out = normalize_scan_json_display_text(r"Siendo S y C los valores conocidos para un mismo \angle, calcule la expresi\'on")
        self.assertIn("un mismo ángulo", out)
        self.assertIn("expresión", out)
        self.assertNotIn(r"\'angulo", out)

    def test_scan_json_display_text_unwraps_text_macro_in_prose(self) -> None:
        out = normalize_scan_json_display_text("De la medida del \\text{\u00e1ngulo} AOB")
        self.assertIn("De la medida del \u00e1ngulo AOB", out)
        self.assertNotIn(r"\text{", out)

    def test_scan_json_display_text_unwraps_text_macro_inside_word(self) -> None:
        out = normalize_scan_json_display_text("la expresi\\text{\u00f3n}")
        self.assertEqual(out, "la expresi\u00f3n")

    def test_scan_json_display_text_unwraps_text_macro_inside_math(self) -> None:
        out = normalize_scan_json_display_text(r"$\frac{\pi}{3} \text{rad}$")
        self.assertEqual(out, r"$\frac{\pi}{3} rad$")

    def test_scan_json_display_text_unwraps_textit_macro(self) -> None:
        out = normalize_scan_json_display_text(r"Halle R en el siguiente \textit{ángulo}")
        self.assertEqual(out, "Halle R en el siguiente ángulo")

    def test_scan_json_display_text_unwraps_textbf_macro(self) -> None:
        out = normalize_scan_json_display_text(r"Determine el \textbf{ángulo} pedido")
        self.assertEqual(out, "Determine el ángulo pedido")

    def test_scan_json_display_text_strips_left_right_wrappers(self) -> None:
        out = normalize_scan_json_display_text(r"$\left(4n-\frac{4}{3}\right)^\circ$")
        self.assertEqual(out, r"$(4n-\frac{4}{3})^\circ$")

    def test_normalize_statement_strips_textit_wrapper_in_prose(self) -> None:
        res = normalize_statement(r"Halle R en el siguiente \textit{ángulo}")
        self.assertIn(r"Halle R en el siguiente \'angulo", res.text)
        self.assertNotIn(r"\textit\{", res.text)

    def test_normalize_statement_strips_textit_wrapper_around_angle_word(self) -> None:
        res = normalize_statement(
            r"Si $S^\circ$ y $C^g$ son las medidas de un mismo \textit{ángulo}"
        )
        self.assertIn(r"un mismo \'angulo", res.text)
        self.assertNotIn(r"\textit\{", res.text)

    def test_normalize_statement_wraps_overline_command_in_prose(self) -> None:
        res = normalize_statement(r"Si \overline{OB} es bisectriz del angulo AOC, calcule x")
        self.assertIn(r"$\overline{OB}$", res.text)
        self.assertNotIn(r"\overline\{", res.text)

    def test_normalize_statement_wraps_frac_equation_in_prose(self) -> None:
        res = normalize_statement(
            r"De la siguiente ecuacion, halle \frac{x+1}{x-1}=\frac{1}{2}"
        )
        self.assertIn(r"$\frac{x+1}{x-1} = \frac{1}{2}$", res.text)
        self.assertNotIn(r"\frac\{", res.text)

    def test_normalize_statement_preserves_nested_fraction_expression(self) -> None:
        res = normalize_statement(
            r"De la siguiente ecuacion, halle x^1 \frac{x+1}{x-1} = \frac{5^8 + \frac{5^8}{3}}{2^8 - \frac{2^8}{3}}"
        )
        self.assertIn(r"$x^1$", res.text)
        self.assertIn(r"\frac{x+1}{x-1}", res.text)
        self.assertIn(r"\frac{5^8 + \frac{5^8}{3}}{2^8 - \frac{2^8}{3}}", res.text)
        self.assertNotIn(r"\textasciicircum{}", res.text)
        self.assertNotIn(r"\frac\{", res.text)

    def test_normalize_statement_wraps_sqrt_expression_in_prose(self) -> None:
        res = normalize_statement(
            r"Calcule Q = \sqrt{\frac{C+S}{C-S}} + \sqrt{\frac{4S}{C-S}}"
        )
        self.assertIn(r"Q = $", res.text)
        self.assertIn(r"\sqrt{", res.text)
        self.assertIn(r"\frac{C+S}{C-S}", res.text)
        self.assertIn(r"\frac{4S}{C-S}", res.text)
        self.assertNotIn(r"\sqrt\{", res.text)

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
