from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modulos.instance_factory.normalizer_inference import HfOcrNormalizerClient


class HfOcrNormalizerClientTests(unittest.TestCase):
    def test_prefer_local_uses_local_generation_without_hf_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / "base"
            adapter_dir = root / "adapter"
            base_dir.mkdir()
            adapter_dir.mkdir()
            env = {
                "HF_TOKEN": "",
                "HUGGINGFACEHUB_API_TOKEN": "",
                "HF_OCR_NORMALIZER_PREFER_LOCAL": "1",
                "HF_OCR_NORMALIZER_BASE_MODEL_LOCAL_DIR": str(base_dir),
                "HF_OCR_NORMALIZER_LOCAL_DIR": str(adapter_dir),
            }
            expected = {
                "schema_version": "local_ocr_normalizer_prediction_v1",
                "base_url": "local",
                "final_latex": "\\item test",
            }
            with patch.dict(os.environ, env, clear=False):
                client = HfOcrNormalizerClient(model="Jhoan12/test-normalizer")
                with patch.object(
                    HfOcrNormalizerClient,
                    "_generate_final_latex_local",
                    return_value=expected,
                ) as local_generate:
                    result = client.generate_final_latex({"record_id": "r1"})

            self.assertEqual(result, expected)
            local_generate.assert_called_once_with({"record_id": "r1"})


if __name__ == "__main__":
    unittest.main()
