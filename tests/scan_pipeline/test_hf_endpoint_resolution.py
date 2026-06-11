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

    def test_trained_ocr_rejects_router_endpoint(self) -> None:
        os.environ["HF_TRAINED_OCR_BASE_URL"] = "https://router.huggingface.co/v1"
        extractor = ScanExtractor(provider="hf", model=TRAINED_OCR_VISION_MODEL)

        with self.assertRaisesRegex(RuntimeError, "router de Hugging Face Inference Providers"):
            extractor._resolve_hf_base_url_for_model(TRAINED_OCR_VISION_MODEL)

    def test_other_hf_model_uses_generic_base_url(self) -> None:
        os.environ["HF_BASE_URL"] = "https://router.example/v1/"
        extractor = ScanExtractor(provider="hf", model="other/model")

        self.assertEqual(
            extractor._resolve_hf_base_url_for_model("other/model"),
            "https://router.example/v1",
        )

    def test_hf_inference_provider_403_gets_friendly_message(self) -> None:
        os.environ["HF_BASE_URL"] = "https://router.huggingface.co/v1/"
        extractor = ScanExtractor(provider="hf", model="other/model")
        err = Exception(
            "Error code: 403 - {'error': 'This authentication method does not have "
            "sufficient permissions to call Inference Providers on behalf of user Jhoan12'}"
        )

        message = extractor._friendly_hf_runtime_error(err, model="other/model")

        self.assertIn("Hugging Face 403", message)
        self.assertIn("Make calls to Inference Providers", message)
        self.assertIn("router.huggingface.co/v1", message)


if __name__ == "__main__":
    unittest.main()
