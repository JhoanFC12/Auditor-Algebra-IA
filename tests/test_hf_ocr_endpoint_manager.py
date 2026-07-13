from __future__ import annotations

import unittest

from modulos.instance_factory.hf_endpoint_manager import call_with_hf_ocr_retry


class HfOcrEndpointManagerTests(unittest.TestCase):
    def test_retry_helper_retries_cold_start_errors(self) -> None:
        attempts = {"count": 0}
        sleeps: list[float] = []
        events: list[dict] = []

        def flaky_call() -> str:
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("503 Service Unavailable")
            return "ocr listo"

        result = call_with_hf_ocr_retry(
            flaky_call,
            max_attempts=3,
            sleep_func=sleeps.append,
            status_callback=events.append,
        )

        self.assertEqual(result, "ocr listo")
        self.assertEqual(attempts["count"], 3)
        self.assertEqual(sleeps, [8.0, 15.0])
        self.assertEqual(events[0]["event"], "hf_ocr_cold_start_retry")
        self.assertEqual(events[-1]["attempt"], 2)

    def test_retry_helper_does_not_retry_non_cold_start_errors(self) -> None:
        attempts = {"count": 0}

        def forbidden_call() -> str:
            attempts["count"] += 1
            raise RuntimeError("403 Forbidden")

        with self.assertRaises(RuntimeError):
            call_with_hf_ocr_retry(
                forbidden_call,
                max_attempts=4,
                sleep_func=lambda _seconds: None,
            )

        self.assertEqual(attempts["count"], 1)


if __name__ == "__main__":
    unittest.main()
