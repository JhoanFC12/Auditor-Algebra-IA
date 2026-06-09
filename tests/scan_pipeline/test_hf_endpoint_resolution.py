from __future__ import annotations

import os
import unittest

try:
    from modulos.modulo0_transcriptor.scan_pipeline.extractor import (
        TRAINED_OCR_VISION_MODEL,
        ScanExtractor,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - preexisting optional DB dependency
    if exc.name != "psycopg2":
        raise
    TRAINED_OCR_VISION_MODEL = ""
    ScanExtractor = None  # type: ignore[assignment]


@unittest.skipIf(ScanExtractor is None, "psycopg2 no esta disponible para importar modulo0_transcriptor completo")
class HfEndpointResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {
            key: os.environ.get(key)
            for key in ("HF_TOKEN", "HF_BASE_URL", "HF_TRAINED_OCR_BASE_URL")
        }

    def tearDown(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_trained_ocr_uses_dedicated_endpoint(self) -> None:
        os.environ["HF_TRAINED_OCR_BASE_URL"] = "https://example.endpoint/v1/"
        extractor = ScanExtractor(provider="hf", model=TRAINED_OCR_VISION_MODEL)

        self.assertEqual(
            extractor._resolve_hf_base_url_for_model(TRAINED_OCR_VISION_MODEL),
            "https://example.endpoint/v1",
        )

    def test_trained_ocr_requires_dedicated_endpoint(self) -> None:
        os.environ.pop("HF_TRAINED_OCR_BASE_URL", None)
        extractor = ScanExtractor(provider="hf", model=TRAINED_OCR_VISION_MODEL)

        with self.assertRaisesRegex(RuntimeError, "HF_TRAINED_OCR_BASE_URL"):
            extractor._resolve_hf_base_url_for_model(TRAINED_OCR_VISION_MODEL)

    def test_other_hf_model_uses_generic_base_url(self) -> None:
        os.environ["HF_BASE_URL"] = "https://router.example/v1/"
        extractor = ScanExtractor(provider="hf", model="other/model")

        self.assertEqual(
            extractor._resolve_hf_base_url_for_model("other/model"),
            "https://router.example/v1",
        )


if __name__ == "__main__":
    unittest.main()
