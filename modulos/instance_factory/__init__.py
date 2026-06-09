"""PDF-to-staging factory for book instances."""

from .models import InstancePipelineContext, PipelineStep, StageStatus
from .staging import InstanceStagingStore

__all__ = [
    "InstancePipelineContext",
    "InstancePdfPipelineService",
    "InstanceStagingStore",
    "PipelineStep",
    "StageStatus",
]


def __getattr__(name: str):
    if name == "InstancePdfPipelineService":
        from .pipeline import InstancePdfPipelineService

        return InstancePdfPipelineService
    raise AttributeError(name)
