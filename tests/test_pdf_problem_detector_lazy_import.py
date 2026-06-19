import subprocess
import sys
import unittest


class PdfProblemDetectorLazyImportTests(unittest.TestCase):
    def test_importing_detector_controller_does_not_load_yolo_runtime(self) -> None:
        code = (
            "import sys\n"
            "import modulos.modulo13_laboratorio_pdf_segmentacion.controlador_laboratorio_pdf\n"
            "assert 'ultralytics' not in sys.modules, 'ultralytics loaded during module import'\n"
            "assert 'huggingface_hub' not in sys.modules, 'huggingface_hub loaded during module import'\n"
            "print('ok')\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
