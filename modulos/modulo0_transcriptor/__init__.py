from __future__ import annotations

from typing import Any


__all__ = [
    "InsertResult",
    "PersistableItem",
    "TranscriptorController",
    "TranscriptorSessionState",
    "TranscriptorWorkflow",
]


def __getattr__(name: str) -> Any:
    if name in {"InsertResult", "PersistableItem", "TranscriptorController"}:
        from .controlador_transcriptor import InsertResult, PersistableItem, TranscriptorController

        return {
            "InsertResult": InsertResult,
            "PersistableItem": PersistableItem,
            "TranscriptorController": TranscriptorController,
        }[name]
    if name == "TranscriptorSessionState":
        from .state import TranscriptorSessionState

        return TranscriptorSessionState
    if name == "TranscriptorWorkflow":
        from .workflow.transcriptor_workflow import TranscriptorWorkflow

        return TranscriptorWorkflow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
