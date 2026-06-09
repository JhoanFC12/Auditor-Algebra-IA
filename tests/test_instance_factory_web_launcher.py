from __future__ import annotations

import unittest
from unittest.mock import patch

from modulos.instance_factory.models import InstancePipelineContext
from modulos.instance_factory import web_launcher


class _Proc:
    def __init__(self, code=None) -> None:
        self.code = code

    def poll(self):
        return self.code


class InstanceFactoryWebLauncherTests(unittest.TestCase):
    def tearDown(self) -> None:
        web_launcher._ACTIVE_PROCESSES.clear()
        web_launcher._ACTIVE_RUNTIMES.clear()

    def test_open_url_uses_browser_when_webview_is_missing(self) -> None:
        with patch.object(web_launcher, "find_spec", return_value=None), patch.object(web_launcher.webbrowser, "open") as browser:
            web_launcher._open_url("http://127.0.0.1:1234/", "Fabrica PDF")

        browser.assert_called_once_with("http://127.0.0.1:1234/", new=2)
        self.assertEqual(web_launcher._ACTIVE_PROCESSES, [])

    def test_open_url_falls_back_to_browser_when_webview_process_cannot_start(self) -> None:
        with (
            patch.object(web_launcher, "find_spec", return_value=object()),
            patch.object(web_launcher.subprocess, "Popen", side_effect=OSError("no backend")),
            patch.object(web_launcher.webbrowser, "open") as browser,
        ):
            web_launcher._open_url("http://127.0.0.1:1234/", "Fabrica PDF")

        browser.assert_called_once_with("http://127.0.0.1:1234/", new=2)
        self.assertEqual(web_launcher._ACTIVE_PROCESSES, [])

    def test_open_url_falls_back_to_browser_when_webview_exits_immediately(self) -> None:
        with (
            patch.object(web_launcher, "find_spec", return_value=object()),
            patch.object(web_launcher.subprocess, "Popen", return_value=_Proc(code=3)),
            patch.object(web_launcher.webbrowser, "open") as browser,
        ):
            web_launcher._open_url("http://127.0.0.1:1234/", "Fabrica PDF")

        browser.assert_called_once_with("http://127.0.0.1:1234/", new=2)
        self.assertEqual(web_launcher._ACTIVE_PROCESSES, [])

    def test_open_url_tracks_running_webview_process_without_browser_fallback(self) -> None:
        proc = _Proc(code=None)
        with (
            patch.object(web_launcher, "find_spec", return_value=object()),
            patch.object(web_launcher.subprocess, "Popen", return_value=proc),
            patch.object(web_launcher.webbrowser, "open") as browser,
        ):
            web_launcher._open_url("http://127.0.0.1:1234/", "Fabrica PDF")

        browser.assert_not_called()
        self.assertEqual(web_launcher._ACTIVE_PROCESSES, [proc])

    def test_open_factory_web_app_still_opens_instance_runtime(self) -> None:
        class Runtime:
            def __init__(self, context) -> None:
                self.context = context

            def start(self) -> str:
                return "http://127.0.0.1:9100/"

        context = InstancePipelineContext(book_code="ALG01", instance_type="S01")
        with (
            patch.object(web_launcher, "FactoryWebRuntime", Runtime),
            patch.object(web_launcher, "_open_url") as open_url,
        ):
            url = web_launcher.open_factory_web_app(context=context)

        self.assertEqual(url, "http://127.0.0.1:9100/")
        open_url.assert_called_once_with("http://127.0.0.1:9100/", "Fabrica PDF - ALG01 / S01")
        self.assertEqual(len(web_launcher._ACTIVE_RUNTIMES), 1)

    def test_open_biblioteca_web_app_uses_runtime_when_available(self) -> None:
        class Runtime:
            def start(self) -> str:
                return "http://127.0.0.1:9200/"

        legacy_calls: list[str] = []
        with (
            patch.object(web_launcher, "_resolve_biblioteca_runtime_class", return_value=Runtime),
            patch.object(web_launcher, "_open_url") as open_url,
        ):
            url = web_launcher.open_biblioteca_web_app(legacy_launcher=lambda: legacy_calls.append("legacy"))

        self.assertEqual(url, "http://127.0.0.1:9200/")
        self.assertEqual(legacy_calls, [])
        open_url.assert_called_once_with("http://127.0.0.1:9200/", "Biblioteca de Libros")
        self.assertEqual(len(web_launcher._ACTIVE_RUNTIMES), 1)

    def test_biblioteca_runtime_resolver_finds_integrated_library_runtime(self) -> None:
        runtime_cls = web_launcher._resolve_biblioteca_runtime_class()

        self.assertIsNotNone(runtime_cls)
        self.assertEqual(runtime_cls.__name__, "LibraryWebRuntime")

    def test_open_biblioteca_web_app_falls_back_when_runtime_is_missing(self) -> None:
        legacy_calls: list[str] = []
        with (
            patch.object(web_launcher, "_resolve_biblioteca_runtime_class", return_value=None),
            patch.object(web_launcher, "_open_url") as open_url,
        ):
            url = web_launcher.open_biblioteca_web_app(legacy_launcher=lambda: legacy_calls.append("legacy"))

        self.assertIsNone(url)
        self.assertEqual(legacy_calls, ["legacy"])
        open_url.assert_not_called()
        self.assertEqual(web_launcher._ACTIVE_RUNTIMES, [])

    def test_open_biblioteca_web_app_falls_back_when_runtime_fails(self) -> None:
        class Runtime:
            def start(self) -> str:
                raise RuntimeError("backend unavailable")

        legacy_calls: list[str] = []
        with (
            patch.object(web_launcher, "_resolve_biblioteca_runtime_class", return_value=Runtime),
            patch.object(web_launcher, "_open_url") as open_url,
        ):
            url = web_launcher.open_biblioteca_web_app(legacy_launcher=lambda: legacy_calls.append("legacy"))

        self.assertIsNone(url)
        self.assertEqual(legacy_calls, ["legacy"])
        open_url.assert_not_called()
        self.assertEqual(web_launcher._ACTIVE_RUNTIMES, [])


if __name__ == "__main__":
    unittest.main()
