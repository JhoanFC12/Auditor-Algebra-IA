from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from modulos.instance_factory.runtime_env import load_factory_runtime_env


class InstanceFactoryRuntimeEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env = {
            key: os.environ.get(key)
            for key in (
                "HF_TOKEN",
                "HUGGINGFACEHUB_API_TOKEN",
                "HF_BASE_URL",
                "HF_TRAINED_OCR_BASE_URL",
                "HF_ENDPOINT_START_TIMEOUT",
            )
        }

    def tearDown(self) -> None:
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_env_local_overrides_placeholder_hf_token_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "HF_TOKEN=hf_xxx_replace_me",
                        "HF_BASE_URL=https://router.huggingface.co/v1",
                    ]
                ),
                encoding="utf-8",
            )
            (root / ".env.local").write_text(
                "\n".join(
                    [
                        "HF_TOKEN=hf_real_token_for_test",
                        "HF_TRAINED_OCR_BASE_URL=https://example.endpoints.huggingface.cloud/v1",
                        "HF_ENDPOINT_START_TIMEOUT=300",
                    ]
                ),
                encoding="utf-8",
            )
            os.environ["HF_TOKEN"] = "hf_xxx_replace_me"
            os.environ.pop("HUGGINGFACEHUB_API_TOKEN", None)

            load_factory_runtime_env(root)

            self.assertEqual(os.environ["HF_TOKEN"], "hf_real_token_for_test")
            self.assertEqual(os.environ["HUGGINGFACEHUB_API_TOKEN"], "hf_real_token_for_test")
            self.assertEqual(
                os.environ["HF_TRAINED_OCR_BASE_URL"],
                "https://example.endpoints.huggingface.cloud/v1",
            )
            self.assertEqual(os.environ["HF_ENDPOINT_START_TIMEOUT"], "300")


if __name__ == "__main__":
    unittest.main()
