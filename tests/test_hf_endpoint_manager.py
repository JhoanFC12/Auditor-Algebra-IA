from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from modulos.instance_factory.hf_endpoint_manager import HfEndpointManager, MANAGE_ENDPOINT_PERMISSION_HINT


class FakeEndpoint:
    def __init__(self, *, name: str = "math-ocr", status: str = "running", url: str = "https://example.endpoints.huggingface.cloud") -> None:
        self.name = name
        self.status = status
        self.url = url
        self.repository = "Jhoan12/math-ocr"
        self.namespace = "Jhoan12"
        self.resume_called = False
        self.wait_called = False
        self.scale_to_zero_called = False

    def resume(self, running_ok: bool = True):
        self.resume_called = True
        self.status = "running"
        return self

    def wait(self, timeout=None, refresh_every=5):
        self.wait_called = True
        self.status = "running"
        return self

    def scale_to_zero(self):
        self.scale_to_zero_called = True
        self.status = "scaledToZero"
        return self


class FakeApi:
    def __init__(self, endpoint: FakeEndpoint | None = None, *, error: Exception | None = None) -> None:
        self.endpoint = endpoint or FakeEndpoint()
        self.error = error
        self.token = ""

    def get_inference_endpoint(self, name: str):
        if self.error:
            raise self.error
        if name != self.endpoint.name:
            raise RuntimeError("not found")
        return self.endpoint

    def list_inference_endpoints(self):
        if self.error:
            raise self.error
        return [self.endpoint]


class HfEndpointManagerTests(unittest.TestCase):
    def manager(self, api: FakeApi, root: Path) -> HfEndpointManager:
        def factory(token: str):
            api.token = token
            return api

        return HfEndpointManager(api_factory=factory, env_root=root)

    def test_status_detects_endpoint_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            endpoint = FakeEndpoint(name="math-ocr", status="running")
            api = FakeApi(endpoint)
            with patch.dict(os.environ, {
                "HF_TOKEN": "hf_test",
                "HF_TRAINED_OCR_ENDPOINT_NAME": "math-ocr",
                "HF_TRAINED_OCR_BASE_URL": "",
            }, clear=False):
                status = self.manager(api, root).status()

        self.assertEqual(status["status"], "running")
        self.assertTrue(status["manageable"])
        self.assertEqual(status["name"], "math-ocr")
        self.assertEqual(api.token, "hf_test")

    def test_status_can_match_endpoint_by_url_without_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            endpoint = FakeEndpoint(name="ocr-url", status="scaledToZero", url="https://abc.endpoints.huggingface.cloud")
            api = FakeApi(endpoint)
            with patch.dict(os.environ, {
                "HF_TOKEN": "hf_test",
                "HF_TRAINED_OCR_ENDPOINT_NAME": "",
                "HF_TRAINED_OCR_BASE_URL": "https://abc.endpoints.huggingface.cloud/v1",
            }, clear=False):
                status = self.manager(api, root).status()

        self.assertEqual(status["status"], "scaledToZero")
        self.assertEqual(status["name"], "ocr-url")

    def test_resume_paused_endpoint_waits_until_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            endpoint = FakeEndpoint(status="paused")
            api = FakeApi(endpoint)
            with patch.dict(os.environ, {
                "HF_TOKEN": "hf_test",
                "HF_TRAINED_OCR_ENDPOINT_NAME": "math-ocr",
            }, clear=False):
                status = self.manager(api, root).resume(wait=True, timeout_s=1, poll_s=1)

        self.assertEqual(status["status"], "running")
        self.assertTrue(endpoint.resume_called)
        self.assertTrue(endpoint.wait_called)

    def test_scale_to_zero_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            endpoint = FakeEndpoint(status="running")
            api = FakeApi(endpoint)
            with patch.dict(os.environ, {
                "HF_TOKEN": "hf_test",
                "HF_TRAINED_OCR_ENDPOINT_NAME": "math-ocr",
            }, clear=False):
                status = self.manager(api, root).scale_to_zero()

        self.assertEqual(status["status"], "scaledToZero")
        self.assertTrue(endpoint.scale_to_zero_called)
        self.assertEqual(status["message"], "Endpoint OCR apagado para ahorro.")

    def test_permission_error_returns_actionable_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = FakeApi(error=RuntimeError("403 Forbidden"))
            with patch.dict(os.environ, {
                "HF_TOKEN": "hf_test",
                "HF_TRAINED_OCR_ENDPOINT_NAME": "math-ocr",
            }, clear=False):
                status = self.manager(api, root).status()

        self.assertEqual(status["status"], "error")
        self.assertIn("Manage your Inference Endpoints", status["message"])
        self.assertEqual(status["message"], MANAGE_ENDPOINT_PERMISSION_HINT)


if __name__ == "__main__":
    unittest.main()
