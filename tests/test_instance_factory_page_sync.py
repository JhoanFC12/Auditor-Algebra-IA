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
        self.rows = list(rows)
        return Path(".")


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


if __name__ == "__main__":
    unittest.main()
