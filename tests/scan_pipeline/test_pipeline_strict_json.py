from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modulos.modulo0_transcriptor.scan_pipeline.extractor import (
    ScanExtractor,
    _normalize_structured_payload,
    canonicalize_faithful_ocr_text,
)
from modulos.modulo0_transcriptor.scan_pipeline.image_role import (
    ImageEvidence,
    collect_new_item_numbers,
    extract_numbered_headers,
    resolve_image_role,
)
from modulos.modulo0_transcriptor.scan_pipeline.ocr_ensemble import (
    OCRCandidateAnalysis,
    analyze_ocr_candidate,
    is_ocr_candidate_recoverable,
    is_ocr_candidate_valid,
    renumber_items_continuously,
    resolve_hf_ocr_ensemble_models,
    select_best_ocr_candidate,
    should_accept_ocr_candidate_fast,
    should_continue_numbering_for_items,
)
from modulos.modulo0_transcriptor.scan_pipeline.pipeline import ScanPipeline
from modulos.modulo0_transcriptor.scan_pipeline.schema import ScanItem
from modulos.modulo0_transcriptor.scan_pipeline.statement_cleanup import merge_statement_fragments


class StrictJsonPipelineTests(unittest.TestCase):
    def test_vision_chat_retries_with_context_safe_max_tokens(self) -> None:
        extractor = ScanExtractor(provider="hf", model="demo", max_tokens=900, strict_json=False)
        extractor._get_hf_client = lambda _model="": object()  # type: ignore[assignment]
        extractor._encode_image = lambda *_args, **_kwargs: "data:image/jpeg;base64,AA=="  # type: ignore[assignment]
        calls = []

        def _call(_client: object, payload: dict) -> str:
            calls.append(dict(payload))
            if len(calls) == 1:
                raise RuntimeError(
                    "'max_tokens' or 'max_completion_tokens' is too large: 900. "
                    "This model's maximum context length is 4096 tokens and your request has "
                    "4045 input tokens (900 > 4096 - 4045)."
                )
            return "<1.> Halle x."

        extractor._call_vision_chat = _call  # type: ignore[assignment]
        raw = extractor._vision_chat(
            prompt="Transcribe.",
            image_path=Path("dummy.png"),
            strict_json=False,
            allow_context_token_retry=True,
        )
        self.assertEqual(raw, "<1.> Halle x.")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["max_tokens"], 43)

    def test_extract_raw_from_image_retries_by_reducing_image_only(self) -> None:
        extractor = ScanExtractor(provider="hf", model="demo", max_tokens=900, strict_json=False)
        calls = []

        def _vision_chat(**kwargs: object) -> str:
            calls.append(dict(kwargs))
            if len(calls) == 1:
                raise RuntimeError(
                    "'max_tokens' or 'max_completion_tokens' is too large: 900. "
                    "This model's maximum context length is 4096 tokens and your request has "
                    "4045 input tokens (900 > 4096 - 4045)."
                )
            return "<1.> Halle x.\nA) $1$\nB) $2$"

        def _no_structure(**_kwargs: object) -> tuple[list[dict], str]:
            raise AssertionError("extract_raw_from_image must not call structure_raw_output")

        extractor._vision_chat = _vision_chat  # type: ignore[assignment]
        extractor.structure_raw_output = _no_structure  # type: ignore[assignment]

        items, raw = extractor.extract_raw_from_image(
            image_path=Path("dummy.png"),
            curso="SIN_CURSO",
            tema="SIN_TEMA",
            start_n=1,
        )

        self.assertIn("<1.>", raw)
        self.assertTrue(items)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["prompt"], calls[1]["prompt"])
        self.assertEqual(calls[0]["system_prompt"], calls[1]["system_prompt"])
        self.assertNotIn("max_tokens_override", calls[1])
        self.assertEqual(int(calls[1]["image_max_side_px"]), 384)

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
        self.assertIn("gráfico", parsed[0]["statement"])

    def test_extractor_strict_json_humanizes_angle_word_in_visible_json(self) -> None:
        extractor = ScanExtractor(provider="hf", strict_json=True)
        raw = (
            "```json\n"
            "{\n"
            '  "items": [\n'
            "    {\n"
            '      "schema": "ScanItemJSON-v1",\n'
            '      "n": 6,\n'
            '      "curso": "SIN_CURSO",\n'
            '      "tema": "SIN_TEMA",\n'
            '      "has_figure": false,\n'
            '      "figure_tag": "",\n'
            '      "statement": "Siendo S y C los valores conocidos para un mismo \\\\angle, calcule la expresi\\\\\'on",\n'
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
            start_n=6,
        )
        self.assertEqual(len(parsed), 1)
        self.assertIn("mismo ángulo", parsed[0]["statement"])
        self.assertIn("expresión", parsed[0]["statement"])

    def test_extractor_strict_json_unwraps_text_macros_in_visible_json(self) -> None:
        extractor = ScanExtractor(provider="hf", strict_json=True)
        raw = (
            "```json\n"
            "{\n"
            '  "items": [\n'
            "    {\n"
            '      "schema": "ScanItemJSON-v1",\n'
            '      "n": 4,\n'
            '      "curso": "SIN_CURSO",\n'
            '      "tema": "SIN_TEMA",\n'
            '      "has_figure": false,\n'
            '      "figure_tag": "",\n'
            '      "statement": "De la medida del \\\\text{\u00e1ngulo} AOB y la expresi\\\\text{\u00f3n}: $Q=1$",\n'
            '      "options": {"A":"\\\\frac{\\\\pi}{3} \\\\text{rad}","B":"2","C":"3","D":"4","E":"5"},\n'
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
            start_n=4,
        )
        self.assertEqual(len(parsed), 1)
        self.assertIn("medida del \u00e1ngulo AOB", parsed[0]["statement"])
        self.assertIn("expresi\u00f3n", parsed[0]["statement"])
        self.assertNotIn(r"\text{", parsed[0]["statement"])
        self.assertEqual(parsed[0]["options"]["A"], r"\frac{\pi}{3} rad")

    def test_extractor_strict_json_humanizes_visible_json_but_keeps_basic_math(self) -> None:
        extractor = ScanExtractor(provider="hf", strict_json=True)
        raw = (
            "```json\n"
            "{\n"
            '  "items": [\n'
            "    {\n"
            '      "schema": "ScanItemJSON-v1",\n'
            '      "n": 27,\n'
            '      "curso": "SIN_CURSO",\n'
            '      "tema": "SIN_TEMA",\n'
            '      "has_figure": false,\n'
            '      "figure_tag": "",\n'
            '      "statement": "Si $100,405^x=a^8b^mc^5$ y el \\\\textit{\u00e1ngulo} $\\\\theta$ mide $\\\\left(4n-\\\\frac{4}{3}\\\\right)^\\\\circ$",\n'
            '      "options": {"A":"$\\\\frac{\\\\pi}{30} \\\\text{rad}$","B":"2","C":"3","D":"4","E":"5"},\n'
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
            start_n=27,
        )
        self.assertEqual(len(parsed), 1)
        statement = parsed[0]["statement"]
        self.assertIn("el ángulo", statement)
        self.assertIn(r"$\theta$", statement)
        self.assertIn(r"$(4n-\frac{4}{3})^\circ$", statement)
        self.assertNotIn(r"\textit{", statement)
        self.assertNotIn(r"\left", statement)
        self.assertNotIn(r"\right", statement)
        self.assertEqual(parsed[0]["options"]["A"], r"$\frac{\pi}{30} rad$")

    def test_extractor_strict_json_preserves_beta_command(self) -> None:
        extractor = ScanExtractor(provider="hf", strict_json=True)
        raw = (
            "{"
            '"items":['
            "{"
            '"schema":"ScanItemJSON-v1",'
            '"n":24,'
            '"curso":"SIN_CURSO",'
            '"tema":"SIN_TEMA",'
            '"has_figure":false,'
            '"figure_tag":"",'
            '"statement":"Las medidas de \\\\alpha y \\beta son",'
            '"options":{"A":"1","B":"2","C":"3","D":"4","E":"5"},'
            '"needs_review":false'
            "}"
            "]"
            "}"
        )
        parsed = extractor.parse_raw_output(
            raw_output=raw,
            curso="SIN_CURSO",
            tema="SIN_TEMA",
            start_n=24,
        )
        self.assertEqual(len(parsed), 1)
        self.assertIn(r"\beta", parsed[0]["statement"])
        self.assertNotIn("\b", parsed[0]["statement"])

    def test_extractor_strict_json_preserves_frac_and_right_commands(self) -> None:
        extractor = ScanExtractor(provider="hf", strict_json=True)
        raw = (
            "{"
            '"items":['
            "{"
            '"schema":"ScanItemJSON-v1",'
            '"n":24,'
            '"curso":"SIN_CURSO",'
            '"tema":"SIN_TEMA",'
            '"has_figure":false,'
            '"figure_tag":"",'
            '"statement":"$\\\\left(4n-\\frac{4}{3}\\right)^\\\\circ$ y \\beta",'
            '"options":{"A":"1","B":"2","C":"3","D":"4","E":"5"},'
            '"needs_review":false'
            "}"
            "]"
            "}"
        )
        parsed = extractor.parse_raw_output(
            raw_output=raw,
            curso="SIN_CURSO",
            tema="SIN_TEMA",
            start_n=24,
        )
        self.assertEqual(len(parsed), 1)
        statement = parsed[0]["statement"]
        self.assertIn(r"\frac{4}{3}", statement)
        self.assertIn(r"$(4n-\frac{4}{3})^\circ$", statement)
        self.assertIn(r"\beta", statement)
        self.assertNotIn(r"\left", statement)
        self.assertNotIn(r"\right", statement)
        self.assertNotIn("\f", statement)
        self.assertNotIn("\r", statement)

    def test_extractor_strict_json_keeps_real_newline_escape(self) -> None:
        extractor = ScanExtractor(provider="hf", strict_json=True)
        raw = (
            "{"
            '"items":['
            "{"
            '"schema":"ScanItemJSON-v1",'
            '"n":10,'
            '"curso":"SIN_CURSO",'
            '"tema":"SIN_TEMA",'
            '"has_figure":false,'
            '"figure_tag":"",'
            '"statement":"Linea 1\\nLinea 2",'
            '"options":{"A":"1","B":"2","C":"3","D":"4","E":"5"},'
            '"needs_review":false'
            "}"
            "]"
            "}"
        )
        parsed = extractor.parse_raw_output(
            raw_output=raw,
            curso="SIN_CURSO",
            tema="SIN_TEMA",
            start_n=10,
        )
        self.assertEqual(len(parsed), 1)
        self.assertIn("Linea 1", parsed[0]["statement"])
        self.assertIn("Linea 2", parsed[0]["statement"])

    def test_extractor_strict_json_keeps_unicode_escape_sequences(self) -> None:
        extractor = ScanExtractor(provider="hf", strict_json=True)
        raw = (
            "{"
            '"items":['
            "{"
            '"schema":"ScanItemJSON-v1",'
            '"n":11,'
            '"curso":"SIN_CURSO",'
            '"tema":"SIN_TEMA",'
            '"has_figure":false,'
            '"figure_tag":"",'
            '"statement":"Marca \\u00a3 interna",'
            '"options":{"A":"1","B":"2","C":"3","D":"4","E":"5"},'
            '"needs_review":false'
            "}"
            "]"
            "}"
        )
        parsed = extractor.parse_raw_output(
            raw_output=raw,
            curso="SIN_CURSO",
            tema="SIN_TEMA",
            start_n=11,
        )
        self.assertEqual(len(parsed), 1)
        self.assertIn("Marca", parsed[0]["statement"])

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

    def test_local_structured_output_splits_inline_continuation_plus_new_item(self) -> None:
        extractor = ScanExtractor(provider="hf", strict_json=True)
        raw = (
            "C) {2kπ + π/4} D) {2kπ + π/3} E) {2kπ + 2π/3} "
            "25. Sea f la función definida por: "
            "f(x) = a sen(bx+c)+d y g la función definida por: g(x)=acos(bx+c)+d. "
            "Determine todos los valores de x para las cuales las gráficas de f y g se intersecan. Si k ∈ Z "
            "A) (2k+1)π - 2c / 4b B) (4k+1)π - 4c / 4b "
            "C) (4k+1)π - 4c / 2b D) (2k+1)π - 4c / 2b E) kπ - 4c / 2b"
        )
        items, structured = extractor.build_local_structured_output(
            raw_output=raw,
            curso="",
            tema="",
            start_n=2,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["n"], 25)
        self.assertIn("25", structured)
        self.assertIn('"C": "{2kπ + π/4}"', structured)
        self.assertIn('"D": "{2kπ + π/3}"', structured)
        self.assertIn('"E": "{2kπ + 2π/3}"', structured)
        self.assertNotIn('"A": "(2k+1)π - 2c / 4b"', structured.split('"items":', 1)[0])
        self.assertNotIn('"B": "(4k+1)π - 4c / 4b"', structured.split('"items":', 1)[0])

    def test_extract_numbered_headers_ignores_inline_option_boundaries(self) -> None:
        raw = (
            "66. Calcule el rango de la función definida por "
            "A) [1/3, 1] B) [1, √3] C) [-1, 3] D) [1, 3] E) [-1/3, 3] "
            "67. Calcule el dominio de la función definida por "
            "A) R - {√3/6} B) R - {√3/6, -√3/2} "
            "C) R - {-√3/2} D) R - {√3/3, -√3} E) R - {1/6, -1/2}"
        )
        self.assertEqual(extract_numbered_headers(raw), [66, 67])

    def test_local_structured_output_preserves_real_number_after_inline_options(self) -> None:
        extractor = ScanExtractor(provider="hf", strict_json=True)
        raw = (
            "66. Calcule el rango de la función definida por\n"
            "f(x) = √3 tan(2x + π/6), x ∈ [0, arctan(2 - √3)]\n"
            "A) [1/3, 1] B) [1, √3] C) [-1, 3]\n"
            "D) [1, 3] E) [-1/3, 3]\n"
            "67. Calcule el dominio de la función definida por\n"
            "f(x) = csc(2arccot(2x) - 2π/3)\n"
            "A) R - {√3/6} B) R - {√3/6, -√3/2}\n"
            "C) R - {-√3/2}\n"
            "D) R - {√3/3, -√3} E) R - {1/6, -1/2}"
        )
        items, structured = extractor.build_local_structured_output(
            raw_output=raw,
            curso="",
            tema="",
            start_n=1,
        )
        self.assertEqual([item["n"] for item in items], [66, 67])
        self.assertIn('"n": 67', structured)

    def test_extract_numbered_headers_supports_circled_unicode_markers(self) -> None:
        raw = "\u2460 Enunciado 1\nA) 1 B) 2 C) 3 D) 4 E) 5\n\u2461 Enunciado 2\nA) 6 B) 7 C) 8 D) 9 E) 10"
        self.assertEqual(extract_numbered_headers(raw), [1, 2])

    def test_extract_numbered_headers_supports_bare_number_lines_and_n_dot_prefix(self) -> None:
        raw = "N.11\nEnunciado 11\nA) 1 B) 2 C) 3 D) 4 E) 5\n\n12\nEnunciado 12\nA) 1 B) 2 C) 3 D) 4 E) 5"
        self.assertEqual(extract_numbered_headers(raw), [11, 12])

    def test_local_structured_output_builds_sequential_items_without_explicit_number_headers(self) -> None:
        extractor = ScanExtractor(provider="ocr", strict_json=False)
        raw = (
            "z^x + 5z^x + 9z^x es:\n"
            "a) z^x\n"
            "b) 10z^x\n"
            "c) 14z^x\n"
            "d) 6z^x\n"
            "e) 15z^x\n\n"
            "-7b^(x+1) - 4b^(x+1) - 3b^(x+1) es:\n\n"
            "a) -14b^x          b) -11b^(x+1)         c) -14b^(x+1)        d) -b^(x+1)       e) -10b^(x+1)"
        )
        items, _structured = extractor.build_local_structured_output(
            raw_output=raw,
            curso="",
            tema="",
            start_n=1,
        )
        self.assertEqual([item["n"] for item in items], [1, 2])
        self.assertIn("z^x + 5z^x + 9z^x", items[0]["statement"])
        self.assertIn("-7b^(x+1) - 4b^(x+1)", items[1]["statement"])

    def test_local_structured_output_keeps_numbering_from_bare_number_lines(self) -> None:
        extractor = ScanExtractor(provider="ocr", strict_json=False)
        raw = (
            "11\n"
            "x + y es:\n"
            "A) 1\nB) 2\nC) 3\nD) 4\nE) 5\n\n"
            "12\n"
            "x - y es:\n"
            "A) 1\nB) 2\nC) 3\nD) 4\nE) 5"
        )
        items, _structured = extractor.build_local_structured_output(
            raw_output=raw,
            curso="",
            tema="",
            start_n=1,
        )
        self.assertEqual([item["n"] for item in items], [11, 12])

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

    def test_process_raw_output_does_not_skip_key_like_raw_when_initial_items_are_present(self) -> None:
        pipeline = ScanPipeline(
            provider="hf",
            max_retries=0,
            parse_max_retries=0,
            strict_json=True,
        )
        raw = (
            "C) {2kπ + π/4} D) {2kπ + π/3} E) {2kπ + 2π/3} "
            "25. Sea f la función definida por: "
            "f(x) = a sen(bx+c)+d y g la función definida por: g(x)=acos(bx+c)+d. "
            "Determine todos los valores de x para las cuales las gráficas de f y g se intersecan. Si k ∈ Z "
            "A) (2k+1)π - 2c / 4b B) (4k+1)π - 4c / 4b "
            "C) (4k+1)π - 4c / 2b D) (2k+1)π - 4c / 2b E) kπ - 4c / 2b"
        )
        initial_items = [
            {
                "schema": "ScanItemJSON-v1",
                "n": 25,
                "curso": "",
                "tema": "",
                "has_figure": False,
                "figure_tag": "",
                "statement": "Sea f la función definida por: f(x)=a sen(bx+c)+d y g la función definida por: g(x)=acos(bx+c)+d.",
                "options": {
                    "A": "(2k+1)π - 2c / 4b",
                    "B": "(4k+1)π - 4c / 4b",
                    "C": "(4k+1)π - 4c / 2b",
                    "D": "(2k+1)π - 4c / 2b",
                    "E": "kπ - 4c / 2b",
                },
                "needs_review": False,
            }
        ]

        result = pipeline.process_raw_output(
            raw_output=raw,
            image_path=Path("mixed_continuation_25.png"),
            start_n=2,
            curso="",
            tema="",
            has_figure_hint=False,
            initial_items=initial_items,
        )

        self.assertEqual(len(result.skipped_images), 0)
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].item.n, 25)

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

    def test_pipeline_adds_warning_for_control_chars(self) -> None:
        pipeline = ScanPipeline(
            provider="hf",
            max_retries=0,
            parse_max_retries=0,
            strict_json=True,
        )
        result = pipeline.process_raw_output(
            raw_output='{"items":[{"schema":"ScanItemJSON-v1","n":1,"curso":"A","tema":"B","has_figure":false,"figure_tag":"","statement":"Texto\\u0008roto","options":{"A":"1","B":"2","C":"3","D":"4","E":"5"},"needs_review":false}]}',
            image_path=Path("problem_control_chars.png"),
            start_n=1,
            curso="A",
            tema="B",
            has_figure_hint=False,
        )
        self.assertEqual(len(result.items), 1)
        self.assertIn("control_chars_detected_in_statement", result.items[0].latex_warnings)

    def test_image_role_keeps_mixed_image_even_if_key_classifier_fires(self) -> None:
        decision = resolve_image_role(
            ImageEvidence(
                raw_text="C) opcion pendiente C D) opcion pendiente D E) opcion pendiente E 42. Enunciado nuevo",
                has_pending_item=True,
                is_key_candidate=True,
                structured_item_count=1,
                leading_options_count=3,
            )
        )

        self.assertEqual(decision.role, "continuation_plus_new_items")
        self.assertTrue(decision.keep_for_processing)

    def test_local_structured_output_does_not_confuse_interval_closure_with_item_header(self) -> None:
        extractor = ScanExtractor(provider="ocr", strict_json=False)
        raw = (
            "f(x) = cos(|x| - π/3); x ∈ [-5π/6; -π/6]\n\n"
            "A) [3/4;2) B) [5/4;2) C) [5/4;2]\n"
            "D) [3/4;2] E) (0;2]\n\n"
            "7. Enunciado nuevo.\n"
            "A) alt1 B) alt2 C) alt3 D) alt4 E) alt5"
        )
        items, structured = extractor.build_local_structured_output(
            raw_output=raw,
            curso="",
            tema="",
            start_n=2,
        )

        self.assertEqual([it["n"] for it in items], [7])
        self.assertIn('"A": "[3/4;2)"', structured)
        self.assertIn('"E": "(0;2]"', structured)


    def test_local_structured_output_keeps_full_leading_options_before_multiple_new_items(self) -> None:
        extractor = ScanExtractor(provider="ocr", strict_json=False)
        raw = (
            "A) opcion pendiente A\n"
            "B) opcion pendiente B\n"
            "C) opcion pendiente C\n"
            "D) opcion pendiente D\n"
            "E) opcion pendiente E\n"
            "32. Enunciado nuevo 32.\n"
            "A) alt 32A B) alt 32B C) alt 32C D) alt 32D E) alt 32E\n"
            "33. Enunciado nuevo 33.\n"
            "A) alt 33A B) alt 33B C) alt 33C D) alt 33D E) alt 33E"
        )
        items, structured = extractor.build_local_structured_output(
            raw_output=raw,
            curso="",
            tema="",
            start_n=2,
        )

        self.assertEqual([it["n"] for it in items], [32, 33])
        self.assertIn('"A": "opcion pendiente A"', structured)
        self.assertIn('"E": "opcion pendiente E"', structured)

    def test_collect_new_item_numbers_uses_only_explicit_evidence_after_pending(self) -> None:
        numbers = collect_new_item_numbers(
            raw_text="C) opcion pendiente C D) opcion pendiente D E) opcion pendiente E 25. Enunciado nuevo",
            pending_num=24,
            structured_items=[
                {"n": 24, "statement": "continuacion"},
                {"n": 25, "statement": "item nuevo"},
            ],
        )
        self.assertEqual(numbers, [25])

    def test_merge_statement_fragments_avoids_repeating_high_overlap_equation(self) -> None:
        merged = merge_statement_fragments(
            "Determine el rango de la función f, definida por: f(x)=cos(x-π/3); x∈[-5π/6;-π/6]",
            "f(x)=cos(|x|-π/3); x∈[-5π/6;-π/6]",
        )
        self.assertEqual(merged.count("f(x)"), 1)

    def test_merge_statement_fragments_appends_real_continuation(self) -> None:
        merged = merge_statement_fragments(
            "Determine el rango de la función f, definida por:",
            "f(x)=cos(|x|-π/3); x∈[-5π/6;-π/6]",
        )
        self.assertIn("Determine el rango", merged)
        self.assertIn("f(x)=cos(|x|-π/3)", merged)


    def test_ocr_ensemble_prefers_clean_candidate_over_mojibake(self) -> None:
        clean = analyze_ocr_candidate(
            model="zai-org/GLM-4.5V",
            raw_text=(
                "1. z^x + 5z^x + 9z^x es:\n"
                "A) z^x\n"
                "B) 10z^x\n"
                "C) 14z^x\n"
                "D) 6z^x\n"
                "E) 15z^x"
            ),
            curso="",
            tema="",
            start_n=1,
        )
        dirty = analyze_ocr_candidate(
            model="google/gemma-3-27b-it",
            raw_text=(
                "1. xâ´ + 5xÂ² + 9xâ»Â¹ es:\n"
                "A) xâ»Â¹\n"
                "B) 10xâ»Â¹\n"
                "C) 14xâ»Â¹\n"
                "D) 6xâ»Â¹\n"
                "E) 15xâ»Â¹"
            ),
            curso="",
            tema="",
            start_n=1,
        )
        best = select_best_ocr_candidate([clean, dirty])
        self.assertEqual(best.model, "zai-org/GLM-4.5V")
        self.assertGreater(clean.score, dirty.score)

    def test_ocr_ensemble_prefers_majority_sequential_numbering(self) -> None:
        candidate_a = analyze_ocr_candidate(
            model="Qwen/Qwen2.5-VL-7B-Instruct",
            raw_text=(
                "16. 7x^2 - {...} es:\n"
                "A) uno\nB) dos\nC) tres\nD) cuatro\nE) cinco\n\n"
                "17. 6z - {...} es:\n"
                "A) uno\nB) dos\nC) tres\nD) cuatro\nE) cinco"
            ),
            curso="",
            tema="",
            start_n=16,
        )
        candidate_b = analyze_ocr_candidate(
            model="Qwen/Qwen2.5-VL-72B-Instruct",
            raw_text=(
                "16. 7x^2 - {...} es:\n"
                "A) uno\nB) dos\nC) tres\nD) cuatro\nE) cinco\n\n"
                "17. 6z - {...} es:\n"
                "A) uno\nB) dos\nC) tres\nD) cuatro\nE) cinco"
            ),
            curso="",
            tema="",
            start_n=16,
        )
        candidate_c = analyze_ocr_candidate(
            model="zai-org/GLM-4.5V",
            raw_text=(
                "3. 7x^2 - {...} es:\n"
                "A) uno\nB) dos\nC) tres\nD) cuatro\nE) cinco"
            ),
            curso="",
            tema="",
            start_n=16,
        )
        best = select_best_ocr_candidate([candidate_a, candidate_b, candidate_c])
        self.assertIn(best.model, {"Qwen/Qwen2.5-VL-7B-Instruct", "Qwen/Qwen2.5-VL-72B-Instruct"})
        self.assertEqual(best.parsed_numbers, [16, 17])

    def test_ocr_ensemble_keeps_only_primary_rescue_models_in_order(self) -> None:
        ordered = resolve_hf_ocr_ensemble_models(
            current_model="zai-org/GLM-4.5V",
            available_models=[
                "zai-org/GLM-4.5V",
                "Qwen/Qwen2.5-VL-72B-Instruct",
                "Qwen/Qwen2.5-VL-7B-Instruct",
                "zai-org/GLM-4.5V-FP8",
                "google/gemma-3-27b-it",
            ],
            unavailable_models=[],
        )
        self.assertEqual(
            ordered,
            [
                "zai-org/GLM-4.5V",
                "Qwen/Qwen2.5-VL-72B-Instruct",
                "Qwen/Qwen2.5-VL-7B-Instruct",
            ],
        )

    def test_ocr_ensemble_does_not_flag_full_structured_output_as_repetitive_garbage(self) -> None:
        candidate = analyze_ocr_candidate(
            model="zai-org/GLM-4.5V",
            raw_text=(
                "11. x^3 + y^3 es:\n"
                "A) uno\nB) dos\nC) tres\nD) cuatro\nE) cinco\n\n"
                "12. x + y + z es:\n"
                "A) uno\nB) dos\nC) tres\nD) cuatro\nE) cinco\n\n"
                "13. 3x + 4y es:\n"
                "A) uno\nB) dos\nC) tres\nD) cuatro\nE) cinco"
            ),
            curso="",
            tema="",
            start_n=11,
        )
        self.assertNotIn("basura_repetitiva", candidate.penalties)

    def test_ocr_ensemble_penalizes_unique_overline_artifact(self) -> None:
        clean = analyze_ocr_candidate(
            model="zai-org/GLM-4.5V",
            raw_text=(
                "11. x^3 + y^3 es:\n"
                "A) uno\nB) dos\nC) tres\nD) cuatro\nE) cinco"
            ),
            curso="",
            tema="",
            start_n=11,
        )
        overline = analyze_ocr_candidate(
            model="Qwen/Qwen2.5-VL-7B-Instruct",
            raw_text=(
                "11. \\overline{x^3 + y^3} es:\n"
                "A) uno\nB) dos\nC) tres\nD) cuatro\nE) cinco"
            ),
            curso="",
            tema="",
            start_n=11,
        )
        best = select_best_ocr_candidate([clean, overline])
        self.assertEqual(best.model, "zai-org/GLM-4.5V")

    def test_ocr_ensemble_does_not_penalize_sqrt_for_radicacion_or_exponent_topics(self) -> None:
        raw_text = (
            "1. Determine el valor reducido de M.\n"
            "M = \\sqrt[3]{3^2} \\cdot \\sqrt[4]{2^5}\n"
            "A) 2\nB) -4\nC) -6\nD) 6\nE) 4"
        )
        general = analyze_ocr_candidate(
            model="Qwen/Qwen2.5-VL-72B-Instruct",
            raw_text=raw_text,
            curso="",
            tema="Polinomios",
            start_n=1,
        )
        radicacion = analyze_ocr_candidate(
            model="Qwen/Qwen2.5-VL-72B-Instruct",
            raw_text=raw_text,
            curso="",
            tema="Radicacion",
            start_n=1,
        )
        teoria_exponentes = analyze_ocr_candidate(
            model="Qwen/Qwen2.5-VL-72B-Instruct",
            raw_text=raw_text,
            curso="",
            tema="S01_Teoria_de_Exponentes",
            start_n=1,
        )
        self.assertIn("sqrt_sospechoso", general.penalties)
        self.assertNotIn("sqrt_sospechoso", radicacion.penalties)
        self.assertNotIn("sqrt_sospechoso", teoria_exponentes.penalties)
        self.assertGreater(radicacion.score, general.score)
        self.assertGreater(teoria_exponentes.score, general.score)

    def test_ocr_candidate_fast_accept_requires_clean_high_score(self) -> None:
        candidate = analyze_ocr_candidate(
            model="zai-org/GLM-4.5V",
            raw_text=(
                "14. 4x^2-[2x^2+y^2-3]+[-4y^2-2x^2+1] es:\n"
                "A) 2x^2-4y^2-1\n"
                "B) 2-5y^2\n"
                "C) x^2-y^2-4\n"
                "D) x^2-5\n"
                "E) y^2-3"
            ),
            curso="",
            tema="",
            start_n=14,
        )
        accepted, reasons = should_accept_ocr_candidate_fast(candidate, start_n=14, min_score=80)
        self.assertTrue(accepted)
        self.assertEqual(reasons, [])

    def test_ocr_candidate_with_canonical_angle_header_is_valid(self) -> None:
        candidate = analyze_ocr_candidate(
            model="zai-org/GLM-4.5V",
            raw_text=(
                "<6.> x^4y - x^3y^2 + x^2y es:\n"
                "A) uno\nB) dos\nC) tres\nD) cuatro\nE) cinco"
            ),
            curso="",
            tema="",
            start_n=6,
        )
        valid, reasons = is_ocr_candidate_valid(candidate, start_n=6)
        self.assertTrue(valid)
        self.assertEqual(reasons, [])

    def test_ocr_candidate_with_good_structure_is_not_invalidated_by_delimiters(self) -> None:
        candidate = analyze_ocr_candidate(
            model="Qwen/Qwen2.5-VL-72B-Instruct",
            raw_text=(
                "<9.> Si se cumple que a1^2+a2^2+...+an^2=p^2 Calcule (a1+a2)/(b1+b2) A) p B) q C) 1 D) q/p E) p/q\n\n"
                "<10.> Dadas las relaciones a=(a-b)^2+b(a+1) Halle (a^6-b^6)^2/(c^6-4a^3b^3) A) a^3b^3 B) b^3c^3 C) a^3+b^6 D) a^3c^3 E) c^6"
            ),
            curso="",
            tema="",
            start_n=9,
        )
        valid, reasons = is_ocr_candidate_valid(candidate, start_n=9)
        self.assertTrue(valid)
        self.assertNotIn("delimitadores_graves", reasons)

    def test_ocr_candidate_with_partial_option_structure_is_recoverable(self) -> None:
        candidate = OCRCandidateAnalysis(
            model="Qwen/Qwen2.5-VL-72B-Instruct",
            raw_text="<9.> problema 9 ... <10.> problema 10 ...",
            score=74,
            header_numbers=[9, 10],
            parsed_numbers=[9, 10],
            item_count=2,
            complete_option_items=0,
            option_label_total=8,
            leading_option_count=0,
            has_leading_continuation=False,
            continuation_candidate=False,
            mojibake_hits=0,
            delimiter_imbalance=4,
            penalties=["delimitadores=4"],
            bonuses=[],
        )
        valid, valid_reasons = is_ocr_candidate_valid(candidate, start_n=9)
        recoverable, recoverable_reasons = is_ocr_candidate_recoverable(candidate, start_n=9)
        self.assertFalse(valid)
        self.assertIn("delimitadores_graves", valid_reasons)
        self.assertTrue(recoverable)
        self.assertEqual(recoverable_reasons, [])

    def test_ocr_candidate_missing_expected_start_is_recoverable_but_not_valid(self) -> None:
        candidate = OCRCandidateAnalysis(
            model="zai-org/GLM-4.5V",
            raw_text="<10.> solo item 10 ...",
            score=47,
            header_numbers=[10],
            parsed_numbers=[10],
            item_count=1,
            complete_option_items=1,
            option_label_total=5,
            leading_option_count=0,
            has_leading_continuation=False,
            continuation_candidate=False,
            mojibake_hits=0,
            delimiter_imbalance=0,
            penalties=[],
            bonuses=[],
        )
        valid, valid_reasons = is_ocr_candidate_valid(candidate, start_n=9)
        recoverable, recoverable_reasons = is_ocr_candidate_recoverable(candidate, start_n=9)
        self.assertFalse(valid)
        self.assertIn("cobertura_inicial_insuficiente", valid_reasons)
        self.assertTrue(recoverable)
        self.assertEqual(recoverable_reasons, [])

    def test_ocr_candidate_strong_misaligned_start_is_recoverable(self) -> None:
        candidate = analyze_ocr_candidate(
            model="Qwen/Qwen2.5-VL-72B-Instruct",
            raw_text=(
                "<31.> Hallar el valor de n si el grado de P y Q es 3 y 4 respectivamente.\n"
                "A) 1\nB) 2\nC) 3\nD) -1\nE) 4\n\n"
                "<32.> Hallar m, si el polinomio F(x) = (x^{m^m}+4x-2)^{m^m}(2x^m+x+3)^{m-1} es de grado 756.\n"
                "A) 3\nB) 27\nC) 28\nD) 9\nE) N.A."
            ),
            curso="",
            tema="S04_Expresiones_Algebraicas",
            start_n=50,
        )
        valid, valid_reasons = is_ocr_candidate_valid(candidate, start_n=50)
        recoverable, recoverable_reasons = is_ocr_candidate_recoverable(candidate, start_n=50)
        self.assertFalse(valid)
        self.assertIn("cobertura_inicial_insuficiente", valid_reasons)
        self.assertTrue(recoverable)
        self.assertEqual(recoverable_reasons, [])

    def test_ocr_candidate_accepts_numbering_restart_after_large_expected_start(self) -> None:
        candidate = analyze_ocr_candidate(
            model="Qwen/Qwen2.5-VL-72B-Instruct",
            raw_text=(
                "<01.> Simplificar: T = 1\n"
                "A) 1\nB) 2\nC) 3\nD) 6\nE) 10\n\n"
                "<02.> Calcular: E = x^x\n"
                "A) 8\nB) 64\nC) 16\nD) 4\nE) 32\n\n"
                "<03.> Si x^x = 2, hallar V.\n"
                "A) 1\nB) 2\nC) 3\nD) 4\nE) 5"
            ),
            curso="",
            tema="S01_Teoria_de_Exponentes",
            start_n=36,
        )
        valid, valid_reasons = is_ocr_candidate_valid(candidate, start_n=36)
        recoverable, recoverable_reasons = is_ocr_candidate_recoverable(candidate, start_n=36)
        self.assertTrue(valid)
        self.assertEqual(valid_reasons, [])
        self.assertTrue(recoverable)
        self.assertEqual(recoverable_reasons, [])
        self.assertIn("reinicio_numeracion", candidate.bonuses)
        self.assertNotIn("arranque_regresivo", candidate.penalties)

    def test_ocr_candidate_accepts_single_complete_item_after_numbering_restart(self) -> None:
        candidate = analyze_ocr_candidate(
            model="Qwen/Qwen2.5-VL-72B-Instruct",
            raw_text=(
                "<05.> Si: A = \\sqrt{20 + \\sqrt{20 + \\sqrt{20 + \\ldots}}}, ademas:\n"
                "T = \\sqrt[4]{A + 11 + \\sqrt[4]{A + 11 + \\sqrt[4]{A + 11 + \\ldots}}}\n"
                "Calcular: \\sqrt[4]{T^4 - T}\n"
                "A) \\sqrt{2}\nB) 2\nC) \\sqrt[4]{20}\nD) \\sqrt[4]{31}\nE) \\sqrt[4]{15}"
            ),
            curso="",
            tema="S02_Radicales",
            start_n=36,
        )
        valid, valid_reasons = is_ocr_candidate_valid(candidate, start_n=36)
        recoverable, recoverable_reasons = is_ocr_candidate_recoverable(candidate, start_n=36)
        self.assertTrue(valid)
        self.assertEqual(valid_reasons, [])
        self.assertTrue(recoverable)
        self.assertEqual(recoverable_reasons, [])
        self.assertIn("reinicio_numeracion", candidate.bonuses)
        self.assertNotIn("arranque_regresivo", candidate.penalties)

    def test_ocr_candidate_with_misaligned_start_and_delimiters_is_recoverable(self) -> None:
        candidate = analyze_ocr_candidate(
            model="Qwen/Qwen2.5-VL-72B-Instruct",
            raw_text=(
                "<24.> Indicar el valor de (a+b+c) si: ax^2 + 3x + (x+3)^2 + bx + 3c = 0\n"
                "A) cero\nB) -6\nC) 7\nD) -13\nE) 10\n\n"
                "<25.> Si el polinomio se anula para mas de 2 valores asignados a su variable.\n"
                "P(x) = (ab+ac-3)x^2 + (ac+bc-6)x + (ab+bc-9)\n"
                "Hallar: T = abc(a+b)(a+c)(b+c)\n"
                "A) 160\nB) 163\nC) 161\nD) 162\nE) 164"
            ),
            curso="",
            tema="S05_Polinomios_Especiales",
            start_n=27,
        )
        valid, valid_reasons = is_ocr_candidate_valid(candidate, start_n=27)
        recoverable, recoverable_reasons = is_ocr_candidate_recoverable(candidate, start_n=27)
        self.assertFalse(valid)
        self.assertIn("cobertura_inicial_insuficiente", valid_reasons)
        self.assertTrue(recoverable)
        self.assertEqual(recoverable_reasons, [])

    def test_should_continue_numbering_for_items_detects_restart_block(self) -> None:
        items = [
            {
                "schema": "ScanItemJSON-v1",
                "n": 1,
                "curso": "",
                "tema": "S02_Radicales",
                "has_figure": False,
                "figure_tag": "",
                "statement": "Problema 1",
                "options": {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"},
                "needs_review": False,
            },
            {
                "schema": "ScanItemJSON-v1",
                "n": 2,
                "curso": "",
                "tema": "S02_Radicales",
                "has_figure": True,
                "figure_tag": "img-2",
                "statement": "Problema 2",
                "options": {"A": "6", "B": "7", "C": "8", "D": "9", "E": "10"},
                "needs_review": False,
            },
        ]
        self.assertTrue(should_continue_numbering_for_items(items, start_n=36))
        renumbered, mapping = renumber_items_continuously(items, start_n=36)
        self.assertEqual([it["n"] for it in renumbered], [36, 37])
        self.assertEqual(mapping, [(1, 36), (2, 37)])
        self.assertEqual(renumbered[1]["figure_tag"], "img-37")

    def test_pipeline_process_raw_output_renumbers_restarting_initial_items(self) -> None:
        pipeline = ScanPipeline(provider="ocr", strict_json=False, debug_dir="")
        initial_items = [
            {
                "schema": "ScanItemJSON-v1",
                "n": 1,
                "curso": "",
                "tema": "S02_Radicales",
                "has_figure": False,
                "figure_tag": "",
                "statement": "Problema 1",
                "options": {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"},
                "needs_review": False,
            },
            {
                "schema": "ScanItemJSON-v1",
                "n": 2,
                "curso": "",
                "tema": "S02_Radicales",
                "has_figure": False,
                "figure_tag": "",
                "statement": "Problema 2",
                "options": {"A": "6", "B": "7", "C": "8", "D": "9", "E": "10"},
                "needs_review": False,
            },
        ]
        run = pipeline.process_raw_output(
            raw_output="<01.> Problema 1\nA) 1\nB) 2\nC) 3\nD) 4\nE) 5\n\n<02.> Problema 2\nA) 6\nB) 7\nC) 8\nD) 9\nE) 10",
            image_path=ROOT / "tests" / "fixtures" / "dummy.png",
            start_n=36,
            curso="",
            tema="S02_Radicales",
            initial_items=initial_items,
        )
        self.assertEqual([row.item.n for row in run.items], [36, 37])

    def test_select_best_ocr_candidate_prefers_recoverable_remote_with_better_numbering(self) -> None:
        glm = OCRCandidateAnalysis(
            model="zai-org/GLM-4.5V",
            raw_text="<10.> solo item 10 ...",
            score=82,
            header_numbers=[10],
            parsed_numbers=[10],
            item_count=1,
            complete_option_items=1,
            option_label_total=5,
            leading_option_count=0,
            has_leading_continuation=False,
            continuation_candidate=False,
            mojibake_hits=0,
            delimiter_imbalance=0,
            penalties=[],
            bonuses=[],
        )
        qwen72 = OCRCandidateAnalysis(
            model="Qwen/Qwen2.5-VL-72B-Instruct",
            raw_text="<9.> problema 9 ... <10.> problema 10 ...",
            score=74,
            header_numbers=[9, 10],
            parsed_numbers=[9, 10],
            item_count=2,
            complete_option_items=0,
            option_label_total=8,
            leading_option_count=0,
            has_leading_continuation=False,
            continuation_candidate=False,
            mojibake_hits=0,
            delimiter_imbalance=4,
            penalties=["delimitadores=4"],
            bonuses=[],
        )
        best = select_best_ocr_candidate([glm, qwen72], start_n=9)
        self.assertEqual(best.model, "Qwen/Qwen2.5-VL-72B-Instruct")

    def test_select_best_ocr_candidate_prioritizes_better_numbering_over_model_preference(self) -> None:
        glm = analyze_ocr_candidate(
            model="zai-org/GLM-4.5V",
            raw_text=(
                "[CONT.] Si se cumple que ... A) p B) q C) 1 D) q/p E) p/q\n\n"
                "<10.> Dadas las relaciones ... A) a^3b^3 B) b^3c^3 C) a^3+b^6 D) a^3c^3 E) c^6"
            ),
            curso="",
            tema="",
            start_n=9,
        )
        qwen72 = analyze_ocr_candidate(
            model="Qwen/Qwen2.5-VL-72B-Instruct",
            raw_text=(
                "<9.> Si se cumple que ... A) p B) q C) 1 D) q/p E) p/q\n\n"
                "<10.> Dadas las relaciones ... A) a^3b^3 B) b^3c^3 C) a^3+b^6 D) a^3c^3 E) c^6"
            ),
            curso="",
            tema="",
            start_n=9,
        )
        best = select_best_ocr_candidate([glm, qwen72], start_n=9)
        self.assertEqual(best.model, "Qwen/Qwen2.5-VL-72B-Instruct")

    def test_canonicalize_faithful_ocr_text_wraps_problem_numbers_as_angle_headers(self) -> None:
        raw = (
            "1. Enunciado uno A) a B) b C) c D) d E) e "
            "2. Enunciado dos A) a B) b C) c D) d E) e"
        )
        canonical = canonicalize_faithful_ocr_text(raw, start_n=1)
        self.assertIn("<1.>", canonical)
        self.assertIn("<2.>", canonical)
        self.assertIn("\n\n<2.>", canonical)

    def test_canonicalize_faithful_ocr_text_ignores_stray_integer_inside_statement(self) -> None:
        raw = (
            "3. ¿Cuántos radicales debemos tomar en la expresión ... de modo que el exponente final de x sea "
            "297. x∈R^+ - {1} A) 20 B) 21 C) 22 D) 23 E) 24 "
            "4. Se tienen los siguientes polinomios."
        )
        canonical = canonicalize_faithful_ocr_text(raw, start_n=3)
        self.assertIn("<3.>", canonical)
        self.assertIn("<4.>", canonical)
        self.assertNotIn("<297.>", canonical)

    def test_canonicalize_faithful_ocr_text_preserves_leading_continuation(self) -> None:
        raw = (
            "Halle 9a+3b+3c A) 3 B) -3 C) -18 D) 0 E) -1 "
            "5. Se define la expresiÃ³n algebraica f en los enteros."
        )
        canonical = canonicalize_faithful_ocr_text(raw, start_n=5)
        self.assertTrue(canonical.startswith("[CONT.] Halle 9a+3b+3c"))
        self.assertIn("A) 3 B) -3 C) -18 D) 0 E) -1", canonical)
        self.assertIn("<5.>", canonical)

    def test_build_local_structured_output_ignores_stray_integer_inside_statement(self) -> None:
        raw = (
            "<3.> ¿Cuántos radicales debemos tomar en la expresión √x^{3!}√x^{4!}√x^{5!}...n+√x^{(n+2)!} "
            "de modo que el exponente final de x sea 297. x∈R^+ - {1} A) 20 B) 21 C) 22 D) 23 E) 24\n\n"
            "<4.> Se tienen los siguientes polinomios. P(x)=ax^{3}+bx^{2}+cx+3 "
            "Q(x)=3x^{3}+cx^{2}+bx+a donde se cumple P(x+2)+Q(x-1)≡6x-18"
        )
        extractor = ScanExtractor(provider="ocr", model="", strict_json=False)
        items, structured_raw = extractor.build_local_structured_output(
            raw_output=raw,
            curso="",
            tema="",
            start_n=3,
        )
        self.assertEqual([int(item["n"]) for item in items], [3, 4])
        self.assertIn('\"n\": 3', structured_raw)
        self.assertIn('\"n\": 4', structured_raw)
        self.assertNotIn('\"n\": 297', structured_raw)

    def test_build_local_structured_output_extracts_markerized_leading_continuation(self) -> None:
        raw = (
            "[CONT.] Halle 9a+3b+3c A) 3 B) -3 C) -18 D) 0 E) -1\n\n"
            "<5.> Se define la expresiÃƒÂ³n algebraica f en los enteros."
        )
        extractor = ScanExtractor(provider="ocr", model="", strict_json=False)
        items, structured_raw = extractor.build_local_structured_output(
            raw_output=raw,
            curso="",
            tema="",
            start_n=5,
        )
        self.assertEqual([int(item["n"]) for item in items], [5])
        self.assertIn('"leading_continuation": "Halle 9a+3b+3c"', structured_raw)
        self.assertIn('"A": "3"', structured_raw)
        self.assertIn('"E": "-1"', structured_raw)

    def test_normalize_structured_payload_ignores_model_leading_when_no_real_prefix(self) -> None:
        payload = {
            "leading_continuation": "¿Cuántos radicales debemos tomar en la expresión ...",
            "leading_options": {"A": "20", "B": "21", "C": "22", "D": "23", "E": "24"},
            "leading_option_labels": ["A", "B", "C", "D", "E"],
            "leading_has_figure": True,
            "items": [
                {"n": 99, "statement": "¿Cuántos radicales debemos tomar en la expresión ...", "options": {"A": "20", "B": "21", "C": "22", "D": "23", "E": "24"}},
                {"n": 100, "statement": "Se tienen los siguientes polinomios.", "options": {"A": "...", "B": "...", "C": "...", "D": "...", "E": "..."}},
            ],
        }
        normalized = _normalize_structured_payload(
            payload=payload,
            leading_payload=None,
            detected_headers=[3, 4],
        )
        self.assertEqual(normalized["leading_continuation"], "")
        self.assertEqual(normalized["leading_options"], {})
        self.assertEqual(normalized["leading_option_labels"], [])
        self.assertFalse(normalized["leading_has_figure"])
        self.assertEqual([int(item["n"]) for item in normalized["items"]], [3, 4])

    def test_pipeline_correction_retry_failure_does_not_crash_batch(self) -> None:
        pipeline = ScanPipeline(provider="hf", model="demo", max_retries=1, strict_json=True)

        def _boom(**_: object) -> dict:
            raise RuntimeError("Error code: 500 - internal")

        pipeline.extractor.correct_item = _boom  # type: ignore[assignment]
        item = ScanItem.empty(n=11, curso="SIN_CURSO", tema="SIN_TEMA")
        row = pipeline._validate_and_retry_item(
            item=item,
            normalize_meta={"unknown_symbols": ["Â³"]},
            image_path=Path("dummy.png"),
            curso="SIN_CURSO",
            tema="SIN_TEMA",
        )
        self.assertTrue(row.item.needs_review)
        self.assertTrue(any(err.startswith("correction_retry_failed:") for err in row.render_errors))

    def test_pipeline_parse_retry_failure_does_not_raise(self) -> None:
        pipeline = ScanPipeline(provider="hf", model="demo", max_retries=1, parse_max_retries=1, strict_json=True)

        def _no_parse(**_: object) -> list[dict]:
            return []

        def _no_structure(**_: object) -> tuple[list[dict], str]:
            return ([], "sin parse")

        def _repair_boom(**_: object) -> tuple[list[dict], str]:
            raise RuntimeError("Error code: 500 - internal")

        pipeline.extractor.parse_raw_output = _no_parse  # type: ignore[assignment]
        pipeline.extractor.structure_raw_output = _no_structure  # type: ignore[assignment]
        pipeline.extractor.repair_raw_output = _repair_boom  # type: ignore[assignment]
        run = pipeline.process_raw_output(
            raw_output="texto sin parse",
            image_path=Path("dummy.png"),
            start_n=1,
            curso="SIN_CURSO",
            tema="SIN_TEMA",
        )
        self.assertGreaterEqual(run.json_parse_failed_count, 1)
        self.assertTrue(run.items)

    def test_pipeline_uses_statement_header_number_when_json_n_is_wrong(self) -> None:
        pipeline = ScanPipeline(provider="ocr", model="", max_retries=0, strict_json=False)
        run = pipeline.process_raw_output(
            raw_output="raw",
            image_path=Path("dummy.png"),
            start_n=25,
            curso="SIN_CURSO",
            tema="SIN_TEMA",
            initial_items=[
                {
                    "schema": "ScanItemJSON-v1",
                    "n": 25,
                    "curso": "",
                    "tema": "",
                    "has_figure": False,
                    "figure_tag": "",
                    "statement": "<26> La simplificación de E es:",
                    "options": {"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"},
                    "needs_review": False,
                }
            ],
        )
        self.assertEqual(len(run.items), 1)
        self.assertEqual(run.items[0].item.n, 26)
        self.assertFalse(run.items[0].item.statement.startswith("<26>"))
        self.assertIn("simplific", run.items[0].item.statement.lower())

    def test_canonicalize_faithful_ocr_text_promotes_missing_first_item_before_next_header(self) -> None:
        raw = (
            "Si se cumple que a1^2 + a2^2 + ... + an^2 = p^2 "
            "A) p B) q C) 1 D) q/p E) p/q "
            "<10.> Dadas las relaciones a=(a-b)^2+b(a+1) "
            "A) a^3b^3 B) b^3c^3 C) a^3+b^6 D) a^3c^3 E) c^6"
        )
        canonical = canonicalize_faithful_ocr_text(raw, start_n=9)
        self.assertTrue(canonical.startswith("<9.> Si se cumple que"))
        self.assertIn("\n\n<10.>", canonical)
        self.assertNotIn("[CONT.]", canonical)

    def test_canonicalize_faithful_ocr_text_promotes_full_problem_without_header(self) -> None:
        raw = "En el gráfico, calcule x en función de β A) uno B) dos C) tres D) cuatro E) cinco"
        canonical = canonicalize_faithful_ocr_text(raw, start_n=5)
        self.assertTrue(canonical.startswith("<5.> En el gráfico"))

    def test_canonicalize_faithful_ocr_text_marks_option_only_payload_as_continuation(self) -> None:
        raw = "A) 5√3 B) 6√7 C) 2√21 D) 2√13 E) 3√13"
        canonical = canonicalize_faithful_ocr_text(raw, start_n=12)
        self.assertTrue(canonical.startswith("[CONT.] A) 5"))

    def test_canonicalize_faithful_ocr_text_strips_regressive_header_from_option_only_continuation(self) -> None:
        raw = "11. A) 5√3 B) 6√7 C) 2√21 D) 2√13 E) 3√13"
        canonical = canonicalize_faithful_ocr_text(raw, start_n=12)
        self.assertTrue(canonical.startswith("[CONT.] A) 5"))
        self.assertNotIn("<11.>", canonical)

    def test_canonicalize_faithful_ocr_text_keeps_markerized_option_only_payload_as_continuation(self) -> None:
        raw = "[CONT.] A) 1/10 B) 1 C) 1000 D) 100 E) 10"
        canonical = canonicalize_faithful_ocr_text(raw, start_n=7)
        self.assertEqual(canonical, "[CONT.] A) 1/10 B) 1 C) 1000 D) 100 E) 10")

    def test_canonicalize_faithful_ocr_text_promotes_markerized_missing_problem_before_next_header(self) -> None:
        raw = (
            "[CONT.] 7. Si se cumple que x^(x^5)=2^32, determine el valor de "
            "sqrt[3](2x^5). A) 5 B) 32 C) 8 D) 2 E) 4 "
            "<8.> Dada la sucesion ... A) a B) b C) c D) d E) e"
        )
        canonical = canonicalize_faithful_ocr_text(raw, start_n=7)
        self.assertTrue(canonical.startswith("<7.> Si se cumple que"))
        self.assertIn("\n\n<8.>", canonical)
        self.assertNotIn("[CONT.] 7.", canonical)

    def test_next_item_number_hint_uses_existing_faithful_ocr_headers(self) -> None:
        from modulos.modulo0_transcriptor.gui_transcriptor import TranscriptorWindow

        app = TranscriptorWindow.__new__(TranscriptorWindow)
        app._items = []
        app._ocr_raw_first_by_label = {
            "a": "<1.> uno\n\n<2.> dos",
            "b": "[CONT.] A) a B) b\n\n<4.> cuatro",
        }
        hint = TranscriptorWindow._next_item_number_hint(app, None, 2)
        self.assertEqual(hint, 5)


if __name__ == "__main__":
    unittest.main()
