from __future__ import annotations

import unittest

from modulos.semantic_profile_seed import build_problem_semantic_seed, parse_final_problem_latex
from tools.validate_semantic_descriptor_contracts import load_schemas, validate_payload


class SemanticProfileSeedTests(unittest.TestCase):
    def test_parse_final_problem_latex_extracts_tags_and_options(self) -> None:
        final_latex = (
            r"\item[\textbf{22.}] [[curso=Geometria]] [[tema=Triangulos]] "
            r"[[Estado=sin_revisar]] [[Clave=B]] Calcular $x$. [[Imagen=img-22]] "
            "£A)$10^\\circ$æB)$20^\\circ$æC)$30^\\circ$£D)$40^\\circ$ææE)$50^\\circ$£"
        )

        parsed = parse_final_problem_latex(final_latex)

        self.assertEqual(parsed["number"], "22")
        self.assertEqual(parsed["course"], "Geometria")
        self.assertEqual(parsed["topic"], "Triangulos")
        self.assertEqual(parsed["answer"], "B")
        self.assertEqual(parsed["image_tags"], ["img-22"])
        self.assertIn("Calcular x", parsed["statement"])
        self.assertEqual(set(parsed["options"]), {"A", "B", "C", "D", "E"})

    def test_build_problem_semantic_seed_matches_contract(self) -> None:
        final_latex = (
            r"\item[\textbf{22.}] [[curso=Geometria]] [[tema=Triangulos]] "
            r"[[Estado=sin_revisar]] [[Clave=B]] Calcular $x$. [[Imagen=img-22]] "
            "£A)$10^\\circ$æB)$20^\\circ$æC)$30^\\circ$£D)$40^\\circ$ææE)$50^\\circ$£"
        )
        profile = build_problem_semantic_seed(
            problem_id=22,
            final_latex=final_latex,
            raw_ocr="<22.> Calcular x. A) 10 B) 20 C) 30 D) 40 E) 50",
        )

        self.assertEqual(profile["schema_version"], "problem_semantic_profile_v1")
        self.assertEqual(profile["problem_id"], "22")
        self.assertEqual(profile["modality"], "text_image")
        self.assertTrue(profile["evidence"]["uses_figure"])
        self.assertIn("triangulo", profile["objects"]["geometry"])
        schemas, errors = load_schemas()
        self.assertEqual(errors, [])
        issue = validate_payload(__file__, profile, schemas)
        self.assertTrue(issue.valid, issue.errors)

    def test_build_problem_semantic_seed_keeps_unknown_classification_pending(self) -> None:
        final_latex = r"\item[\textbf{1.}] Calcular $n$. £A)$1$æB)$2$æC)$3$£D)$4$ææE)$5$£"

        profile = build_problem_semantic_seed(problem_id="p1", final_latex=final_latex)

        self.assertEqual(profile["course"], "SIN_CURSO")
        self.assertEqual(profile["topic"], "SIN_TEMA")
        self.assertEqual(profile["concepts"], [])
        self.assertEqual(profile["review"]["status"], "sin_revisar")


if __name__ == "__main__":
    unittest.main()
