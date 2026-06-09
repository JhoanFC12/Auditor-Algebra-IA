from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class PipelineStage(str, Enum):
    SEGMENT = "segment"
    OCR_EXTRACT = "ocr_extract"
    OCR_PARSE = "ocr_parse"
    METADATA_CLASSIFY = "metadata_classify"
    REASONING_PASS = "reasoning_pass"
    FORMAT_PASS = "format_pass"
    KEYS_APPLY = "keys_apply"
    PERSIST = "persist"


@dataclass
class PipelineStageResult:
    stage: PipelineStage
    ok: bool
    details: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


@dataclass
class PipelineExecutionPlan:
    stages: List[PipelineStage] = field(default_factory=list)

    def append(self, stage: PipelineStage) -> None:
        self.stages.append(stage)
