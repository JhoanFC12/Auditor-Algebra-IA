from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .models import InstancePipelineContext


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASETS_ROOT = REPO_ROOT / ".cache" / "transcriptor_runs" / "datasets"
SCHEMA_VERSION = "pdf_factory_training_cycle_status_v1"
STATE_SCHEMA_VERSION = "pdf_factory_training_cycle_state_v1"
DEFAULT_TARGET_SAMPLES = 500


@dataclass(frozen=True)
class TrainingBankSpec:
    key: str
    label: str
    model_stage: str
    bank_kind: str
    sample_unit: str
    default_dirs: tuple[str, ...]
    root_env: str
    target_env: str
    count_keys: tuple[str, ...]
    fallback_dirs: tuple[str, ...] = ()
    recursive_manifest_scan: bool = False
    count_policy: str = ""
    train_action: str = ""


TRAINING_BANKS: tuple[TrainingBankSpec, ...] = (
    TrainingBankSpec(
        key="problem_detector",
        label="Segmentacion de problemas",
        model_stage="pdf_detector",
        bank_kind="problem_detector_corrections",
        sample_unit="pagina corregida",
        default_dirs=("problem_detector_corrections_live", "pdf_problem_boxes_live"),
        root_env="PDF_PROBLEM_DETECTOR_CORRECTIONS_ROOT",
        target_env="PDF_PROBLEM_DETECTOR_TRAINING_TARGET",
        count_keys=("samples_total", "pages_total", "records_total"),
        recursive_manifest_scan=True,
        count_policy="Cuenta paginas/boxes de problemas con correccion humana o golden base confirmada.",
        train_action="export_pdf_problem_boxes_yolo_and_retrain_detector",
    ),
    TrainingBankSpec(
        key="ocr_raw",
        label="OCR crudo",
        model_stage="ocr",
        bank_kind="ocr_golden",
        sample_unit="crop corregido",
        default_dirs=("ocr_golden_live", "ocr_geometry_golden_live"),
        root_env="OCR_TRAINING_BANK_ROOTS",
        target_env="OCR_TRAINING_TARGET",
        count_keys=("records_corrected", "samples_total", "records_confirmed", "records_total"),
        count_policy="Cuenta solo OCR corregido/revisado para entrenar transcripcion fiel.",
        train_action="prepare_local_ocr_lab_dataset_or_hf_ocr_training",
    ),
    TrainingBankSpec(
        key="figure_segmenter",
        label="Segmentacion de graficos",
        model_stage="figure_segmenter",
        bank_kind="segment_training_live",
        sample_unit="imagen corregida",
        default_dirs=("segment_training_live",),
        root_env="SEGMENT_LIVE_GOLDEN_BASE",
        target_env="SEGMENT_LIVE_GOLDEN_TARGET_CORRECTED",
        count_keys=("corrected_images", "samples_total", "records_confirmed", "records_total"),
        count_policy="Cuenta imagenes donde el usuario movio, agrego o elimino boxes de graficos.",
        train_action="build_graph_detector_feedback_dataset_and_retrain_yolo",
    ),
    TrainingBankSpec(
        key="normalizer",
        label="Normalizador final",
        model_stage="normalizer",
        bank_kind="normalizer_training_bank",
        sample_unit="problema normalizado",
        default_dirs=("normalizer_training_bank",),
        root_env="NORMALIZER_TRAINING_BANK_ROOT",
        target_env="NORMALIZER_TRAINING_SAMPLE_TARGET",
        count_keys=("samples_total", "records_confirmed", "records_total"),
        count_policy="Cuenta problemas principales con formato final humano; las continuaciones viven dentro del padre.",
        train_action="prepare_normalizer_training_bank_dataset_and_train_sft",
    ),
)


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def default_target_samples() -> int:
    return _positive_int(os.getenv("TRAINING_SAMPLE_TARGET"), DEFAULT_TARGET_SAMPLES)


def _positive_int(raw: Any, default: int) -> int:
    try:
        value = int(str(raw).strip())
    except Exception:
        return int(default)
    return value if value > 0 else int(default)


def _datasets_root(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root).expanduser().resolve()
    configured = str(os.getenv("TRAINING_DATASETS_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_DATASETS_ROOT.expanduser().resolve()


def _cycle_state_path(datasets_root: Path) -> Path:
    configured = str(os.getenv("TRAINING_CYCLE_STATE_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return datasets_root / "training_cycle_state.json"


def _target_for(spec: TrainingBankSpec) -> int:
    return _positive_int(os.getenv(spec.target_env), default_target_samples())


def load_cycle_state(*, root: Path | None = None) -> dict[str, Any]:
    datasets_root = _datasets_root(root)
    path = _cycle_state_path(datasets_root)
    if not path.exists():
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "cycle_id": "",
            "state_path": str(path),
            "started_at": "",
            "reason": "",
            "baselines": {},
        }
    payload = _read_json(path)
    if not payload:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "cycle_id": "",
            "state_path": str(path),
            "started_at": "",
            "reason": "",
            "baselines": {},
        }
    payload["state_path"] = str(path)
    payload.setdefault("schema_version", STATE_SCHEMA_VERSION)
    payload.setdefault("baselines", {})
    return payload


def save_cycle_state(state: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    datasets_root = _datasets_root(root)
    path = _cycle_state_path(datasets_root)
    payload = {
        "schema_version": STATE_SCHEMA_VERSION,
        "cycle_id": str(state.get("cycle_id") or uuid.uuid4().hex[:12]),
        "state_path": str(path),
        "started_at": str(state.get("started_at") or now_text()),
        "reason": str(state.get("reason") or ""),
        "baselines": dict(state.get("baselines") or {}),
        "metadata": dict(state.get("metadata") or {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _paths_from_env(raw: str) -> list[Path]:
    text = str(raw or "").strip()
    if not text:
        return []
    chunks = [chunk.strip() for chunk in text.split(os.pathsep) if chunk.strip()]
    return [Path(chunk).expanduser().resolve() for chunk in chunks]


def _candidate_roots(
    spec: TrainingBankSpec,
    *,
    datasets_root: Path,
    context: InstancePipelineContext | None = None,
) -> list[Path]:
    env_roots = _paths_from_env(os.getenv(spec.root_env, ""))
    roots: list[Path] = list(env_roots) if env_roots else []
    if not roots:
        roots.extend((datasets_root / name).expanduser().resolve() for name in spec.default_dirs)

    if spec.key == "problem_detector" and context is not None:
        try:
            roots.append((context.staging_root().parent / "problem_detector_corrections").expanduser().resolve())
        except Exception:
            pass

    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _manifest_dirs(root: Path, *, recursive: bool) -> list[Path]:
    if not root.exists():
        return []
    direct = root / "manifest.json"
    if direct.exists():
        return [root]
    if not recursive:
        return []
    dirs: list[Path] = []
    for path in sorted(root.rglob("manifest.json"), key=lambda item: str(item).lower()):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        dirs.append(path.parent)
    return dirs


def _jsonl_count(path: Path) -> int:
    if not path.exists() or not path.is_file():
        return 0
    count = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    count += 1
    except Exception:
        return 0
    return count


def _fallback_count(root: Path, spec: TrainingBankSpec) -> int:
    if spec.key == "normalizer":
        return len(list((root / "samples").glob("*.json"))) if (root / "samples").exists() else _jsonl_count(root / "samples.jsonl")
    if spec.key == "ocr_raw":
        corrected = root / "records_corrected.jsonl"
        if corrected.exists():
            return _jsonl_count(corrected)
        return len(list((root / "records").glob("*.json"))) if (root / "records").exists() else 0
    if spec.key == "figure_segmenter":
        corrected = root / "source_records_corrected.jsonl"
        if corrected.exists():
            return _jsonl_count(corrected)
        return len(list((root / "records").glob("*.json"))) if (root / "records").exists() else 0
    if spec.key == "problem_detector":
        metadata_dir = root / "metadata"
        if metadata_dir.exists():
            return len(list(metadata_dir.glob("*.json")))
    return 0


def _sample_count(manifest: dict[str, Any], root: Path, spec: TrainingBankSpec) -> int:
    for key in spec.count_keys:
        value = manifest.get(key)
        try:
            if value is not None:
                return max(0, int(value))
        except Exception:
            continue
    return _fallback_count(root, spec)


def _root_status(root: Path, spec: TrainingBankSpec) -> dict[str, Any] | None:
    manifest_dirs = _manifest_dirs(root, recursive=spec.recursive_manifest_scan)
    if not manifest_dirs and not root.exists():
        return None
    if not manifest_dirs:
        count = _fallback_count(root, spec)
        if count <= 0:
            return {
                "root": str(root),
                "exists": root.exists(),
                "samples_total": 0,
                "manifest_path": "",
                "schema_version": "",
            }
        return {
            "root": str(root),
            "exists": root.exists(),
            "samples_total": count,
            "manifest_path": "",
            "schema_version": "",
        }

    rows: list[dict[str, Any]] = []
    for manifest_dir in manifest_dirs:
        manifest_path = manifest_dir / "manifest.json"
        manifest = _read_json(manifest_path)
        rows.append(
            {
                "root": str(manifest_dir),
                "exists": True,
                "samples_total": _sample_count(manifest, manifest_dir, spec),
                "manifest_path": str(manifest_path),
                "schema_version": str(manifest.get("schema_version") or manifest.get("schema") or ""),
                "updated_at": str(manifest.get("updated_at") or manifest.get("created_at") or ""),
                "raw_counts": {
                    key: manifest.get(key)
                    for key in spec.count_keys
                    if key in manifest
                },
            }
        )
    if len(rows) == 1:
        return rows[0]
    return {
        "root": str(root),
        "exists": True,
        "samples_total": sum(int(row.get("samples_total") or 0) for row in rows),
        "manifest_path": "",
        "schema_version": "combined_manifest_roots_v1",
        "children": rows,
    }


def _task_status(
    spec: TrainingBankSpec,
    *,
    datasets_root: Path,
    context: InstancePipelineContext | None = None,
    cycle_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    target = _target_for(spec)
    roots = _candidate_roots(spec, datasets_root=datasets_root, context=context)
    root_rows = [row for row in (_root_status(root, spec) for root in roots) if row is not None]
    historical_samples_total = sum(int(row.get("samples_total") or 0) for row in root_rows)
    baselines = cycle_state.get("baselines") if isinstance(cycle_state, dict) else {}
    baseline = 0
    if isinstance(baselines, dict):
        task_baseline = baselines.get(spec.key)
        if isinstance(task_baseline, dict):
            baseline = max(0, int(task_baseline.get("samples_total") or 0))
    if baseline > historical_samples_total:
        baseline = 0
    samples_total = max(0, historical_samples_total - baseline)
    remaining = max(0, target - samples_total)
    ready = samples_total >= target
    return {
        "key": spec.key,
        "label": spec.label,
        "model_stage": spec.model_stage,
        "bank_kind": spec.bank_kind,
        "sample_unit": spec.sample_unit,
        "samples_total": samples_total,
        "historical_samples_total": historical_samples_total,
        "cycle_baseline_samples": baseline,
        "target_samples": target,
        "remaining_samples": remaining,
        "ready_to_train": ready,
        "count_policy": spec.count_policy,
        "train_action": spec.train_action,
        "next_action": spec.train_action if ready else "collect_more_human_corrections",
        "roots": root_rows,
    }


def load_training_cycle_status(
    *,
    root: Path | None = None,
    context: InstancePipelineContext | None = None,
    include_cycle: bool = True,
) -> dict[str, Any]:
    datasets_root = _datasets_root(root)
    cycle_state = load_cycle_state(root=datasets_root) if include_cycle else {}
    tasks = [
        _task_status(spec, datasets_root=datasets_root, context=context, cycle_state=cycle_state)
        for spec in TRAINING_BANKS
    ]
    ready = [row for row in tasks if row.get("ready_to_train")]
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": now_text(),
        "datasets_root": str(datasets_root),
        "target_per_model": default_target_samples(),
        "cycle": {
            "cycle_id": str(cycle_state.get("cycle_id") or "") if include_cycle else "",
            "started_at": str(cycle_state.get("started_at") or "") if include_cycle else "",
            "reason": str(cycle_state.get("reason") or "") if include_cycle else "",
            "state_path": str(cycle_state.get("state_path") or "") if include_cycle else "",
        },
        "tasks_total": len(tasks),
        "tasks_ready_to_train": len(ready),
        "samples_total": sum(int(row.get("samples_total") or 0) for row in tasks),
        "tasks": tasks,
        "policy": {
            "default_target_samples_per_model": DEFAULT_TARGET_SAMPLES,
            "human_corrections_are_training_data": True,
            "retrain_when_target_reached": True,
            "promotion_to_db_is_separate_from_training": True,
        },
    }


def start_new_training_cycle(
    *,
    root: Path | None = None,
    context: InstancePipelineContext | None = None,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    datasets_root = _datasets_root(root)
    previous_status = load_training_cycle_status(root=datasets_root, context=context, include_cycle=False)
    baselines = {
        str(task.get("key")): {
            "label": str(task.get("label") or ""),
            "samples_total": int(task.get("historical_samples_total") or task.get("samples_total") or 0),
            "target_samples": int(task.get("target_samples") or default_target_samples()),
            "sample_unit": str(task.get("sample_unit") or ""),
        }
        for task in previous_status.get("tasks") or []
        if isinstance(task, dict) and str(task.get("key") or "")
    }
    state = save_cycle_state(
        {
            "cycle_id": uuid.uuid4().hex[:12],
            "started_at": now_text(),
            "reason": reason,
            "baselines": baselines,
            "metadata": metadata or {},
        },
        root=datasets_root,
    )
    status = load_training_cycle_status(root=datasets_root, context=context, include_cycle=True)
    status["cycle_reset"] = {
        "schema_version": STATE_SCHEMA_VERSION,
        "state_path": state.get("state_path"),
        "cycle_id": state.get("cycle_id"),
        "baselines": baselines,
    }
    return status


def task_by_key(status: dict[str, Any], key: str) -> dict[str, Any]:
    for task in status.get("tasks") or []:
        if isinstance(task, dict) and str(task.get("key") or "") == key:
            return task
    return {}
