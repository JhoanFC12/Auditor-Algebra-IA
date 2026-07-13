from pathlib import Path
import json
import tempfile
import unittest

from modulos.book_visual_catalog import (
    DEFAULT_BOOTSTRAP_LABEL,
    InventoryRecord,
    PAGE_LABELS,
    PdfListingRecord,
    build_book_id,
    build_label_ranges,
    build_theme_payload,
    classify_ocr_text,
    detect_part_reason,
    find_duplicate_match,
    find_general_pdf_for_part,
    infer_bibliographic_fields,
    infer_course_folder,
    parse_page_spec,
    render_pdf_listing_markdown,
    render_obsidian_markdown,
    write_part_record,
    write_themes_json,
    PartMatch,
)


class BookVisualCatalogTests(unittest.TestCase):
    def test_parse_page_spec_supports_ranges_and_pages(self):
        self.assertEqual(parse_page_spec("1-3,5,7-8", 10), [1, 2, 3, 5, 7, 8])

    def test_build_label_ranges_groups_contiguous_pages(self):
        rows = [
            {"page_number": 1, "page_label": "dudosa"},
            {"page_number": 2, "page_label": "dudosa"},
            {"page_number": 3, "page_label": "indice"},
            {"page_number": 4, "page_label": "indice"},
            {"page_number": 5, "page_label": "teoria"},
        ]
        payload = build_label_ranges(rows)
        self.assertEqual(
            payload["ranges"],
            [
                {"label": "dudosa", "start_page": 1, "end_page": 2, "pages_total": 2},
                {"label": "indice", "start_page": 3, "end_page": 4, "pages_total": 2},
                {"label": "teoria", "start_page": 5, "end_page": 5, "pages_total": 1},
            ],
        )
        self.assertEqual(payload["label_counts"]["dudosa"], 2)
        self.assertEqual(payload["label_counts"]["indice"], 2)
        self.assertEqual(payload["label_counts"]["teoria"], 1)

    def test_build_book_id_uses_slug_and_hash_prefix(self):
        book_id = build_book_id(Path(r"E:\Banco de Preguntas\Algebra Basica.pdf"), "abcdef1234567890")
        self.assertEqual(book_id, "algebra-basica-abcdef1234")

    def test_infer_course_folder_from_source_paths(self):
        cases = [
            (r"E:\Banco de Preguntas\1. ALGEBRA\demo.pdf", "1. ALGEBRA/demo.pdf", "Libros de Algebra"),
            (r"E:\Banco de Preguntas\2. GEOMETRIA\demo.pdf", "2. GEOMETRIA/demo.pdf", "Geometria"),
            (
                r"E:\Banco de Preguntas\3. GEOMETRIA ANALITICA\demo.pdf",
                "3. GEOMETRIA ANALITICA/demo.pdf",
                "Geometria Analitica",
            ),
            (r"E:\Banco de Preguntas\4. TRIGONOMETRIA\demo.pdf", "4. TRIGONOMETRIA/demo.pdf", "Trigonometria"),
            (r"E:\Banco de Preguntas\5. ARITMETICA\demo.pdf", "5. ARITMETICA/demo.pdf", "Aritmetica"),
            (r"E:\Banco de Preguntas\CONAMAT\demo.pdf", "CONAMAT/demo.pdf", "Examenes y Concursos"),
        ]
        for pdf_path, relpath, expected in cases:
            with self.subTest(pdf_path=pdf_path):
                self.assertEqual(infer_course_folder(pdf_path=pdf_path, pdf_relpath=relpath), expected)

    def test_infer_bibliographic_fields_from_path(self):
        payload = infer_bibliographic_fields(
            pdf_path=r"E:\Banco de Preguntas\1. ALGEBRA\1. Cuzcano\2. Luis_Manrique\PRE UNI- ALGEBRA.pdf",
            pdf_relpath="1. ALGEBRA/1. Cuzcano/2. Luis_Manrique/PRE UNI- ALGEBRA.pdf",
        )
        self.assertEqual(payload["bibliographic_title"], "PRE UNI ALGEBRA")
        self.assertEqual(payload["bibliographic_author"], "Luis Manrique")
        self.assertEqual(payload["bibliographic_collection"], "Cuzcano")
        self.assertEqual(payload["material_type"], "libro")
        self.assertEqual(payload["bibliographic_status"], "inferred_from_path")

    def test_infer_bibliographic_fields_does_not_treat_collection_as_author(self):
        payload = infer_bibliographic_fields(
            pdf_path=r"E:\Banco de Preguntas\1. ALGEBRA\1. Cuzcano\LOGARITMOS (1).pdf",
            pdf_relpath="1. ALGEBRA/1. Cuzcano/LOGARITMOS (1).pdf",
        )
        self.assertEqual(payload["bibliographic_author"], "")
        self.assertEqual(payload["bibliographic_collection"], "Cuzcano")

    def test_render_obsidian_markdown_mentions_core_artifacts(self):
        class _Record:
            book_id = "demo-book-abcdef1234"
            pdf_hash_sha256 = "abcdef123456"
            page_count = 12
            pdf_path = r"E:\Banco de Preguntas\demo.pdf"
            pdf_relpath = "demo.pdf"
            source_root = r"E:\Banco de Preguntas"
            inventory_status = "ok"
            metadata_title = ""
            metadata_author = ""
            bibliographic_title = "demo"
            bibliographic_author = "Autor Demo"
            bibliographic_editorial = "Editorial Demo"
            bibliographic_collection = "Coleccion Demo"
            material_type = "libro"
            bibliographic_status = "inferred_from_path"
            bibliographic_notes = "nota"

        page_rows = [
            {
                "page_number": 1,
                "page_label": DEFAULT_BOOTSTRAP_LABEL,
                "image_path": "pages/page-0001.png",
                "thumbnail_path": "thumbnails/page-0001.jpg",
            }
        ]
        range_payload = build_label_ranges(page_rows)
        rendered = render_obsidian_markdown(
            record=_Record(),
            book_dir=Path("unused"),
            page_rows=page_rows,
            range_payload=range_payload,
            contact_sheets=[
                {
                    "sheet_index": 1,
                    "start_page": 1,
                    "end_page": 1,
                    "image_path": "contact_sheets/contact_sheet_001.png",
                }
            ],
            analyzed_at="2026-07-09T12:00:00+00:00",
        )
        self.assertIn("# demo", rendered)
        self.assertIn("## Contact Sheets", rendered)
        self.assertIn("contact_sheets/contact_sheet_001.png", rendered)
        self.assertIn(DEFAULT_BOOTSTRAP_LABEL, rendered)
        for label in PAGE_LABELS:
            self.assertIn(f"`{label}`", rendered)

    def test_render_pdf_listing_markdown_groups_by_course(self):
        rendered = render_pdf_listing_markdown(
            [
                PdfListingRecord(
                    index=1,
                    course="Libros de Algebra",
                    source_top_folder="1. ALGEBRA",
                    pdf_path=r"E:\Banco de Preguntas\1. ALGEBRA\demo.pdf",
                    pdf_relpath="1. ALGEBRA/demo.pdf",
                    file_name="demo.pdf",
                    file_size_bytes=1024 * 1024,
                    modified_at="2026-07-10T00:00:00+00:00",
                )
            ]
        )
        self.assertIn("Total PDF: `1`", rendered)
        self.assertIn("## Libros de Algebra", rendered)
        self.assertIn("`1. ALGEBRA/demo.pdf`", rendered)

    def test_find_duplicate_match_detects_same_pdf_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            book_dir = root / "books" / "canonical-book"
            book_dir.mkdir(parents=True)
            (book_dir / "book.json").write_text(
                '{"book_id":"canonical-book","pdf_path":"E:/a.pdf","pdf_hash_sha256":"abc123"}',
                encoding="utf-8",
            )
            record = InventoryRecord(
                schema_version="book_visual_catalog_v1",
                book_id="candidate-book",
                source_root="E:/",
                pdf_path="E:/b.pdf",
                pdf_relpath="b.pdf",
                pdf_hash_sha256="abc123",
                file_size_bytes=1,
                modified_at="",
                discovered_at="",
                page_count=1,
            )

            match = find_duplicate_match(record=record, output_root=root)

            self.assertIsNotNone(match)
            self.assertEqual(match.match_type, "exact_hash")
            self.assertEqual(match.canonical_book_id, "canonical-book")

    def test_detect_part_reason_for_problem_image_pdf(self):
        reason = detect_part_reason(
            pdf_path=r"E:\Banco\2. GEOMETRIA\S4-Congruencia_de_Triangulos_IMG\Problema12.3.pdf",
            pdf_relpath="2. GEOMETRIA/S4-Congruencia_de_Triangulos_IMG/Problema12.3.pdf",
        )
        self.assertIn("problema", reason.lower())

    def test_find_general_pdf_for_part_prefers_parent_general_pdf(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            parent = root / "CEPUNT 2027-I"
            part_dir = parent / "S4-Congruencia_de_Triangulos_IMG"
            part_dir.mkdir(parents=True)
            general = parent / "Congruencia de Triangulos_Solucionario.pdf"
            part = part_dir / "Problema12.3.pdf"
            general.write_bytes(b"general" * 100)
            part.write_bytes(b"part")

            resolved = find_general_pdf_for_part(part, source_root=root)

            self.assertEqual(resolved, general.resolve())

    def test_detect_part_reason_for_page_range_pdf(self):
        reason = detect_part_reason(
            pdf_path=r"E:\Banco\ALG. (08) PRODUCTOS-NOTABLES-II_117--------120.pdf",
            pdf_relpath="1. ALGEBRA/Vesalius/ALG. (08) PRODUCTOS-NOTABLES-II_117--------120.pdf",
        )
        self.assertIn("rango", reason.lower())

    def test_write_part_record_deduplicates_same_part(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            match = PartMatch(
                match_type="part_of_general_pdf",
                general_pdf_path="E:/Banco/general.pdf",
                part_pdf_path="E:/Banco/parte.pdf",
                reason="PDF individual de problema detectado.",
            )

            write_part_record(root, match)
            write_part_record(root, match)

            rows = (root / "parts_rejected.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)

    def test_classify_ocr_text_detects_theory(self):
        result = classify_ocr_text(
            text="Definicion. Propiedades de los polinomios. Teorema fundamental.",
            page_number=5,
            page_count=20,
        )
        self.assertEqual(result.label, "teoria")

    def test_classify_ocr_text_detects_proposed_problems(self):
        result = classify_ocr_text(
            text="Ejercicios propuestos. Resolver los siguientes problemas de algebra.",
            page_number=12,
            page_count=30,
        )
        self.assertEqual(result.label, "problemas_propuestos")

    def test_classify_ocr_text_detects_numbered_multiple_choice_problems(self):
        result = classify_ocr_text(
            text=(
                "01. Si los polinomios son homogeneos, calcular el valor. "
                "A) 1 B) 2 C) 3 D) 4 E) 5 "
                "02. Hallar x. A) 8 B) 9 C) 10 D) 11 E) 12 "
                "03. Indique el valor correcto."
            ),
            page_number=2,
            page_count=5,
        )
        self.assertEqual(result.label, "problemas_propuestos")

    def test_classify_ocr_text_detects_mixed_page(self):
        result = classify_ocr_text(
            text="Definicion y propiedades. Ejercicios propuestos y problemas resueltos.",
            page_number=8,
            page_count=30,
        )
        self.assertEqual(result.label, "mixta")

    def test_build_theme_payload_exports_single_topic_with_segments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            book_dir = Path(tmpdir) / "books" / "logaritmos-demo"
            book_dir.mkdir(parents=True)
            (book_dir / "book.json").write_text(
                (
                    '{"book_id":"logaritmos-demo","page_count":30,'
                    '"bibliographic":{"title":"LOGARITMOS (1)","material_type":"libro"}}'
                ),
                encoding="utf-8",
            )
            (book_dir / "ranges.json").write_text(
                json.dumps(
                    {
                        "ranges": [
                            {"label": "portada", "start_page": 1, "end_page": 1},
                            {"label": "teoria", "start_page": 3, "end_page": 4},
                            {"label": "ejemplos", "start_page": 5, "end_page": 6},
                            {"label": "problemas_propuestos", "start_page": 12, "end_page": 30},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            payload = build_theme_payload(book_dir=book_dir, generated_at="2026-07-10T00:00:00+00:00")

            self.assertEqual(payload["schema_version"], "book_catalog_themes_v1")
            self.assertEqual(payload["status"], "tema_unico")
            self.assertEqual(len(payload["themes"]), 1)
            theme = payload["themes"][0]
            self.assertEqual(theme["theme_name"], "Logaritmos")
            self.assertEqual(theme["start_page"], 1)
            self.assertEqual(theme["end_page"], 30)
            self.assertEqual(
                theme["segments"],
                [
                    {"label": "teoria", "start_page": 3, "end_page": 4},
                    {"label": "ejemplos", "start_page": 5, "end_page": 6},
                    {"label": "problemas_propuestos", "start_page": 12, "end_page": 30},
                ],
            )

    def test_build_theme_payload_leaves_generic_book_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            book_dir = Path(tmpdir) / "books" / "ceprevi-algebra-demo"
            book_dir.mkdir(parents=True)
            (book_dir / "book.json").write_text(
                (
                    '{"book_id":"ceprevi-algebra-demo","page_count":86,'
                    '"bibliographic":{"title":"CEPREVI Álgebra","material_type":"libro"}}'
                ),
                encoding="utf-8",
            )

            payload = build_theme_payload(book_dir=book_dir, generated_at="2026-07-10T00:00:00+00:00")

            self.assertEqual(payload["status"], "pendiente")
            self.assertEqual(payload["themes"], [])

    def test_write_themes_json_updates_obsidian_section(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            book_dir = Path(tmpdir) / "books" / "division-demo"
            book_dir.mkdir(parents=True)
            (book_dir / "book.json").write_text(
                (
                    '{"book_id":"division-demo","page_count":12,'
                    '"bibliographic":{"title":"División de polinomios","material_type":"libro"}}'
                ),
                encoding="utf-8",
            )
            (book_dir / "obsidian.md").write_text("# Division\n\nNotas previas.\n", encoding="utf-8")

            payload = write_themes_json(book_dir=book_dir, generated_at="2026-07-10T00:00:00+00:00")
            note = (book_dir / "obsidian.md").read_text(encoding="utf-8")

            self.assertEqual(payload["status"], "tema_unico")
            self.assertTrue((book_dir / "themes.json").exists())
            self.assertIn("## Temas estructurados", note)
            self.assertIn("División de polinomios", note)


if __name__ == "__main__":
    unittest.main()
