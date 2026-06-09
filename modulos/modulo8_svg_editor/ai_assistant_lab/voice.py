from __future__ import annotations

from collections.abc import Callable
import threading


class VoiceUnavailable(RuntimeError):
    pass


def voice_status() -> tuple[bool, str]:
    try:
        import speech_recognition as sr  # type: ignore[import-not-found]
    except ImportError:
        return False, "Falta SpeechRecognition."
    try:
        sr.Microphone.list_microphone_names()
    except Exception as exc:  # noqa: BLE001 - PyAudio/device failures are environment dependent.
        return False, f"No se pudo acceder al microfono/PyAudio: {exc}"
    return True, "Entrada por voz disponible."


def listen_once(language: str = "es-PE", timeout: int = 5, phrase_time_limit: int = 12) -> str:
    """Optional speech input.

    This module intentionally has no hard dependency. If SpeechRecognition is not
    installed, the GUI can keep working and show a friendly message.
    """
    try:
        import speech_recognition as sr  # type: ignore[import-not-found]
    except ImportError as exc:
        raise VoiceUnavailable(
            "Entrada por voz no disponible. Instala SpeechRecognition y PyAudio para activarla."
        ) from exc

    recognizer = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.4)
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
    except Exception as exc:  # noqa: BLE001 - optional voice backend can fail in many ways.
        raise VoiceUnavailable(f"No pude escuchar desde el microfono: {exc}") from exc

    try:
        return recognizer.recognize_google(audio, language=language)
    except Exception as exc:  # noqa: BLE001
        raise VoiceUnavailable(f"No pude reconocer la voz: {exc}") from exc


class ContinuousVoiceListener:
    def __init__(
        self,
        *,
        language: str = "es-PE",
        on_text: Callable[[str], None],
        on_error: Callable[[str], None],
        on_status: Callable[[str], None],
        on_done: Callable[[], None] | None = None,
    ) -> None:
        self.language = language
        self.on_text = on_text
        self.on_error = on_error
        self.on_status = on_status
        self.on_done = on_done
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        try:
            import speech_recognition as sr  # type: ignore[import-not-found]
        except ImportError as exc:
            self.on_error("Falta SpeechRecognition.")
            raise VoiceUnavailable("Falta SpeechRecognition.") from exc

        recognizer = sr.Recognizer()
        recognizer.pause_threshold = 1.8
        recognizer.non_speaking_duration = 0.8
        recognizer.dynamic_energy_threshold = True

        try:
            microphone = sr.Microphone()
        except Exception as exc:  # noqa: BLE001
            self.on_error(f"No se pudo abrir el microfono: {exc}")
            return

        with microphone as source:
            try:
                self.on_status("Calibrando microfono...")
                recognizer.adjust_for_ambient_noise(source, duration=0.5)
            except Exception as exc:  # noqa: BLE001
                self.on_error(f"No se pudo calibrar el microfono: {exc}")
                return

            self.on_status("Dictado activo. Habla y pulsa Detener para terminar.")
            while not self._stop_event.is_set():
                try:
                    audio = recognizer.listen(source, timeout=1.0, phrase_time_limit=25)
                except sr.WaitTimeoutError:
                    continue
                except Exception as exc:  # noqa: BLE001
                    self.on_error(f"No pude escuchar: {exc}")
                    continue

                try:
                    text = recognizer.recognize_google(audio, language=self.language)
                except sr.UnknownValueError:
                    self.on_status("No entendi ese fragmento; sigo escuchando.")
                    continue
                except Exception as exc:  # noqa: BLE001
                    self.on_error(f"No pude reconocer el fragmento: {exc}")
                    continue
                if text.strip():
                    self.on_text(text.strip())
                if self._stop_event.is_set():
                    break

        self.on_status("Dictado detenido.")
        if self.on_done:
            self.on_done()
