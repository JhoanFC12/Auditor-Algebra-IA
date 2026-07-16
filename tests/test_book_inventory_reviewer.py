from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from modulos.book_inventory_reviewer.server import ReviewerStore


class ReviewerStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.catalog = root / "catalog.csv"
        with self.catalog.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=["drive_id", "title", "course", "course_scope", "url"],
            )
            writer.writeheader()
            writer.writerow(
                {
                    "drive_id": "abc123",
                    "title": "Algebra de prueba.pdf",
                    "course": "Algebra",
                    "course_scope": "un_curso",
                    "url": "https://drive.google.com/file/d/abc123/view",
                }
            )
        self.store = ReviewerStore(
            catalog_path=self.catalog,
            state_path=root / "reviews.json",
            export_root=root / "exports",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_snapshot_and_review_persistence(self) -> None:
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["total"], 1)
        self.assertEqual(snapshot["items"][0]["review_state"], "pendiente")
        self.assertTrue(snapshot["items"][0]["preview_url"].endswith("/preview"))

        self.store.save_decision(
            "abc123",
            {
                "review_state": "confirmado",
                "confirmed_course": "Algebra",
                "material_type": "libro_problemas",
                "multiple_choice": "si",
                "notes": "Confirmado visualmente",
            },
        )
        reviewed = self.store.snapshot()["items"][0]
        self.assertEqual(reviewed["review_state"], "confirmado")
        self.assertEqual(reviewed["notes"], "Confirmado visualmente")

    def test_paginated_query_filters_catalog(self) -> None:
        result = self.store.query_catalog(page=1, page_size=25, course="Algebra", review_state="pendiente")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["total_filtered"], 1)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["pages"], 1)
        self.assertEqual(result["items"][0]["id"], "abc123")

    def test_unclassified_item_can_be_excluded(self) -> None:
        self.store._catalog_by_id["abc123"]["course"] = "Pendiente"
        decision = self.store.save_decision(
            "abc123",
            {
                "review_state": "excluido",
                "confirmed_course": "Pendiente",
                "material_type": "otro",
                "multiple_choice": "no",
            },
        )
        self.assertEqual(decision["review_state"], "excluido")

    def test_export_creates_course_shortcut(self) -> None:
        self.store.save_decision(
            "abc123",
            {
                "review_state": "confirmado",
                "confirmed_course": "Algebra",
                "material_type": "libro_problemas",
                "multiple_choice": "si",
            },
        )
        manifest = self.store.export()
        self.assertEqual(manifest["total"], 1)
        algebra = self.store.export_root / "Algebra"
        self.assertTrue((algebra / "catalogo.csv").exists())
        self.assertEqual(len(list(algebra.glob("*.url"))), 1)


if __name__ == "__main__":
    unittest.main()
