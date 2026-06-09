from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from ..state import TranscriptorSessionState


@dataclass
class GuardResult:
    ok: bool
    errors: List[str] = field(default_factory=list)


def require_sources(state: TranscriptorSessionState) -> GuardResult:
    if state.source_images:
        return GuardResult(ok=True)
    return GuardResult(ok=False, errors=["No hay imagenes cargadas."])


def require_items(state: TranscriptorSessionState) -> GuardResult:
    if state.items:
        return GuardResult(ok=True)
    return GuardResult(ok=False, errors=["No hay items estructurados."])


def require_output(state: TranscriptorSessionState) -> GuardResult:
    if (state.output_text or "").strip():
        return GuardResult(ok=True)
    return GuardResult(ok=False, errors=["No hay salida renderizada."])


def require_db_name(db_name: str) -> GuardResult:
    if (db_name or "").strip():
        return GuardResult(ok=True)
    return GuardResult(ok=False, errors=["No hay base de datos seleccionada."])
