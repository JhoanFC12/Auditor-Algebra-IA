from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modulos.book_catalog_sync import SyncOptions, run_catalog_sync


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class BookCatalogSyncTests(unittest.TestCase):
    def test_run_catalog_sync_derives_instances_and_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_root = root / ".cache" / "book_catalog"
            sync_root = root / ".cache" / "book_catalog_sync"
            pdf_path = root / "fuente" / "geometry.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(b"%PDF-1.4")

            book_id = "geometry-book-123"
            (output_root / "inventory.jsonl").parent.mkdir(parents=True, exist_ok=True)
            (output_root / "inventory.jsonl").write_text(
                json.dumps(
                    {
                        "book_id": book_id,
                        "pdf_path": str(pdf_path),
                        "pdf_relpath": "Geometria/geometry.pdf",
                        "pdf_hash_sha256": "hash-123",
                        "page_count": 18,
                        "material_type": "libro",
                        "bibliographic_title": "Geometria Base",
                        "bibliographic_author": "Autor Uno",
                        "bibliographic_editorial": "Editorial X",
                        "bibliographic_collection": "",
                        "bibliographic_status": "reviewed",
                        "source_root": str(root / "fuente"),
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            _write_json(
                output_root / "books" / book_id / "book.json",
                {
                    "book_id": book_id,
                    "bibliographic": {
                        "title": "Geometria Base",
                        "author": "Autor Uno",
                        "editorial": "Editorial X",
                    },
                },
            )
            _write_json(
                output_root / "books" / book_id / "ranges.json",
                {
                    "ranges": [
                        {"label": "portada", "start_page": 1, "end_page": 1, "pages_total": 1},
                        {"label": "teoria", "start_page": 2, "end_page": 5, "pages_total": 4},
                        {"label": "problemas_propuestos", "start_page": 6, "end_page": 12, "pages_total": 7},
                    ]
                },
            )
            _write_json(
                output_root / "books" / book_id / "themes.json",
                {
                    "instance_semantics": "section_from_index",
                    "themes": [
                        {
                            "theme_name": "Rectas y angulos",
                            "start_page": 2,
                            "end_page": 12,
                            "segments": [
                                {"label": "teoria", "start_page": 2, "end_page": 5},
                                {"label": "problemas_propuestos", "start_page": 6, "end_page": 12},
                            ],
                        }
                    ]
                },
            )
            (output_root / "books" / book_id / "pages").mkdir(parents=True, exist_ok=True)
            (output_root / "books" / book_id / "pages" / "page-0001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            course_note = output_root / "Cursos" / "Geometria" / f"{book_id}.md"
            course_note.parent.mkdir(parents=True, exist_ok=True)
            course_note.write_text("# Nota", encoding="utf-8")
            (output_root / "books" / book_id / "obsidian.md").write_text("# Obsidian", encoding="utf-8")

            with patch("modulos.book_catalog_sync.BookProgressController.listar_bases_datos", return_value=[]):
                report = run_catalog_sync(SyncOptions(output_root=output_root, sync_root=sync_root))

            self.assertEqual(report["backend"]["status"], "unavailable")
            self.assertEqual(report["summary"]["books_detected"], 1)
            self.assertEqual(report["summary"]["books_new"], 1)
            self.assertEqual(report["summary"]["instances_to_create"], 1)
            instance = report["books"][0]["instances"][0]
            self.assertEqual(instance["payload"]["config_snapshot"]["instance_semantics"], "section_from_index")
            self.assertEqual(instance["payload"]["config_snapshot"]["selected_content_label"], "problemas_propuestos")
            self.assertEqual(instance["payload"]["config_snapshot"]["page_start"], 6)
            self.assertEqual(instance["payload"]["config_snapshot"]["page_end"], 12)
            self.assertEqual(instance["payload"]["config_snapshot"]["theme_page_start"], 2)
            self.assertEqual(instance["payload"]["config_snapshot"]["theme_page_end"], 12)
            self.assertEqual(instance["payload"]["config_snapshot"]["selected_page_ranges_display"], "pp. 6-12")
            self.assertIn('"instance_semantics": "section_from_index"', instance["payload"]["notas"])
            self.assertIn("Paginas operativas (problemas_propuestos): pp. 6-12.", instance["payload"]["notas"])
            self.assertTrue((sync_root / "sync_plan.json").exists())
            self.assertTrue((sync_root / "sync_report.md").exists())
            imported = (sync_root / "imported_books.jsonl").read_text(encoding="utf-8")
            self.assertIn(book_id, imported)

    def test_run_catalog_sync_excludes_exams_and_unreviewed_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_root = root / ".cache" / "book_catalog"
            sync_root = root / ".cache" / "book_catalog_sync"
            pdf1 = root / "fuente" / "exam.pdf"
            pdf2 = root / "fuente" / "draft.pdf"
            pdf1.parent.mkdir(parents=True, exist_ok=True)
            pdf1.write_bytes(b"%PDF-1.4")
            pdf2.write_bytes(b"%PDF-1.4")

            exam_id = "exam-book-1"
            draft_id = "draft-book-2"
            inventory_lines = [
                {
                    "book_id": exam_id,
                    "pdf_path": str(pdf1),
                    "pdf_relpath": "Examenes/exam.pdf",
                    "pdf_hash_sha256": "hash-exam",
                    "page_count": 10,
                    "material_type": "examen_concurso",
                    "bibliographic_title": "Examen",
                    "bibliographic_author": "",
                    "bibliographic_editorial": "",
                    "bibliographic_collection": "",
                    "bibliographic_status": "reviewed",
                    "source_root": str(root / "fuente"),
                },
                {
                    "book_id": draft_id,
                    "pdf_path": str(pdf2),
                    "pdf_relpath": "Geometria/draft.pdf",
                    "pdf_hash_sha256": "hash-draft",
                    "page_count": 10,
                    "material_type": "libro",
                    "bibliographic_title": "Borrador",
                    "bibliographic_author": "",
                    "bibliographic_editorial": "",
                    "bibliographic_collection": "",
                    "bibliographic_status": "pending_review",
                    "source_root": str(root / "fuente"),
                },
            ]
            (output_root / "inventory.jsonl").parent.mkdir(parents=True, exist_ok=True)
            (output_root / "inventory.jsonl").write_text(
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in inventory_lines),
                encoding="utf-8",
            )
            for book_id in (exam_id, draft_id):
                _write_json(output_root / "books" / book_id / "book.json", {"book_id": book_id, "bibliographic": {"title": book_id}})
                _write_json(output_root / "books" / book_id / "ranges.json", {"ranges": [{"label": "dudosa", "start_page": 1, "end_page": 3, "pages_total": 3}]})
                (output_root / "books" / book_id / "pages").mkdir(parents=True, exist_ok=True)
                (output_root / "books" / book_id / "pages" / "page-0001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
                (output_root / "books" / book_id / "obsidian.md").write_text("# Obsidian", encoding="utf-8")
            (output_root / "Cursos" / "Examenes y Concursos" / f"{exam_id}.md").parent.mkdir(parents=True, exist_ok=True)
            (output_root / "Cursos" / "Examenes y Concursos" / f"{exam_id}.md").write_text("# Exam", encoding="utf-8")
            (output_root / "Cursos" / "Geometria" / f"{draft_id}.md").parent.mkdir(parents=True, exist_ok=True)
            (output_root / "Cursos" / "Geometria" / f"{draft_id}.md").write_text("# Draft", encoding="utf-8")

            with patch("modulos.book_catalog_sync.BookProgressController.listar_bases_datos", return_value=[]):
                report = run_catalog_sync(SyncOptions(output_root=output_root, sync_root=sync_root))

            self.assertEqual(report["summary"]["books_detected"], 2)
            self.assertEqual(report["summary"]["books_excluded"], 1)
            self.assertEqual(report["summary"]["books_without_actionable_ranges"], 1)
            self.assertEqual(report["summary"]["books_without_actionable_topics"], 1)
            conflicts = (sync_root / "conflicts.jsonl").read_text(encoding="utf-8")
            self.assertIn(exam_id, conflicts)
            self.assertIn(draft_id, conflicts)

    def test_run_catalog_sync_excludes_consulta_books_even_with_themes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_root = root / ".cache" / "book_catalog"
            sync_root = root / ".cache" / "book_catalog_sync"
            pdf_path = root / "fuente" / "consulta.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(b"%PDF-1.4")

            book_id = "consulta-book-1"
            (output_root / "inventory.jsonl").parent.mkdir(parents=True, exist_ok=True)
            (output_root / "inventory.jsonl").write_text(
                json.dumps(
                    {
                        "book_id": book_id,
                        "pdf_path": str(pdf_path),
                        "pdf_relpath": "Algebra/consulta.pdf",
                        "pdf_hash_sha256": "hash-consulta",
                        "page_count": 12,
                        "material_type": "consulta",
                        "bibliographic_title": "Libro de consulta",
                        "bibliographic_author": "",
                        "bibliographic_editorial": "",
                        "bibliographic_collection": "",
                        "bibliographic_status": "reviewed",
                        "source_root": str(root / "fuente"),
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            _write_json(output_root / "books" / book_id / "book.json", {"book_id": book_id, "bibliographic": {"title": "Libro de consulta"}})
            _write_json(output_root / "books" / book_id / "ranges.json", {"ranges": []})
            _write_json(
                output_root / "books" / book_id / "themes.json",
                {
                    "themes": [
                        {
                            "theme_name": "Tema teorico",
                            "start_page": 1,
                            "end_page": 12,
                            "segments": [{"label": "teoria", "start_page": 1, "end_page": 12}],
                        }
                    ]
                },
            )
            (output_root / "books" / book_id / "obsidian.md").write_text("# Obsidian", encoding="utf-8")
            course_note = output_root / "Cursos" / "Algebra" / f"{book_id}.md"
            course_note.parent.mkdir(parents=True, exist_ok=True)
            course_note.write_text("# Consulta", encoding="utf-8")

            with patch("modulos.book_catalog_sync.BookProgressController.listar_bases_datos", return_value=[]):
                report = run_catalog_sync(SyncOptions(output_root=output_root, sync_root=sync_root))

            self.assertEqual(report["summary"]["books_detected"], 1)
            self.assertEqual(report["summary"]["books_excluded"], 1)
            self.assertEqual(report["summary"]["instances_to_create"], 0)
            conflicts = (sync_root / "conflicts.jsonl").read_text(encoding="utf-8")
            self.assertIn("libro de consulta", conflicts)

    def test_run_catalog_sync_blocks_duplicate_existing_book_by_workspace_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_root = root / ".cache" / "book_catalog"
            sync_root = root / ".cache" / "book_catalog_sync"
            pdf_path = root / "fuente" / "Coleccion" / "Libro Uno.pdf"
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(b"%PDF-1.4")

            book_id = "libro-uno-a1b2c3d4e5"
            (output_root / "inventory.jsonl").parent.mkdir(parents=True, exist_ok=True)
            (output_root / "inventory.jsonl").write_text(
                json.dumps(
                    {
                        "book_id": book_id,
                        "pdf_path": str(pdf_path),
                        "pdf_relpath": "Algebra/Libro Uno.pdf",
                        "pdf_hash_sha256": "hash-uno",
                        "page_count": 20,
                        "material_type": "libro",
                        "bibliographic_title": "Libro Uno",
                        "bibliographic_author": "Autor Uno",
                        "bibliographic_editorial": "",
                        "bibliographic_collection": "",
                        "bibliographic_status": "reviewed",
                        "source_root": str(root / "fuente"),
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            _write_json(output_root / "books" / book_id / "book.json", {"book_id": book_id, "bibliographic": {"title": "Libro Uno"}})
            _write_json(output_root / "books" / book_id / "ranges.json", {"ranges": []})
            (output_root / "books" / book_id / "obsidian.md").write_text("# Obsidian", encoding="utf-8")
            (output_root / "Cursos" / "Algebra" / f"{book_id}.md").parent.mkdir(parents=True, exist_ok=True)
            (output_root / "Cursos" / "Algebra" / f"{book_id}.md").write_text("# Nota", encoding="utf-8")

            existing_workspace = str(pdf_path.parent / "libro-uno")
            existing_rows = [
                {
                    "id": 99,
                    "codigo": "legacy-libro-uno",
                    "titulo": "Libro Uno Historico",
                    "autor": "Autor Uno",
                    "curso": "Algebra",
                    "notas": "",
                    "workspace_dir": existing_workspace,
                    "workspace_dir_server": existing_workspace,
                    "pdf_path": str(root / "mirror" / "source.pdf"),
                }
            ]

            with patch("modulos.book_catalog_sync.BookProgressController.listar_bases_datos", return_value=["demo_db"]), patch(
                "modulos.book_catalog_sync.BookProgressController.listar_libros",
                return_value=existing_rows,
            ), patch(
                "modulos.book_catalog_sync.BookProgressController.listar_instancias_todos",
                return_value={99: [{"id": 1, "tipo": "s1_teoria_de_exponentes", "notas": ""}]},
            ):
                report = run_catalog_sync(SyncOptions(output_root=output_root, sync_root=sync_root, db_name="demo_db"))

            self.assertEqual(report["summary"]["books_conflict"], 1)
            conflicts = (sync_root / "conflicts.jsonl").read_text(encoding="utf-8")
            self.assertIn("misma huella de origen", conflicts)
            self.assertIn("legacy-libro-uno", conflicts)


if __name__ == "__main__":
    unittest.main()
