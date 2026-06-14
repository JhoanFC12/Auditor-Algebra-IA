from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from modulos.instance_factory.models import InstancePipelineContext
from modulos.instance_factory.pipeline import InstancePdfPipelineService
from modulos.instance_factory.staging import InstanceStagingStore
from modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf import (
    PdfProblemGoldenController,
    ProblemPageRecord,
)


class _FakeGolden:
    def __init__(self, rows: list[ProblemPageRecord]) -> None:
        self.rows = list(rows)
        self.upserted: list[ProblemPageRecord] = []

    def load_instance(self, _name: str) -> list[ProblemPageRecord]:
        return list(self.rows)

    def upsert_instance_rows(self, _name: str, rows: list[ProblemPageRecord]) -> Path:
        self.upserted = list(rows)
        incoming_ids = {str(row.record_id) for row in rows}
        incoming_pages = {int(row.page_number) for row in rows}
        kept = [
            row for row in self.rows
            if str(row.record_id) not in incoming_ids and int(row.page_number) not in incoming_pages
        ]
        self.rows = [*kept, *list(rows)]
        return Path(".")

    def delete_instance_row(self, _name: str, record_id: str) -> list[ProblemPageRecord]:
        self.rows = [row for row in self.rows if str(row.record_id) != str(record_id)]
        return list(self.rows)


class InstanceFactoryPageSyncTests(unittest.TestCase):
    def test_service_exposes_one_record_per_pdf_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "page.png"
            image.write_bytes(b"fake")
            rows = [
                ProblemPageRecord("p1_linked_old", "book.pdf", 1, image, [(0, 0, 10, 10)], detector_source="linked_problem_crops_live", reviewed=True),
                ProblemPageRecord("p1_pdf_factory", "book.pdf", 1, image, [(0, 0, 20, 20)], detector_source="pdf_factory:model", reviewed=False),
                ProblemPageRecord("p2", "book.pdf", 2, image, [(0, 0, 30, 30)], reviewed=False),
            ]
            context = InstancePipelineContext(book_code="ALG", instance_type="S1", pdf_path=str(Path(tmp) / "book.pdf"))
            service = InstancePdfPipelineService(
                context,
                golden_controller=_FakeGolden(rows),  # type: ignore[arg-type]
                staging_store=InstanceStagingStore(context, root=Path(tmp) / "staging"),
            )

            pages = service.load_pages()

            self.assertEqual([page.page_number for page in pages], [1, 2])
            self.assertEqual(pages[0].record_id, "p1_pdf_factory")

    def test_upsert_preserves_reviewed_flag_for_detected_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "golden"
            source = Path(tmp) / "page.png"
            Image.new("RGB", (40, 40), "white").save(source)
            controller = PdfProblemGoldenController(golden_root=root)
            old = ProblemPageRecord("linked_old", "book.pdf", 1, source, [(2, 2, 18, 18)], reviewed=True)
            row = ProblemPageRecord("p0001", "book.pdf", 1, source, [(1, 1, 20, 20)], reviewed=False)

            controller.upsert_instance_rows("ALG__S1", [old])
            controller.upsert_instance_rows("ALG__S1", [row])
            loaded = controller.load_instance("ALG__S1")

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].record_id, "p0001")
            self.assertFalse(loaded[0].reviewed)

    def test_delete_instance_row_preserves_remaining_review_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "golden"
            source_1 = Path(tmp) / "page1.png"
            source_2 = Path(tmp) / "page2.png"
            Image.new("RGB", (40, 40), "white").save(source_1)
            Image.new("RGB", (40, 40), "white").save(source_2)
            controller = PdfProblemGoldenController(golden_root=root)
            first = ProblemPageRecord("p0001", "book.pdf", 1, source_1, [(1, 1, 20, 20)], reviewed=False)
            second = ProblemPageRecord("p0002", "book.pdf", 2, source_2, [(2, 2, 22, 22)], reviewed=True)

            controller.upsert_instance_rows("ALG__S1", [first, second])
            remaining = controller.delete_instance_row("ALG__S1", "p0001")
            loaded = controller.load_instance("ALG__S1")

            self.assertEqual([row.record_id for row in remaining], ["p0002"])
            self.assertEqual([row.record_id for row in loaded], ["p0002"])
            self.assertTrue(loaded[0].reviewed)
            self.assertFalse((root / "ALG__S1" / "records" / "p0001.json").exists())
            self.assertFalse((root / "ALG__S1" / "pages_png" / "p0001.png").exists())

    def test_update_page_boxes_upserts_only_the_changed_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "page.png"
            Image.new("RGB", (80, 80), "white").save(image)
            rows = [
                ProblemPageRecord("p0001", "book.pdf", 1, image, [(1, 1, 20, 20)], reviewed=False),
                ProblemPageRecord("p0002", "book.pdf", 2, image, [(2, 2, 22, 22)], reviewed=False),
                ProblemPageRecord("p0003", "book.pdf", 3, image, [(3, 3, 23, 23)], reviewed=False),
            ]
            golden = _FakeGolden(rows)
            context = InstancePipelineContext(book_code="ALG", instance_type="S1", pdf_path=str(Path(tmp) / "book.pdf"))
            service = InstancePdfPipelineService(
                context,
                golden_controller=golden,  # type: ignore[arg-type]
                staging_store=InstanceStagingStore(context, root=Path(tmp) / "staging"),
            )

            service.update_page_boxes("p0002", [[4, 4, 30, 30]], layout_mode="auto", reviewed=True)

            self.assertEqual([row.record_id for row in golden.upserted], ["p0002"])
            self.assertEqual(sorted(row.record_id for row in golden.rows), ["p0001", "p0002", "p0003"])
            updated = next(row for row in golden.rows if row.record_id == "p0002")
            self.assertEqual(updated.boxes, [(4, 4, 30, 30)])
            self.assertTrue(updated.reviewed)


if __name__ == "__main__":
    unittest.main()
