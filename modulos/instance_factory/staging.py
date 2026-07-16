from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from .models import (
    PIPELINE_CONTRACT_VERSION,
    InstancePipelineContext,
    PipelineStep,
    StageStatus,
    StagingProblemRecord,
    build_pipeline_contract,
    utc_now_text,
)
from .continuations import continuation_flags_enabled, has_continuation_marker, strip_continuation_marker
from .normalizer_training_bank import remove_sample as remove_normalizer_training_sample
from .normalizer_training_bank import upsert_sample as upsert_normalizer_training_sample
from .problem_solution_linking import (
    PROMOTABLE_BUNDLE_STATUS,
    PROMOTION_BUNDLE_SCHEMA_VERSION,
    bundle_fingerprint,
    candidate_evidence_fingerprint,
    candidate_review_fingerprint,
    canonical_payload_fingerprint,
    problem_source_fingerprint,
    unit_source_fingerprint,
    validate_confirmed_bundle,
    validate_solution_unit,
)


STATIC_MANIFEST_PAYLOAD_CACHE_TTL_S = 30.0
MODEL_INVENTORY_ENV_KEYS = (
    "PDF_PROBLEM_MODEL",
    "PDF_PROBLEM_MODEL_REPO",
    "YOLO_FIGURE_SEGMENT_MODEL",
    "YOLO_FIGURE_MODEL",
    "FIGURE_DETECTOR_MODEL",
    "YOLO_SEGMENT_MODEL",
    "YOLO_DETECT_MODEL",
    "YOLO_FIGURE_SEGMENT_MODEL_REPO",
    "HF_MODEL",
    "HF_OCR_NORMALIZER_MODEL",
)
MODEL_INVENTORY_CONFIG_FILES = (
    "hf_pdf_problem_detector_job_v4.json",
    "hf_pdf_problem_detector_job_v3.json",
    "hf_pdf_problem_detector_job_v2.json",
    "hf_ocr_geometry_rules_v4_reasoning_job.json",
    "hf_ocr_geometry_reviewed_v3_graphaware_reasoning_job.json",
    "hf_ocr_reviewed_v3_reasoning_job.json",
    "hf_graph_detector_job.json",
    "hf_ocr_normalizer_job.json",
)
_STATIC_MANIFEST_PAYLOAD_CACHE: dict[str, tuple[str, float, dict[str, Any]]] = {}


def _static_manifest_payload_signature() -> str:
    repo_root = Path(__file__).resolve().parents[2]
    rows: list[dict[str, Any]] = []
    for key in MODEL_INVENTORY_ENV_KEYS:
        rows.append({"kind": "env", "name": key, "value": str(os.getenv(key, "") or "")})
    for filename in MODEL_INVENTORY_CONFIG_FILES:
        path = repo_root / "config" / filename
        try:
            stat = path.stat()
            rows.append({"kind": "config", "name": filename, "mtime_ns": int(stat.st_mtime_ns), "size": int(stat.st_size)})
        except OSError:
            rows.append({"kind": "config", "name": filename, "mtime_ns": 0, "size": 0})
    return hashlib.sha1(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _cached_static_manifest_payload(cache_key: str, builder: Any) -> dict[str, Any]:
    signature = _static_manifest_payload_signature()
    cached = _STATIC_MANIFEST_PAYLOAD_CACHE.get(cache_key)
    if cached is not None:
        cached_signature, created_at, payload = cached
        if cached_signature == signature and time.monotonic() - created_at <= STATIC_MANIFEST_PAYLOAD_CACHE_TTL_S:
            return copy.deepcopy(payload)
    payload = builder()
    _STATIC_MANIFEST_PAYLOAD_CACHE[cache_key] = (signature, time.monotonic(), copy.deepcopy(payload))
    return payload


def _problem_number_from_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    patterns = (
        r"\\item\s*\[\s*\\textbf\{\s*(\d{1,4})\s*\.?\s*\}\s*\]",
        r"<\s*(\d{1,4})\s*[\.)]?\s*>",
        r"^\s*(\d{1,4})\s*[\.)]\s+",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return str(match.group(1)).strip()
    return ""


def _first_problem_number(*values: Any) -> str:
    for value in values:
        direct = str(value or "").strip()
        if re.fullmatch(r"\d{1,4}", direct):
            return direct
        inferred = _problem_number_from_text(direct)
        if inferred:
            return inferred
    return ""


def _canonical_final_option_value(value: Any) -> str:
    text = str(value or "").lower()
    text = (
        text.replace(r"\circ", "degree")
        .replace("°", "degree")
        .replace(r"\(", "")
        .replace(r"\)", "")
    )
    return re.sub(r"[${}\s]", "", text).strip()


def _extract_final_format_options(value: Any) -> dict[str, str] | None:
    text = (
        str(value or "")
        .replace("Ã‚Â£", "\u00a3")
        .replace("Â£", "\u00a3")
        .replace("Ã¦", "\u00e6")
        .replace("Â¦", "\u00e6")
    )
    match = re.search(
        r"\u00a3A\)([\s\S]*?)\u00e6B\)([\s\S]*?)\u00e6C\)([\s\S]*?)\u00a3D\)([\s\S]*?)\u00e6\u00e6E\)([\s\S]*?)\u00a3",
        text,
    )
    if not match:
        return None
    return dict(zip(("A", "B", "C", "D", "E"), (str(item) for item in match.groups())))


def _extract_loose_option_line(value: Any) -> dict[str, str] | None:
    text = str(value or "").strip()
    text = re.sub(r"^\s*\[CONT\.?\]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    match = re.search(
        r"(?:^|\s)A\)([\s\S]*?)\s+B\)([\s\S]*?)\s+C\)([\s\S]*?)\s+D\)([\s\S]*?)\s+E\)([\s\S]*?)\s*$",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return dict(zip(("A", "B", "C", "D", "E"), (str(item) for item in match.groups())))


def _remove_duplicate_loose_option_continuation(value: Any) -> str:
    text = str(value or "").strip()
    final_options = _extract_final_format_options(text)
    if not text or not final_options:
        return text
    lines = text.splitlines()
    final_line = 0
    acc: list[str] = []
    for index, line in enumerate(lines):
        acc.append(line)
        if _extract_final_format_options("\n".join(acc)):
            final_line = index
            break
    out: list[str] = []
    for index, line in enumerate(lines):
        loose_options = _extract_loose_option_line(line) if index > final_line else None
        if loose_options and all(
            _canonical_final_option_value(final_options[label]) == _canonical_final_option_value(loose_options[label])
            and _canonical_final_option_value(loose_options[label])
            for label in ("A", "B", "C", "D", "E")
        ):
            continue
        out.append(line)
    return "\n".join(out).strip()


def _repair_normalized_final_latex_number(
    normalized: dict[str, Any],
    record: StagingProblemRecord,
) -> dict[str, Any]:
    payload = dict(normalized or {})
    source = record.source if isinstance(record.source, dict) else {}
    number = _first_problem_number(
        payload.get("numero"),
        payload.get("latex_rendered_item"),
        record.raw_ocr,
        payload.get("enunciado_latex"),
        source.get("problem_number"),
        source.get("n"),
    )
    if number and not str(payload.get("numero") or "").strip():
        payload["numero"] = number
    final_latex = str(payload.get("latex_rendered_item") or "").strip()
    if not final_latex or not number:
        return payload
    repaired = re.sub(
        r"\\item\s*\[\s*\\textbf\{\s*(?:\.|\s*)\s*\}\s*\]",
        lambda _match: f"\\item[\\textbf{{{number}.}}]",
        final_latex,
        count=1,
        flags=re.IGNORECASE,
    )
    marker = rf"<\s*{re.escape(number)}\s*[\.)]?\s*>\s*"
    repaired = re.sub(rf"(\]\]\s*){marker}", r"\1", repaired, count=1, flags=re.IGNORECASE)
    repaired = re.sub(
        rf"(\\item\s*\[\s*\\textbf\{{\s*{re.escape(number)}\s*\.?\s*\}}\s*\]\s*){marker}",
        r"\1",
        repaired,
        count=1,
        flags=re.IGNORECASE,
    )
    repaired = _remove_duplicate_loose_option_continuation(repaired)
    payload["latex_rendered_item"] = repaired
    return payload


def _build_retraining_evaluation_matrix() -> dict[str, Any]:
    def _build() -> dict[str, Any]:
        from .model_inventory import build_retraining_evaluation_matrix

        return build_retraining_evaluation_matrix()

    try:
        return _cached_static_manifest_payload("evaluation_matrix", _build)
    except Exception:
        return {}


def _build_model_inventory_manifest() -> dict[str, Any]:
    def _build() -> dict[str, Any]:
        from .model_inventory import build_model_inventory_manifest

        return build_model_inventory_manifest()

    try:
        return _cached_static_manifest_payload("model_inventory", _build)
    except Exception:
        return {}


MAX_ARTIFACT_RECORD_DIR_LEN = 48
MAX_ARTIFACT_PATH_LEN_SOFT_LIMIT = 240
MIN_COMPACT_RECORD_FILE_STEM_LEN = 24
MIN_COMPACT_ARTIFACT_DIR_STEM_LEN = 12


def compact_artifact_dir_name(record_id: str, *, max_len: int = MAX_ARTIFACT_RECORD_DIR_LEN) -> str:
    max_len = max(1, int(max_len))
    raw = str(record_id or "").strip()
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-") or "record"
    if len(clean) <= max_len:
        return clean
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()
    if max_len <= 10:
        return digest[:max_len]
    digest_len = 16 if max_len >= 18 else max(8, max_len - 3)
    digest_len = min(digest_len, max_len - 2)
    keep = max_len - digest_len - 1
    prefix = clean[:keep].rstrip("._-") or "r"
    return f"{prefix}_{digest[:digest_len]}"


class InstanceStagingStore:
    schema_version = "pdf_factory_staging_v1"
    candidate_schema_version = "pdf_factory_promotion_candidate_v1"
    safe_record_id_re = re.compile(r"^[A-Za-z0-9._-]+$")
    problem_solution_record_statuses = frozenset({"pending_review", "solutions_absent_confirmed"})
    terminal_problem_solution_candidate_statuses = frozenset({"confirmed", "rejected", "orphan"})
    managed_problem_solution_artifact_keys = (
        "problem_solution_bundle_id",
        "problem_solution_bundle_path",
        "problem_solution_bundle_fingerprint",
        "problem_solution_bundle_revision",
        "problem_solution_bundle_status",
    )
    required_metadata = {
        "libro": "source.book_code",
        "instancia": "source.instance_type",
        "pdf": "source.pdf_path",
        "pagina": "source.page_number",
        "box": "source.bbox_px",
        "crop": "crop_path",
        "modelos": "models",
        "confianza": "confidence",
        "estado": "status",
    }

    def __init__(self, context: InstancePipelineContext, root: Path | None = None) -> None:
        self.context = context
        self.root = Path(root or context.staging_root()).expanduser().resolve()
        if root is not None:
            try:
                self.context.staging_root_override = str(self.root)
            except Exception:
                pass
        self.records_dir = self.root / "records"
        self.root.mkdir(parents=True, exist_ok=True)
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self._records_cache_signature: tuple[tuple[str, int, int], ...] | None = None
        self._records_cache_entries: list[tuple[Path, StagingProblemRecord]] | None = None

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def server_artifacts_path(self) -> Path:
        return self.root / "server_artifacts.json"

    @property
    def problem_solution_state_path(self) -> Path:
        return self.root / "problem_solution_state.json"

    @property
    def problem_solution_bundles_dir(self) -> Path:
        return self.root / "problem_solution_bundles"

    @property
    def problem_solution_transaction_journal_path(self) -> Path:
        return self.root / ".problem_solution_transaction.json"

    @property
    def _problem_solution_lock_path(self) -> Path:
        return self.root / ".problem_solution_state.lock"

    @property
    def _record_write_lock_path(self) -> Path:
        return self.root / ".staging_records.lock"

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _atomic_write_bytes(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        try:
            with temporary.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _problem_solution_transaction_snapshots(self, paths: list[Path]) -> list[dict[str, Any]]:
        root = self.root.resolve()
        snapshots: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_path in paths:
            path = Path(raw_path).expanduser().resolve()
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError as exc:
                raise ValueError(f"problem_solution_transaction_path_outside_root:{path}") from exc
            if relative in seen:
                continue
            seen.add(relative)
            exists = path.is_file()
            snapshots.append(
                {
                    "path": relative,
                    "existed": exists,
                    "content_b64": base64.b64encode(path.read_bytes()).decode("ascii") if exists else "",
                }
            )
        return snapshots

    def _restore_problem_solution_transaction_snapshots(self, snapshots: list[dict[str, Any]]) -> None:
        root = self.root.resolve()
        for raw in snapshots:
            row = dict(raw or {})
            relative = str(row.get("path") or "").strip()
            if not relative:
                raise RuntimeError("problem_solution_transaction_invalid_journal_path")
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise RuntimeError("problem_solution_transaction_journal_outside_root") from exc
            if bool(row.get("existed")):
                try:
                    payload = base64.b64decode(str(row.get("content_b64") or ""), validate=True)
                except Exception as exc:
                    raise RuntimeError("problem_solution_transaction_invalid_journal_content") from exc
                self._atomic_write_bytes(path, payload)
            else:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        self._invalidate_records_cache()

    def _write_problem_solution_transaction_journal(
        self,
        snapshots: list[dict[str, Any]],
        *,
        phase: str,
    ) -> None:
        self._atomic_write_json(
            self.problem_solution_transaction_journal_path,
            {
                "schema_version": "problem_solution_transaction_journal_v1",
                "phase": str(phase or "prepared"),
                "updated_at": utc_now_text(),
                "snapshots": copy.deepcopy(snapshots),
            },
        )

    def _recover_problem_solution_transaction(self) -> None:
        path = self.problem_solution_transaction_journal_path
        if not path.is_file():
            return
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise RuntimeError("problem_solution_transaction_invalid_journal") from exc
        if not isinstance(journal, dict) or str(journal.get("schema_version") or "") != (
            "problem_solution_transaction_journal_v1"
        ):
            raise RuntimeError("problem_solution_transaction_invalid_journal")
        if str(journal.get("phase") or "") != "committed":
            snapshots = [dict(item) for item in list(journal.get("snapshots") or []) if isinstance(item, dict)]
            self._restore_problem_solution_transaction_snapshots(snapshots)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    @contextmanager
    def _problem_solution_lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 5.0
        handle: int | None = None
        while handle is None:
            try:
                handle = os.open(
                    self._problem_solution_lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.write(handle, f"{os.getpid()}\n".encode("ascii", errors="ignore"))
            except FileExistsError:
                try:
                    age = time.time() - self._problem_solution_lock_path.stat().st_mtime
                    if age > 30.0:
                        self._problem_solution_lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise RuntimeError("problem_solution_state_lock_timeout")
                time.sleep(0.01)
        try:
            # A recovery journal may include record files.  Protect restoration
            # with the same lock used by ordinary OCR/human record writers.
            with self._record_write_lock():
                self._recover_problem_solution_transaction()
            yield
        finally:
            try:
                os.close(handle)
            except OSError:
                pass
            try:
                self._problem_solution_lock_path.unlink()
            except FileNotFoundError:
                pass

    @contextmanager
    def _record_write_lock(self):
        """Serialize record replacements with problem-solution transactions."""

        self.root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 5.0
        handle: int | None = None
        while handle is None:
            try:
                handle = os.open(
                    self._record_write_lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.write(handle, f"{os.getpid()}\n".encode("ascii", errors="ignore"))
            except FileExistsError:
                try:
                    age = time.time() - self._record_write_lock_path.stat().st_mtime
                    if age > 30.0:
                        self._record_write_lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise RuntimeError("staging_record_write_lock_timeout")
                time.sleep(0.01)
        try:
            yield
        finally:
            try:
                os.close(handle)
            except OSError:
                pass
            try:
                self._record_write_lock_path.unlink()
            except FileNotFoundError:
                pass

    @contextmanager
    def _problem_solution_record_transaction_lock(self):
        """Use one lock order everywhere: problem-solution state, then records."""

        with self._problem_solution_lock():
            with self._record_write_lock():
                yield

    @staticmethod
    def _default_problem_solution_state() -> dict[str, Any]:
        return {
            "schema_version": "problem_solution_staging_state_v1",
            "revision": 0,
            "updated_at": "",
            "solution_units": [],
            "candidate_links": [],
            "invalidated_candidate_links": [],
            "review_events": [],
            "problem_statuses": {},
            "bundle_ids": [],
            "context_fingerprint": "",
        }

    def _load_problem_solution_state(self) -> dict[str, Any]:
        state = self._default_problem_solution_state()
        try:
            raw = json.loads(self.problem_solution_state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        state.update(raw)
        for key in (
            "solution_units",
            "candidate_links",
            "invalidated_candidate_links",
            "review_events",
            "bundle_ids",
        ):
            if not isinstance(state.get(key), list):
                state[key] = []
        if not isinstance(state.get("problem_statuses"), dict):
            state["problem_statuses"] = {}
        try:
            state["revision"] = max(0, int(state.get("revision") or 0))
        except Exception:
            state["revision"] = 0
        return state

    @staticmethod
    def _assert_problem_solution_revision(state: dict[str, Any], expected_revision: int | None) -> None:
        if expected_revision is None:
            return
        actual = int(state.get("revision") or 0)
        if int(expected_revision) != actual:
            raise RuntimeError(
                f"problem_solution_revision_conflict:expected={int(expected_revision)}:actual={actual}"
            )

    def _write_problem_solution_state(self, state: dict[str, Any]) -> None:
        state = dict(state)
        state["schema_version"] = "problem_solution_staging_state_v1"
        state["context_fingerprint"] = str(
            state.get("context_fingerprint") or self._problem_solution_page_map_fingerprint()
        )
        state["updated_at"] = utc_now_text()
        self._atomic_write_json(self.problem_solution_state_path, state)

    @staticmethod
    def _rows_equal(left: Any, right: Any) -> bool:
        return canonical_payload_fingerprint(left) == canonical_payload_fingerprint(right)

    def problem_solution_snapshot(self) -> dict[str, Any]:
        state = self._load_problem_solution_state()
        bundles: list[dict[str, Any]] = []
        if self.problem_solution_bundles_dir.is_dir():
            for path in sorted(self.problem_solution_bundles_dir.glob("*.json"), key=lambda item: item.name.lower()):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if isinstance(payload, dict):
                    bundles.append(payload)
        return {
            "schema_version": "problem_solution_staging_snapshot_v1",
            "revision": int(state.get("revision") or 0),
            "solution_units": copy.deepcopy(list(state.get("solution_units") or [])),
            "candidate_links": copy.deepcopy(list(state.get("candidate_links") or [])),
            "invalidated_candidate_links": copy.deepcopy(
                list(state.get("invalidated_candidate_links") or [])
            ),
            "bundles": copy.deepcopy(bundles),
            "review_events": copy.deepcopy(list(state.get("review_events") or [])),
            "problem_statuses": copy.deepcopy(dict(state.get("problem_statuses") or {})),
            "context_fingerprint": str(
                state.get("context_fingerprint") or self._problem_solution_page_map_fingerprint()
            ),
        }

    def _problem_solution_scope(self) -> dict[str, str]:
        structure = dict(getattr(self.context, "problem_solution_structure", {}) or {})
        return {
            "book_code": str(getattr(self.context, "book_code", "") or "").strip(),
            "instance_type": str(getattr(self.context, "instance_type", "") or "").strip(),
            "exercise_set_id": str(structure.get("exercise_set_id") or "").strip(),
        }

    def _problem_solution_page_map_fingerprint(self) -> str:
        """Fingerprint only context that determines problem/solution page roles."""

        def semantic_pages(values: Any) -> list[int]:
            pages: set[int] = set()
            for raw in list(values or []):
                try:
                    page = int(raw)
                except (TypeError, ValueError):
                    continue
                if page > 0:
                    pages.add(page)
            return sorted(pages)

        def semantic_ranges(values: Any) -> list[dict[str, int]]:
            ranges: set[tuple[int, int]] = set()
            for raw in list(values or []):
                if not isinstance(raw, dict):
                    continue
                try:
                    start = int(raw.get("start") or raw.get("from") or 0)
                    end = int(raw.get("end") or raw.get("to") or start)
                except (TypeError, ValueError):
                    continue
                if start > 0 and end > 0:
                    ranges.add((min(start, end), max(start, end)))
            return [{"start": start, "end": end} for start, end in sorted(ranges)]

        structure = dict(getattr(self.context, "problem_solution_structure", {}) or {})
        payload = {
            "scope": self._problem_solution_scope(),
            "problem_page_selection": {
                "selected_pages": semantic_pages(getattr(self.context, "selected_pages", [])),
                "page_ranges": semantic_ranges(getattr(self.context, "selected_page_ranges", [])),
                "review_status": str(getattr(self.context, "page_selection_review_status", "") or ""),
                "configured": bool(getattr(self.context, "page_selection_configured", False)),
            },
            "solution_page_selection": {
                "selected_pages": semantic_pages(getattr(self.context, "solution_selected_pages", [])),
                "page_ranges": semantic_ranges(getattr(self.context, "solution_selected_page_ranges", [])),
                "review_status": str(
                    getattr(self.context, "solution_page_selection_review_status", "") or ""
                ),
                "configured": bool(getattr(self.context, "solution_page_selection_configured", False)),
            },
            "structure": {
                "schema_version": str(structure.get("schema_version") or ""),
                "structure_mode": str(structure.get("structure_mode") or ""),
                "solution_status": str(structure.get("solution_status") or ""),
                "exercise_set_id": str(structure.get("exercise_set_id") or ""),
                "review_status": str(structure.get("review_status") or ""),
            },
        }
        return canonical_payload_fingerprint(payload)

    def sync_problem_solution_context(
        self,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Invalidate derived candidates after a semantic page/structure context change."""

        current_fingerprint = self._problem_solution_page_map_fingerprint()
        with self._problem_solution_lock():
            state = self._load_problem_solution_state()
            self._assert_problem_solution_revision(state, expected_revision)
            previous_fingerprint = str(state.get("context_fingerprint") or "").strip()
            if not previous_fingerprint:
                state["context_fingerprint"] = current_fingerprint
                self._write_problem_solution_state(state)
            elif previous_fingerprint != current_fingerprint:
                invalidated = [
                    dict(item)
                    for item in list(state.get("invalidated_candidate_links") or [])
                    if isinstance(item, dict)
                ]
                for raw in list(state.get("candidate_links") or []):
                    if not isinstance(raw, dict):
                        continue
                    archived = copy.deepcopy(raw)
                    archived["invalidated_reason"] = "context_changed"
                    archived["invalidated_from_context_fingerprint"] = previous_fingerprint
                    archived["invalidated_to_context_fingerprint"] = current_fingerprint
                    archived["invalidated_at"] = utc_now_text()
                    invalidated.append(archived)
                state["invalidated_candidate_links"] = invalidated
                state["candidate_links"] = []
                statuses = {
                    str(key): copy.deepcopy(dict(value))
                    for key, value in dict(state.get("problem_statuses") or {}).items()
                    if isinstance(value, dict)
                }
                review_events = [
                    copy.deepcopy(dict(item))
                    for item in list(state.get("review_events") or [])
                    if isinstance(item, dict)
                ]
                invalidated_at = utc_now_text()
                for record_id, previous_status in sorted(statuses.items()):
                    if str(previous_status.get("status") or "") != "solutions_absent_confirmed":
                        continue
                    event_seed = {
                        "record_id": record_id,
                        "action": "invalidate_absence_context_changed",
                        "from_context_fingerprint": previous_fingerprint,
                        "to_context_fingerprint": current_fingerprint,
                    }
                    review_events.append(
                        {
                            "schema_version": "problem_solution_review_event_v1",
                            "review_version": "problem_solution_review_event_v1",
                            "review_event_id": (
                                "psr_"
                                + canonical_payload_fingerprint(event_seed).split(":", 1)[1][:20]
                            ),
                            "target_type": "problem_record",
                            "target_id": record_id,
                            "record_id": record_id,
                            "action": "invalidate_absence_context_changed",
                            "before": {"status": "solutions_absent_confirmed"},
                            "after": {"status": "pending_review"},
                            "status": "pending_review",
                            "reviewer": "system_context_invalidation",
                            "comment": "El mapa semantico de paginas o estructura cambio; revisar nuevamente la ausencia de solucion.",
                            "reviewed_at": invalidated_at,
                        }
                    )
                    statuses[record_id] = {
                        "schema_version": "problem_solution_record_status_v1",
                        "record_id": record_id,
                        "status": "pending_review",
                        "reviewer": "system_context_invalidation",
                        "comment": "context_changed",
                        "reviewed_at": invalidated_at,
                    }
                state["problem_statuses"] = statuses
                state["review_events"] = review_events
                state["context_fingerprint"] = current_fingerprint
                state["revision"] = int(state.get("revision") or 0) + 1
                self._write_problem_solution_state(state)
        return self.problem_solution_snapshot()

    def _normalize_solution_unit(self, raw: dict[str, Any]) -> dict[str, Any]:
        unit = copy.deepcopy(dict(raw or {}))
        unit_id = str(unit.get("solution_unit_id") or unit.get("unit_id") or "").strip()
        if not unit_id:
            raise ValueError("solution_unit_id requerido")
        unit["solution_unit_id"] = unit_id

        expected_scope = self._problem_solution_scope()
        scope = dict(unit.get("scope") or {}) if isinstance(unit.get("scope"), dict) else {}
        aliases = {
            "book_code": ("book_code", "book_id"),
            "instance_type": ("instance_type", "instance_id"),
            "exercise_set_id": ("exercise_set_id",),
        }
        normalized_scope: dict[str, str] = {}
        for canonical, keys in aliases.items():
            value = ""
            for key in keys:
                value = str(unit.get(key) or scope.get(key) or "").strip()
                if value:
                    break
            value = value or str(expected_scope.get(canonical) or "").strip()
            normalized_scope[canonical] = value
            unit[canonical] = value
        unit["scope"] = normalized_scope

        issues = validate_solution_unit(unit, expected_scope=expected_scope)
        if bool(getattr(self.context, "solution_page_selection_configured", False)):
            allowed_pages = {
                int(page)
                for page in list(getattr(self.context, "solution_selected_pages", []) or [])
                if str(page).strip().isdigit() and int(page) > 0
            }
            outside_pages: set[int] = set()
            for fragment in list(unit.get("fragments") or []):
                if not isinstance(fragment, dict):
                    continue
                try:
                    page = int(fragment.get("page_number") or 0)
                except (TypeError, ValueError):
                    page = 0
                if page > 0 and page not in allowed_pages:
                    outside_pages.add(page)
            if outside_pages:
                issues.append(
                    "solution_unit:"
                    f"{unit_id}:outside_solution_page_selection:"
                    + ",".join(str(page) for page in sorted(outside_pages))
                )
        if issues:
            raise ValueError(";".join(issues))
        unit["source_fingerprint"] = unit_source_fingerprint(unit)
        return unit

    def upsert_solution_units(
        self,
        units: list[dict[str, Any]],
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        incoming: list[dict[str, Any]] = []
        for raw in list(units or []):
            incoming.append(self._normalize_solution_unit(dict(raw or {})))
        with self._problem_solution_lock():
            state = self._load_problem_solution_state()
            self._assert_problem_solution_revision(state, expected_revision)
            by_id = {
                str(row.get("solution_unit_id") or row.get("unit_id") or "").strip(): dict(row)
                for row in list(state.get("solution_units") or [])
                if isinstance(row, dict)
            }
            for unit in incoming:
                by_id[unit["solution_unit_id"]] = unit
            updated = [by_id[key] for key in sorted(by_id)]
            if not self._rows_equal(updated, state.get("solution_units") or []):
                previous_units_fingerprint = canonical_payload_fingerprint(
                    state.get("solution_units") or []
                )
                updated_units_fingerprint = canonical_payload_fingerprint(updated)
                invalidated_at = utc_now_text()
                invalidated = [
                    copy.deepcopy(dict(item))
                    for item in list(state.get("invalidated_candidate_links") or [])
                    if isinstance(item, dict)
                ]
                for raw in list(state.get("candidate_links") or []):
                    if not isinstance(raw, dict):
                        continue
                    archived = copy.deepcopy(raw)
                    archived.update(
                        {
                            "invalidated_reason": "solution_units_changed",
                            "invalidated_from_units_fingerprint": previous_units_fingerprint,
                            "invalidated_to_units_fingerprint": updated_units_fingerprint,
                            "invalidated_at": invalidated_at,
                        }
                    )
                    invalidated.append(archived)

                statuses = {
                    str(key): copy.deepcopy(dict(value))
                    for key, value in dict(state.get("problem_statuses") or {}).items()
                    if isinstance(value, dict)
                }
                review_events = [
                    copy.deepcopy(dict(item))
                    for item in list(state.get("review_events") or [])
                    if isinstance(item, dict)
                ]
                for record_id, previous_status in sorted(statuses.items()):
                    if str(previous_status.get("status") or "") != "solutions_absent_confirmed":
                        continue
                    event_seed = {
                        "record_id": record_id,
                        "action": "invalidate_absence_solution_units_changed",
                        "from_units_fingerprint": previous_units_fingerprint,
                        "to_units_fingerprint": updated_units_fingerprint,
                    }
                    review_events.append(
                        {
                            "schema_version": "problem_solution_review_event_v1",
                            "review_version": "problem_solution_review_event_v1",
                            "review_event_id": (
                                "psr_"
                                + canonical_payload_fingerprint(event_seed).split(":", 1)[1][:20]
                            ),
                            "target_type": "problem_record",
                            "target_id": record_id,
                            "record_id": record_id,
                            "action": "invalidate_absence_solution_units_changed",
                            "before": {"status": "solutions_absent_confirmed"},
                            "after": {"status": "pending_review"},
                            "status": "pending_review",
                            "reviewer": "system_evidence_invalidation",
                            "comment": (
                                "Las unidades o boxes de solucion cambiaron; "
                                "revisar nuevamente la ausencia de solucion."
                            ),
                            "reviewed_at": invalidated_at,
                        }
                    )
                    statuses[record_id] = {
                        "schema_version": "problem_solution_record_status_v1",
                        "record_id": record_id,
                        "status": "pending_review",
                        "reviewer": "system_evidence_invalidation",
                        "comment": "solution_units_changed",
                        "reviewed_at": invalidated_at,
                    }

                state["solution_units"] = updated
                state["invalidated_candidate_links"] = invalidated
                state["candidate_links"] = []
                state["problem_statuses"] = statuses
                state["review_events"] = review_events
                state["revision"] = int(state.get("revision") or 0) + 1
                self._write_problem_solution_state(state)
        return self.problem_solution_snapshot()

    @staticmethod
    def _normalize_problem_solution_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in list(candidates or []):
            candidate = copy.deepcopy(dict(raw or {}))
            candidate_id = str(candidate.get("candidate_link_id") or "").strip()
            if not candidate_id:
                raise ValueError("candidate_link_id requerido")
            if candidate_id in seen:
                raise ValueError(f"candidate_link_id duplicado: {candidate_id}")
            seen.add(candidate_id)
            rows.append(candidate)
        rows.sort(key=lambda row: str(row.get("candidate_link_id") or ""))
        return rows

    @staticmethod
    def _normalize_problem_solution_review_event(event: dict[str, Any]) -> dict[str, Any]:
        row = copy.deepcopy(dict(event or {}))
        event_id = str(row.get("review_event_id") or "").strip()
        if not event_id:
            event_id = f"psr_{canonical_payload_fingerprint(row).split(':', 1)[1][:20]}"
            row["review_event_id"] = event_id
        return row

    @staticmethod
    def _problem_solution_candidate_record_id(candidate: dict[str, Any]) -> str:
        row = dict(candidate or {})
        review = dict(row.get("human_review") or {})
        problem_ref = dict(row.get("problem_ref") or {})
        return str(
            row.get("selected_problem_unit_id")
            or review.get("problem_unit_id")
            or problem_ref.get("record_id")
            or problem_ref.get("unit_id")
            or ""
        ).strip()

    def _pending_problem_solution_candidate_ids(
        self,
        state: dict[str, Any],
        record_id: str,
    ) -> list[str]:
        pending: list[str] = []
        wanted = str(record_id or "").strip()
        for raw in list(state.get("candidate_links") or []):
            if not isinstance(raw, dict) or self._problem_solution_candidate_record_id(raw) != wanted:
                continue
            review = dict(raw.get("human_review") or {})
            review_status = str(raw.get("review_status") or review.get("status") or "").strip().lower()
            if review_status in self.terminal_problem_solution_candidate_statuses:
                continue
            candidate_id = str(raw.get("candidate_link_id") or "unknown").strip() or "unknown"
            pending.append(candidate_id)
        return sorted(set(pending))

    def set_problem_solution_record_status(
        self,
        record_id: str,
        status: str,
        reviewer: str,
        comment: str,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        """Persist one per-problem solution review decision and its append-only audit event."""

        clean_record_id = str(record_id or "").strip()
        clean_status = str(status or "").strip().lower()
        clean_reviewer = str(reviewer or "").strip()
        clean_comment = str(comment or "").strip()
        if self.get_record(clean_record_id) is None:
            raise KeyError(clean_record_id)
        if clean_status not in self.problem_solution_record_statuses:
            raise ValueError(f"problem_solution_record_status invalido: {status!r}")
        if not clean_reviewer:
            raise ValueError("reviewer requerido para problem_solution_record_status")

        with self._problem_solution_lock():
            state = self._load_problem_solution_state()
            self._assert_problem_solution_revision(state, expected_revision)
            if clean_status == "solutions_absent_confirmed":
                pending = self._pending_problem_solution_candidate_ids(state, clean_record_id)
                if pending:
                    raise ValueError(
                        ";".join(
                            f"problem_solution:pending_candidate_review:{candidate_id}"
                            for candidate_id in pending
                        )
                    )

            statuses = {
                str(key): copy.deepcopy(dict(value))
                for key, value in dict(state.get("problem_statuses") or {}).items()
                if isinstance(value, dict)
            }
            previous = dict(statuses.get(clean_record_id) or {})
            if (
                str(previous.get("status") or "") == clean_status
                and str(previous.get("reviewer") or "") == clean_reviewer
                and str(previous.get("comment") or "") == clean_comment
            ):
                return self.problem_solution_snapshot()

            reviewed_at = utc_now_text()
            next_status = {
                "schema_version": "problem_solution_record_status_v1",
                "record_id": clean_record_id,
                "status": clean_status,
                "reviewer": clean_reviewer,
                "comment": clean_comment,
                "reviewed_at": reviewed_at,
            }
            event_seed = {
                "record_id": clean_record_id,
                "status": clean_status,
                "reviewer": clean_reviewer,
                "comment": clean_comment,
                "revision": int(state.get("revision") or 0) + 1,
            }
            event_id = f"psr_{canonical_payload_fingerprint(event_seed).split(':', 1)[1][:20]}"
            event = {
                "schema_version": "problem_solution_review_event_v1",
                "review_version": "problem_solution_review_event_v1",
                "review_event_id": event_id,
                "target_type": "problem_record",
                "target_id": clean_record_id,
                "record_id": clean_record_id,
                "action": "confirm_absence" if clean_status == "solutions_absent_confirmed" else "mark_pending",
                "before": {"status": str(previous.get("status") or "")},
                "after": {"status": clean_status},
                "status": clean_status,
                "reviewer": clean_reviewer,
                "comment": clean_comment,
                "reviewed_at": reviewed_at,
            }
            statuses[clean_record_id] = next_status
            reviews = [
                copy.deepcopy(dict(item))
                for item in list(state.get("review_events") or [])
                if isinstance(item, dict)
            ]
            reviews.append(event)
            state["problem_statuses"] = statuses
            state["review_events"] = reviews
            state["revision"] = int(state.get("revision") or 0) + 1
            self._write_problem_solution_state(state)
        return self.problem_solution_snapshot()

    def write_candidate_links(
        self,
        candidates: list[dict[str, Any]],
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        rows = self._normalize_problem_solution_candidates(candidates)
        with self._problem_solution_lock():
            state = self._load_problem_solution_state()
            self._assert_problem_solution_revision(state, expected_revision)
            if not self._rows_equal(rows, state.get("candidate_links") or []):
                state["candidate_links"] = rows
                state["revision"] = int(state.get("revision") or 0) + 1
                self._write_problem_solution_state(state)
        return self.problem_solution_snapshot()

    def append_problem_solution_review(
        self,
        event: dict[str, Any],
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        row = self._normalize_problem_solution_review_event(event)
        event_id = str(row.get("review_event_id") or "")
        with self._problem_solution_lock():
            state = self._load_problem_solution_state()
            self._assert_problem_solution_revision(state, expected_revision)
            current = [dict(item) for item in list(state.get("review_events") or []) if isinstance(item, dict)]
            existing = next((item for item in current if str(item.get("review_event_id") or "") == event_id), None)
            if existing is not None and not self._rows_equal(existing, row):
                raise RuntimeError(f"problem_solution_review_conflict:{event_id}")
            if existing is None:
                current.append(row)
                state["review_events"] = current
                state["revision"] = int(state.get("revision") or 0) + 1
                self._write_problem_solution_state(state)
        return self.problem_solution_snapshot()

    def _problem_solution_bundle_path(self, bundle_id: str) -> Path:
        clean = str(bundle_id or "").strip()
        if not clean or not self.safe_record_id_re.match(clean):
            raise ValueError(f"bundle_id invalido: {bundle_id!r}")
        return self.problem_solution_bundles_dir / f"{clean}.json"

    def read_problem_solution_bundle(self, bundle_id: str) -> dict[str, Any] | None:
        try:
            path = self._problem_solution_bundle_path(bundle_id)
        except ValueError:
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        if not isinstance(payload, dict) or str(payload.get("bundle_id") or "") != str(bundle_id or ""):
            return None
        return payload

    def _attach_problem_solution_bundle(
        self,
        record: StagingProblemRecord,
        bundle: dict[str, Any],
        *,
        rewrite_manifest: bool,
    ) -> None:
        bundle_id = str(bundle.get("bundle_id") or "")
        path = self._problem_solution_bundle_path(bundle_id)
        with self._record_write_lock():
            current = self.get_record(record.record_id) or record
            current.artifacts = {
                **dict(current.artifacts or {}),
                "problem_solution_bundle_id": bundle_id,
                "problem_solution_bundle_path": str(path),
                "problem_solution_bundle_fingerprint": str(bundle.get("bundle_fingerprint") or ""),
                "problem_solution_bundle_revision": int(bundle.get("revision") or 0),
                "problem_solution_bundle_status": str(bundle.get("status") or ""),
            }
            self._upsert_record_unlocked(
                current,
                rewrite_manifest=rewrite_manifest,
                preserve_current_problem_solution_artifacts=False,
            )

    def _problem_solution_record_storage_path(self, record_id: str) -> Path:
        for path in self._record_path_candidates(record_id):
            if path.is_file():
                return path
        wanted = str(record_id or "").strip()
        for path, record in self._load_record_entries():
            if str(record.record_id or "") == wanted or str(record.crop_id or "") == wanted:
                return path
        return self._record_path(record_id)

    @staticmethod
    def _problem_solution_rows_by_id(
        rows: Any,
        *keys: str,
    ) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for raw in list(rows or []):
            if not isinstance(raw, dict):
                continue
            identifier = next((str(raw.get(key) or "").strip() for key in keys if str(raw.get(key) or "").strip()), "")
            if identifier:
                indexed[identifier] = dict(raw)
        return indexed

    def _freeze_problem_solution_dependencies(
        self,
        bundle: dict[str, Any],
        state: dict[str, Any],
    ) -> list[str]:
        """Attach the exact reviewed inputs to a new bundle, never refreshing an old snapshot."""

        if isinstance(bundle.get("dependency_snapshot"), dict) and bundle.get("dependency_snapshot"):
            return self._problem_solution_dependency_issues(bundle, state)

        issues: list[str] = []
        units = self._problem_solution_rows_by_id(state.get("solution_units"), "solution_unit_id", "unit_id")
        candidates = self._problem_solution_rows_by_id(state.get("candidate_links"), "candidate_link_id")
        reviews = self._problem_solution_rows_by_id(state.get("review_events"), "review_event_id")
        problem_ref = dict(bundle.get("problem_ref") or {})
        problem_unit_id = str(problem_ref.get("unit_id") or problem_ref.get("record_id") or "").strip()
        unit_fingerprints: dict[str, str] = {}
        evidence_fingerprints: dict[str, str] = {}
        review_fingerprints: dict[str, str] = {}
        event_fingerprints: dict[str, str] = {}

        for raw_solution in list(bundle.get("solutions") or []):
            if not isinstance(raw_solution, dict):
                continue
            solution = raw_solution
            solution_id = str(solution.get("solution_id") or "unknown")
            unit_id = str(solution.get("solution_unit_id") or "").strip()
            unit = units.get(unit_id)
            if unit is None:
                issues.append(f"solution_bundle:missing_solution_unit:{unit_id or solution_id}")
            else:
                fingerprint = unit_source_fingerprint(unit)
                expected = str(solution.get("source_fingerprint") or "").strip()
                if expected and expected != fingerprint:
                    issues.append(f"solution_bundle:stale_solution_unit:{unit_id or solution_id}")
                else:
                    solution["source_fingerprint"] = fingerprint
                    unit_fingerprints[unit_id] = fingerprint

            candidate_id = str(solution.get("candidate_link_id") or "").strip()
            candidate = candidates.get(candidate_id)
            if candidate is None:
                issues.append(f"solution_bundle:missing_candidate_link:{candidate_id or solution_id}")
            else:
                evidence_fingerprint = candidate_evidence_fingerprint(candidate)
                current_review_fingerprint = candidate_review_fingerprint(candidate)
                expected_evidence = str(solution.get("candidate_evidence_fingerprint") or "").strip()
                expected_review = str(solution.get("review_fingerprint") or "").strip()
                if expected_evidence and expected_evidence != evidence_fingerprint:
                    issues.append(f"solution_bundle:stale_candidate_evidence:{candidate_id or solution_id}")
                if expected_review and expected_review != current_review_fingerprint:
                    issues.append(f"solution_bundle:stale_candidate_review:{candidate_id or solution_id}")
                candidate_review = dict(candidate.get("human_review") or {})
                selected_problem = str(
                    candidate.get("selected_problem_unit_id")
                    or candidate_review.get("problem_unit_id")
                    or ""
                ).strip()
                if str(candidate_review.get("status") or "") != "confirmed" or selected_problem != problem_unit_id:
                    issues.append(f"solution_bundle:candidate_not_human_confirmed:{candidate_id or solution_id}")
                solution["candidate_evidence_fingerprint"] = evidence_fingerprint
                solution["review_fingerprint"] = current_review_fingerprint
                evidence_fingerprints[candidate_id] = evidence_fingerprint
                review_fingerprints[candidate_id] = current_review_fingerprint

            event_id = str(solution.get("human_review_event_id") or "").strip()
            event = reviews.get(event_id)
            if event is None:
                issues.append(f"solution_bundle:missing_review_event:{event_id or solution_id}")
            else:
                event_fingerprints[event_id] = canonical_payload_fingerprint(event)

        if issues:
            return list(dict.fromkeys(issues))
        bundle["dependency_snapshot"] = {
            "schema_version": "problem_solution_dependency_snapshot_v1",
            "page_map_fingerprint": self._problem_solution_page_map_fingerprint(),
            "solution_units": unit_fingerprints,
            "candidate_evidence": evidence_fingerprints,
            "candidate_reviews": review_fingerprints,
            "review_events": event_fingerprints,
        }
        return []

    def _problem_solution_dependency_issues(
        self,
        bundle: dict[str, Any],
        state: dict[str, Any] | None = None,
    ) -> list[str]:
        state = state or self._load_problem_solution_state()
        snapshot = dict(bundle.get("dependency_snapshot") or {})
        if not snapshot:
            return ["solution_bundle:missing_dependency_snapshot"]
        issues: list[str] = []
        if str(snapshot.get("schema_version") or "") != "problem_solution_dependency_snapshot_v1":
            issues.append("solution_bundle:invalid_dependency_snapshot")
        if str(snapshot.get("page_map_fingerprint") or "") != self._problem_solution_page_map_fingerprint():
            issues.append("solution_bundle:stale_page_map")

        units = self._problem_solution_rows_by_id(state.get("solution_units"), "solution_unit_id", "unit_id")
        candidates = self._problem_solution_rows_by_id(state.get("candidate_links"), "candidate_link_id")
        reviews = self._problem_solution_rows_by_id(state.get("review_events"), "review_event_id")
        frozen_units = dict(snapshot.get("solution_units") or {})
        frozen_evidence = dict(snapshot.get("candidate_evidence") or {})
        frozen_reviews = dict(snapshot.get("candidate_reviews") or {})
        frozen_events = dict(snapshot.get("review_events") or {})

        for raw_solution in list(bundle.get("solutions") or []):
            if not isinstance(raw_solution, dict):
                continue
            solution_id = str(raw_solution.get("solution_id") or "unknown")
            unit_id = str(raw_solution.get("solution_unit_id") or "").strip()
            unit = units.get(unit_id)
            current_unit_fingerprint = unit_source_fingerprint(unit) if unit is not None else ""
            expected_unit_fingerprint = str(
                frozen_units.get(unit_id) or raw_solution.get("source_fingerprint") or ""
            ).strip()
            if unit is None:
                issues.append(f"solution_bundle:missing_solution_unit:{unit_id or solution_id}")
            elif not expected_unit_fingerprint or expected_unit_fingerprint != current_unit_fingerprint:
                issues.append(f"solution_bundle:stale_solution_unit:{unit_id or solution_id}")

            candidate_id = str(raw_solution.get("candidate_link_id") or "").strip()
            candidate = candidates.get(candidate_id)
            if candidate is None:
                issues.append(f"solution_bundle:missing_candidate_link:{candidate_id or solution_id}")
            else:
                expected_evidence = str(
                    frozen_evidence.get(candidate_id)
                    or raw_solution.get("candidate_evidence_fingerprint")
                    or ""
                ).strip()
                expected_review = str(
                    frozen_reviews.get(candidate_id) or raw_solution.get("review_fingerprint") or ""
                ).strip()
                if not expected_evidence or expected_evidence != candidate_evidence_fingerprint(candidate):
                    issues.append(f"solution_bundle:stale_candidate_evidence:{candidate_id or solution_id}")
                if not expected_review or expected_review != candidate_review_fingerprint(candidate):
                    issues.append(f"solution_bundle:stale_candidate_review:{candidate_id or solution_id}")

            event_id = str(raw_solution.get("human_review_event_id") or "").strip()
            event = reviews.get(event_id)
            expected_event = str(frozen_events.get(event_id) or "").strip()
            if event is None:
                issues.append(f"solution_bundle:missing_review_event:{event_id or solution_id}")
            elif not expected_event or expected_event != canonical_payload_fingerprint(event):
                issues.append(f"solution_bundle:stale_review_event:{event_id or solution_id}")
        return list(dict.fromkeys(issues))

    def write_problem_solution_bundle(
        self,
        bundle: dict[str, Any],
        expected_revision: int | None = None,
        *,
        rewrite_manifest: bool = True,
    ) -> dict[str, Any]:
        payload = copy.deepcopy(dict(bundle or {}))
        if str(payload.get("schema_version") or "") != PROMOTION_BUNDLE_SCHEMA_VERSION:
            raise ValueError("solution_bundle:invalid_schema_version")
        bundle_id = str(payload.get("bundle_id") or "").strip()
        path = self._problem_solution_bundle_path(bundle_id)
        problem_ref = dict(payload.get("problem_ref") or {})
        record_id = str(problem_ref.get("record_id") or "").strip()
        if not record_id:
            raise ValueError("solution_bundle:missing_problem_record_id")
        record = self.get_record(record_id)
        if record is None:
            raise KeyError(record_id)

        unchanged = False
        with self._problem_solution_lock():
            state = self._load_problem_solution_state()
            self._assert_problem_solution_revision(state, expected_revision)
            current = self.read_problem_solution_bundle(bundle_id)
            current_revision = int((current or {}).get("revision") or 0)
            if current is not None:
                comparison = copy.deepcopy(payload)
                comparison["revision"] = current_revision
                comparison["bundle_fingerprint"] = bundle_fingerprint(comparison)
                unchanged = self._rows_equal(comparison, current)
                if not unchanged and expected_revision is None:
                    raise RuntimeError(
                        f"problem_solution_revision_conflict:bundle={bundle_id}:expected_snapshot_revision_required"
                    )
            if unchanged:
                saved = current or payload
            else:
                requested_revision = int(payload.get("revision") or 0)
                payload["revision"] = (
                    max(current_revision + 1, requested_revision) if current_revision else max(1, requested_revision)
                )
                dependency_issues = self._freeze_problem_solution_dependencies(payload, state)
                if dependency_issues:
                    raise ValueError(";".join(dependency_issues))
                payload["bundle_fingerprint"] = bundle_fingerprint(payload)
                if str(payload.get("status") or "") == PROMOTABLE_BUNDLE_STATUS:
                    issues = self.problem_solution_bundle_issues(payload, record=record)
                    if issues:
                        raise ValueError(";".join(issues))
                self._atomic_write_json(path, payload)
                bundle_ids = {
                    str(item).strip() for item in list(state.get("bundle_ids") or []) if str(item).strip()
                }
                bundle_ids.add(bundle_id)
                state["bundle_ids"] = sorted(bundle_ids)
                state["revision"] = int(state.get("revision") or 0) + 1
                self._write_problem_solution_state(state)
                saved = payload
        self._attach_problem_solution_bundle(record, saved, rewrite_manifest=rewrite_manifest)
        return copy.deepcopy(saved)

    def apply_problem_solution_review(
        self,
        *,
        candidates: list[dict[str, Any]],
        review_event: dict[str, Any],
        expected_revision: int | None,
        bundle_writes: list[dict[str, Any]] | None = None,
        bundle_removals: list[str] | None = None,
        rewrite_manifest: bool = True,
        failure_injector: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Apply one review decision and all affected bundles as one recoverable transaction."""

        candidate_rows = self._normalize_problem_solution_candidates(candidates)
        event = self._normalize_problem_solution_review_event(review_event)
        event_id = str(event.get("review_event_id") or "")
        removal_ids: list[str] = []
        seen_removals: set[str] = set()
        for raw_bundle_id in list(bundle_removals or []):
            bundle_id = str(raw_bundle_id or "").strip()
            self._problem_solution_bundle_path(bundle_id)
            if bundle_id not in seen_removals:
                seen_removals.add(bundle_id)
                removal_ids.append(bundle_id)

        write_inputs: list[dict[str, Any]] = []
        write_ids: set[str] = set()
        for raw_bundle in list(bundle_writes or []):
            payload = copy.deepcopy(dict(raw_bundle or {}))
            bundle_id = str(payload.get("bundle_id") or "").strip()
            self._problem_solution_bundle_path(bundle_id)
            if bundle_id in write_ids:
                raise ValueError(f"bundle_id duplicado: {bundle_id}")
            write_ids.add(bundle_id)
            write_inputs.append(payload)
        overlap = write_ids.intersection(seen_removals)
        if overlap:
            raise ValueError(f"bundle_id no puede eliminarse y escribirse: {sorted(overlap)[0]}")

        def checkpoint(phase: str) -> None:
            if failure_injector is not None:
                failure_injector(phase)

        with self._problem_solution_record_transaction_lock():
            state = self._load_problem_solution_state()
            self._assert_problem_solution_revision(state, expected_revision)
            next_state = copy.deepcopy(state)
            state_changed = False
            if not self._rows_equal(candidate_rows, next_state.get("candidate_links") or []):
                next_state["candidate_links"] = candidate_rows
                state_changed = True

            review_events = [
                copy.deepcopy(dict(item))
                for item in list(next_state.get("review_events") or [])
                if isinstance(item, dict)
            ]
            existing_event = next(
                (item for item in review_events if str(item.get("review_event_id") or "") == event_id),
                None,
            )
            if existing_event is not None and not self._rows_equal(existing_event, event):
                raise RuntimeError(f"problem_solution_review_conflict:{event_id}")
            if existing_event is None:
                review_events.append(event)
                next_state["review_events"] = review_events
                state_changed = True

            bundle_ids = {
                str(item).strip()
                for item in list(next_state.get("bundle_ids") or [])
                if str(item).strip()
            }
            removal_operations: list[tuple[str, Path]] = []
            write_operations: list[tuple[str, Path, dict[str, Any]]] = []
            record_updates: dict[str, StagingProblemRecord] = {}
            record_originals: dict[str, dict[str, Any]] = {}
            record_paths: dict[str, Path] = {}

            def record_for_update(record_id: str) -> StagingProblemRecord | None:
                clean_record_id = str(record_id or "").strip()
                if not clean_record_id:
                    return None
                if clean_record_id in record_updates:
                    return record_updates[clean_record_id]
                current_record = self.get_record(clean_record_id)
                if current_record is None:
                    return None
                cloned = StagingProblemRecord.from_dict(current_record.to_dict())
                record_updates[clean_record_id] = cloned
                record_originals[clean_record_id] = current_record.to_dict()
                record_paths[clean_record_id] = self._problem_solution_record_storage_path(clean_record_id)
                return cloned

            bundle_artifact_keys = (
                "problem_solution_bundle_id",
                "problem_solution_bundle_path",
                "problem_solution_bundle_fingerprint",
                "problem_solution_bundle_revision",
                "problem_solution_bundle_status",
            )

            for bundle_id in removal_ids:
                path = self._problem_solution_bundle_path(bundle_id)
                current = self.read_problem_solution_bundle(bundle_id)
                if current is None and not path.exists() and bundle_id not in bundle_ids:
                    continue
                removal_operations.append((bundle_id, path))
                bundle_ids.discard(bundle_id)
                state_changed = True
                problem_ref = dict((current or {}).get("problem_ref") or {})
                record_id = str(problem_ref.get("record_id") or "").strip()
                record = record_for_update(record_id)
                if record is None:
                    continue
                artifacts = dict(record.artifacts or {})
                if str(artifacts.get("problem_solution_bundle_id") or "") != bundle_id:
                    continue
                for key in bundle_artifact_keys:
                    artifacts.pop(key, None)
                record.artifacts = artifacts

            write_record_ids: set[str] = set()
            for payload in write_inputs:
                if str(payload.get("schema_version") or "") != PROMOTION_BUNDLE_SCHEMA_VERSION:
                    raise ValueError("solution_bundle:invalid_schema_version")
                bundle_id = str(payload.get("bundle_id") or "").strip()
                path = self._problem_solution_bundle_path(bundle_id)
                problem_ref = dict(payload.get("problem_ref") or {})
                record_id = str(problem_ref.get("record_id") or "").strip()
                if not record_id:
                    raise ValueError("solution_bundle:missing_problem_record_id")
                if record_id in write_record_ids:
                    raise ValueError(f"solution_bundle:multiple_bundles_for_record:{record_id}")
                write_record_ids.add(record_id)
                record = record_for_update(record_id)
                if record is None:
                    raise KeyError(record_id)

                current = self.read_problem_solution_bundle(bundle_id)
                current_revision = int((current or {}).get("revision") or 0)
                unchanged = False
                if current is not None:
                    comparison = copy.deepcopy(payload)
                    comparison["revision"] = current_revision
                    comparison["bundle_fingerprint"] = bundle_fingerprint(comparison)
                    unchanged = self._rows_equal(comparison, current)
                if unchanged:
                    saved = copy.deepcopy(current or payload)
                else:
                    requested_revision = int(payload.get("revision") or 0)
                    payload["revision"] = (
                        max(current_revision + 1, requested_revision)
                        if current_revision
                        else max(1, requested_revision)
                    )
                    dependency_issues = self._freeze_problem_solution_dependencies(payload, next_state)
                    if dependency_issues:
                        raise ValueError(";".join(dependency_issues))
                    payload["bundle_fingerprint"] = bundle_fingerprint(payload)
                    if str(payload.get("status") or "") == PROMOTABLE_BUNDLE_STATUS:
                        issues = self.problem_solution_bundle_issues(
                            payload,
                            record=record,
                            state=next_state,
                        )
                        if issues:
                            raise ValueError(";".join(issues))
                    saved = payload
                    write_operations.append((bundle_id, path, saved))

                if bundle_id not in bundle_ids:
                    state_changed = True
                bundle_ids.add(bundle_id)
                record.artifacts = {
                    **dict(record.artifacts or {}),
                    "problem_solution_bundle_id": bundle_id,
                    "problem_solution_bundle_path": str(path),
                    "problem_solution_bundle_fingerprint": str(saved.get("bundle_fingerprint") or ""),
                    "problem_solution_bundle_revision": int(saved.get("revision") or 0),
                    "problem_solution_bundle_status": str(saved.get("status") or ""),
                }

            normalized_bundle_ids = sorted(bundle_ids)
            if not self._rows_equal(normalized_bundle_ids, next_state.get("bundle_ids") or []):
                next_state["bundle_ids"] = normalized_bundle_ids
                state_changed = True
            changed_record_ids = [
                record_id
                for record_id, record in record_updates.items()
                if not self._rows_equal(record.to_dict(), record_originals[record_id])
            ]
            changed = bool(state_changed or removal_operations or write_operations or changed_record_ids)
            if not changed:
                return self.problem_solution_snapshot()

            next_state["revision"] = int(state.get("revision") or 0) + 1
            transaction_paths: list[Path] = [self.problem_solution_state_path]
            transaction_paths.extend(path for _bundle_id, path in removal_operations)
            transaction_paths.extend(path for _bundle_id, path, _payload in write_operations)
            transaction_paths.extend(record_paths[record_id] for record_id in changed_record_ids)
            if rewrite_manifest and changed_record_ids:
                transaction_paths.append(self.manifest_path)
            snapshots = self._problem_solution_transaction_snapshots(transaction_paths)
            self._write_problem_solution_transaction_journal(snapshots, phase="prepared")

            try:
                checkpoint("journal_prepared")
                for _bundle_id, path in removal_operations:
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                checkpoint("bundle_removals_applied")

                for _bundle_id, path, payload in write_operations:
                    self._atomic_write_json(path, payload)
                checkpoint("bundle_writes_applied")

                for record_id in changed_record_ids:
                    self._atomic_write_json(record_paths[record_id], record_updates[record_id].to_dict())
                if changed_record_ids:
                    self._invalidate_records_cache()
                checkpoint("record_attachments_applied")

                self._write_problem_solution_state(next_state)
                checkpoint("state_persisted")

                if rewrite_manifest and changed_record_ids:
                    self.rewrite_manifest()
                checkpoint("manifest_persisted")

                self._write_problem_solution_transaction_journal(snapshots, phase="committed")
                try:
                    self.problem_solution_transaction_journal_path.unlink()
                except OSError:
                    pass
            except BaseException:
                try:
                    self._restore_problem_solution_transaction_snapshots(snapshots)
                except Exception as rollback_exc:
                    raise RuntimeError("problem_solution_transaction_rollback_failed") from rollback_exc
                try:
                    self.problem_solution_transaction_journal_path.unlink()
                except FileNotFoundError:
                    pass
                raise
        return self.problem_solution_snapshot()

    def bundle_for_record(self, record_id: str) -> dict[str, Any] | None:
        record = self.get_record(record_id)
        if record is None:
            return None
        artifacts = dict(record.artifacts or {})
        bundle_id = str(artifacts.get("problem_solution_bundle_id") or "").strip()
        if bundle_id:
            return self.read_problem_solution_bundle(bundle_id)
        raw_path = str(artifacts.get("problem_solution_bundle_path") or "").strip()
        if not raw_path:
            return None
        try:
            path = Path(raw_path).expanduser().resolve()
            root = self.problem_solution_bundles_dir.resolve()
            path.relative_to(root)
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return payload if isinstance(payload, dict) else None

    def remove_problem_solution_bundle(
        self,
        bundle_id: str,
        expected_revision: int | None = None,
        *,
        rewrite_manifest: bool = True,
    ) -> dict[str, Any]:
        """Remove an obsolete reviewed bundle while preserving its review events."""

        path = self._problem_solution_bundle_path(bundle_id)
        record_id = ""
        with self._problem_solution_lock():
            state = self._load_problem_solution_state()
            self._assert_problem_solution_revision(state, expected_revision)
            current = self.read_problem_solution_bundle(bundle_id)
            problem_ref = dict((current or {}).get("problem_ref") or {})
            record_id = str(problem_ref.get("record_id") or "").strip()
            if current is not None or path.exists():
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                state["bundle_ids"] = [
                    str(item)
                    for item in list(state.get("bundle_ids") or [])
                    if str(item) != str(bundle_id)
                ]
                state["revision"] = int(state.get("revision") or 0) + 1
                self._write_problem_solution_state(state)
        if record_id:
            with self._record_write_lock():
                record = self.get_record(record_id)
                if record is not None:
                    artifacts = dict(record.artifacts or {})
                    if str(artifacts.get("problem_solution_bundle_id") or "") == str(bundle_id):
                        for key in self.managed_problem_solution_artifact_keys:
                            artifacts.pop(key, None)
                        record.artifacts = artifacts
                        self._upsert_record_unlocked(
                            record,
                            rewrite_manifest=rewrite_manifest,
                            preserve_current_problem_solution_artifacts=False,
                        )
        return self.problem_solution_snapshot()

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def problem_solution_bundle_issues(
        self,
        bundle: dict[str, Any],
        *,
        record: StagingProblemRecord | None = None,
        state: dict[str, Any] | None = None,
    ) -> list[str]:
        issues = list(validate_confirmed_bundle(bundle))
        stored_fingerprint = str(bundle.get("bundle_fingerprint") or "").strip()
        if not stored_fingerprint or stored_fingerprint != bundle_fingerprint(bundle):
            issues.append("solution_bundle:stale_bundle_fingerprint")

        bundle_scope = dict(bundle.get("scope") or {})
        expected_scope = self._problem_solution_scope()
        for key in ("book_code", "instance_type", "exercise_set_id"):
            actual = str(bundle_scope.get(key) or "").strip()
            expected = str(expected_scope.get(key) or "").strip()
            if not actual:
                issues.append(f"solution_bundle:missing_scope:{key}")
            elif expected and actual != expected:
                issues.append(f"solution_bundle:scope_mismatch:{key}")

        provenance = dict(bundle.get("provenance") or {})
        for key in ("structure_map_version", "box_review_version", "linker_version"):
            if not str(provenance.get(key) or "").strip():
                issues.append(f"solution_bundle:missing_provenance:{key}")

        structure = dict(getattr(self.context, "problem_solution_structure", {}) or {})
        external_required = str(structure.get("solution_status") or "").strip().lower() == "external_source"
        document_relation = dict(bundle.get("document_relation") or {})
        relation_is_external = bool(document_relation.get("external"))
        if external_required and not relation_is_external:
            issues.append("solution_bundle:external_document_required")
        if external_required or relation_is_external:
            if str(document_relation.get("status") or "").strip().lower() != "confirmed":
                issues.append("solution_bundle:external_document_unconfirmed")
            document_reference = str(
                document_relation.get("document_id")
                or document_relation.get("document_reference")
                or document_relation.get("source_pdf_id")
                or document_relation.get("source_pdf_path")
                or ""
            ).strip()
            if not document_reference:
                issues.append("solution_bundle:external_document_reference_missing")

        problem_ref = dict(bundle.get("problem_ref") or {})
        record_id = str(problem_ref.get("record_id") or "").strip()
        record = record or (self.get_record(record_id) if record_id else None)
        if record is None:
            issues.append("solution_bundle:missing_problem_record")
        else:
            expected_source = str(problem_ref.get("source_fingerprint") or "").strip()
            if expected_source and expected_source != problem_source_fingerprint(record):
                issues.append("solution_bundle:stale_problem_source")

        current_state = state if isinstance(state, dict) else self._load_problem_solution_state()
        solution_units_by_id = {
            str(unit.get("solution_unit_id") or unit.get("unit_id") or "").strip(): dict(unit)
            for unit in list(current_state.get("solution_units") or [])
            if isinstance(unit, dict)
        }

        bundle_id = str(bundle.get("bundle_id") or "").strip()
        persisted = self.read_problem_solution_bundle(bundle_id) if bundle_id else None
        is_persisted_payload = bool(
            persisted
            and str((persisted or {}).get("bundle_fingerprint") or "")
            == str(bundle.get("bundle_fingerprint") or "")
        )
        if is_persisted_payload:
            issues.extend(self._problem_solution_dependency_issues(bundle, current_state))

        for solution in list(bundle.get("solutions") or []):
            if not isinstance(solution, dict):
                continue
            solution_id = str(solution.get("solution_id") or "unknown")
            solution_unit_id = str(solution.get("solution_unit_id") or "").strip()
            expected_unit_source = str(solution.get("source_fingerprint") or "").strip()
            if expected_unit_source:
                current_unit = solution_units_by_id.get(solution_unit_id)
                if current_unit is None:
                    issues.append(f"solution_bundle:missing_solution_unit:{solution_unit_id or solution_id}")
                elif expected_unit_source != unit_source_fingerprint(current_unit):
                    issues.append(f"solution_bundle:stale_solution_unit:{solution_unit_id or solution_id}")
            for fragment in list(solution.get("fragments") or []):
                if not isinstance(fragment, dict):
                    continue
                fragment_id = str(fragment.get("fragment_id") or "unknown")
                raw_path = str(fragment.get("crop_path") or "").strip()
                if not raw_path:
                    continue
                path = Path(raw_path).expanduser()
                if not path.is_file():
                    issues.append(f"solution_bundle:missing_asset:{solution_id}:{fragment_id}")
                    continue
                expected_hash = str(fragment.get("sha256") or "").strip().lower()
                if expected_hash.startswith("sha256:"):
                    expected_hash = expected_hash.split(":", 1)[1]
                if expected_hash:
                    try:
                        actual_hash = self._sha256_file(path)
                    except OSError:
                        issues.append(f"solution_bundle:unreadable_asset:{solution_id}:{fragment_id}")
                        continue
                    if actual_hash != expected_hash:
                        issues.append(f"solution_bundle:stale_asset:{solution_id}:{fragment_id}")
        return list(dict.fromkeys(issues))

    def load_server_artifacts(self) -> dict[str, Any]:
        if not self.server_artifacts_path.exists():
            return {
                "schema_version": "pdf_factory_server_artifacts_v1",
                "updated_at": "",
                "page_boxes": {},
                "raw_ocr": {},
            }
        try:
            payload = json.loads(self.server_artifacts_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        page_boxes = payload.get("page_boxes")
        if not isinstance(page_boxes, dict):
            page_boxes = {}
        raw_ocr = payload.get("raw_ocr")
        if not isinstance(raw_ocr, dict):
            raw_ocr = {}
        return {
            "schema_version": "pdf_factory_server_artifacts_v1",
            "updated_at": str(payload.get("updated_at") or ""),
            "page_boxes": page_boxes,
            "raw_ocr": raw_ocr,
        }

    def record_server_page_boxes(
        self,
        *,
        page_number: int,
        boxes: list[dict[str, Any]],
        artifact: dict[str, Any],
        job_id: str = "",
        position: int = 0,
        rewrite_manifest: bool = True,
    ) -> dict[str, Any]:
        page_key = str(int(page_number))
        current = self.load_server_artifacts()
        page_boxes = dict(current.get("page_boxes") or {})
        entry = {
            "schema_version": "pdf_factory_server_page_boxes_v1",
            "page_number": int(page_number),
            "position": int(position or 0),
            "boxes_count": len(list(boxes or [])),
            "artifact": dict(artifact or {}),
            "job_id": str(job_id or ""),
            "updated_at": utc_now_text(),
        }
        page_boxes[page_key] = entry
        payload = {
            "schema_version": "pdf_factory_server_artifacts_v1",
            "updated_at": utc_now_text(),
            "page_boxes": page_boxes,
            "raw_ocr": dict(current.get("raw_ocr") or {}),
        }
        self.server_artifacts_path.parent.mkdir(parents=True, exist_ok=True)
        self.server_artifacts_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if rewrite_manifest:
            self.rewrite_manifest()
        return entry

    def record_server_raw_ocr(
        self,
        *,
        record_id: str,
        raw_ocr: str,
        artifact: dict[str, Any],
        job_id: str = "",
        position: int = 0,
        model: str = "",
        rewrite_manifest: bool = True,
    ) -> dict[str, Any]:
        clean_record_id = str(record_id or "").strip()
        if not clean_record_id:
            raise ValueError("record_id requerido para guardar OCR servidor.")
        current = self.load_server_artifacts()
        raw_index = dict(current.get("raw_ocr") or {})
        entry = {
            "schema_version": "pdf_factory_server_raw_ocr_v1",
            "record_id": clean_record_id,
            "position": int(position or 0),
            "characters": len(str(raw_ocr or "")),
            "artifact": dict(artifact or {}),
            "job_id": str(job_id or ""),
            "model": str(model or ""),
            "updated_at": utc_now_text(),
        }
        raw_index[clean_record_id] = entry
        payload = {
            "schema_version": "pdf_factory_server_artifacts_v1",
            "updated_at": utc_now_text(),
            "page_boxes": dict(current.get("page_boxes") or {}),
            "raw_ocr": raw_index,
        }
        self.server_artifacts_path.parent.mkdir(parents=True, exist_ok=True)
        self.server_artifacts_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        record = self.get_record(clean_record_id)
        if record is not None:
            previous = str(record.raw_ocr or "")
            record.raw_ocr = str(raw_ocr or "")
            record.structured_ocr = {}
            record.artifacts = {
                **dict(record.artifacts or {}),
                "server_raw_ocr_artifact": dict(artifact or {}),
                "server_raw_ocr_job_id": str(job_id or ""),
            }
            record.trace = {
                **dict(record.trace or {}),
                "last_server_raw_ocr": {
                    "schema_version": "server_raw_ocr_trace_v1",
                    "job_id": str(job_id or ""),
                    "model": str(model or ""),
                    "position": int(position or 0),
                    "previous_characters": len(previous),
                    "characters": len(record.raw_ocr),
                    "updated_at": utc_now_text(),
                },
            }
            if record.raw_ocr.strip():
                record.set_step(
                    PipelineStep.OCR,
                    StageStatus.READY,
                    "OCR crudo guardado desde job servidor",
                    source="server_hf_ocr",
                    characters=len(record.raw_ocr),
                )
            else:
                record.set_step(
                    PipelineStep.OCR,
                    StageStatus.PENDING,
                    "OCR crudo vacio desde job servidor",
                    source="server_hf_ocr",
                )
            record.clear_recovered_errors()
            record.sync_status_from_steps()
            self.upsert_record(record, rewrite_manifest=False)
        if rewrite_manifest:
            self.rewrite_manifest()
        return entry

    @staticmethod
    def load_manifest_summary_from_root(root: Path | str) -> dict[str, int] | None:
        """Read staging counters from manifest.json without loading every record file."""
        manifest_path = Path(root) / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        raw_summary = payload.get("summary")
        if not isinstance(raw_summary, dict):
            return None
        records_total = raw_summary.get("records_total", payload.get("records_total", 0))
        defaults = {
            "records_total": records_total,
            "problems_total": records_total,
            "primary_records_total": records_total,
            "crops_found": 0,
            "ocr_done": 0,
            "segments_done": 0,
            "normalized_done": 0,
            "needs_review": 0,
            "human_reviewed": 0,
            "ready": 0,
            "errors": 0,
            "subidos_bd": 0,
        }
        summary: dict[str, int] = {}
        for key, fallback in defaults.items():
            try:
                summary[key] = int(raw_summary.get(key, fallback) or 0)
            except Exception:
                summary[key] = int(fallback or 0)
        return summary

    def load_manifest_summary(self) -> dict[str, int] | None:
        return self.load_manifest_summary_from_root(self.root)

    def _record_path(self, record_id: str) -> Path:
        clean = str(record_id or "").strip()
        if not clean or not self.safe_record_id_re.match(clean):
            raise ValueError(f"record_id invalido para staging: {record_id!r}")
        return self.records_dir / f"{self._record_file_stem(clean)}.json"

    def _legacy_record_path(self, record_id: str) -> Path:
        clean = str(record_id or "").strip()
        if not clean or not self.safe_record_id_re.match(clean):
            raise ValueError(f"record_id invalido para staging: {record_id!r}")
        return self.records_dir / f"{clean}.json"

    def _record_file_stem(self, record_id: str) -> str:
        return self._file_stem_for_dir(self.records_dir, record_id)

    def _file_stem_for_dir(self, directory: Path, record_id: str) -> str:
        clean = str(record_id or "").strip()
        if not clean or not self.safe_record_id_re.match(clean):
            raise ValueError(f"record_id invalido para staging: {record_id!r}")
        directory = Path(directory)
        legacy = directory / f"{clean}.json"
        if len(str(legacy)) < MAX_ARTIFACT_PATH_LEN_SOFT_LIMIT:
            return clean
        available = MAX_ARTIFACT_PATH_LEN_SOFT_LIMIT - len(str(directory)) - len(".json") - 1
        max_len = max(MIN_COMPACT_RECORD_FILE_STEM_LEN, min(MAX_ARTIFACT_RECORD_DIR_LEN, available))
        return compact_artifact_dir_name(clean, max_len=max_len)

    def _record_path_candidates(self, record_id: str) -> list[Path]:
        primary = self._record_path(record_id)
        legacy = self._legacy_record_path(record_id)
        if primary == legacy:
            return [primary]
        return [primary, legacy]

    def _write_record_file(self, record: StagingProblemRecord) -> Path:
        path = self._record_path(record.record_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        for candidate in self._record_path_candidates(record.record_id)[1:]:
            if candidate == path or not candidate.exists():
                continue
            try:
                candidate.unlink()
            except OSError:
                pass
        self._invalidate_records_cache()
        return path

    def artifact_dir(self, kind: str, record_id: str, *, probe_file: str = "latest.json") -> Path:
        clean_kind = re.sub(r"[^A-Za-z0-9._-]+", "_", str(kind or "").strip()).strip("._-")
        if not clean_kind:
            raise ValueError("kind de artefacto requerido")
        clean_record_id = str(record_id or "").strip()
        if not clean_record_id or not self.safe_record_id_re.match(clean_record_id):
            raise ValueError(f"record_id invalido para artefactos: {record_id!r}")
        root = self.root / clean_kind
        legacy = root / clean_record_id
        if len(clean_record_id) <= MAX_ARTIFACT_RECORD_DIR_LEN and len(str(legacy / probe_file)) < MAX_ARTIFACT_PATH_LEN_SOFT_LIMIT:
            return legacy
        available = MAX_ARTIFACT_PATH_LEN_SOFT_LIMIT - len(str(root)) - len(str(probe_file or "latest.json")) - 2
        max_len = max(MIN_COMPACT_ARTIFACT_DIR_STEM_LEN, min(MAX_ARTIFACT_RECORD_DIR_LEN, available))
        return root / compact_artifact_dir_name(clean_record_id, max_len=max_len)

    def _source_identity_key(self, record: StagingProblemRecord) -> str:
        source = dict(record.source or {})
        bbox = source.get("bbox_px") or []
        if isinstance(bbox, tuple):
            bbox = list(bbox)
        try:
            bbox_key = json.dumps([int(v) for v in list(bbox)[:4]], separators=(",", ":"))
        except Exception:
            bbox_key = json.dumps(bbox, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        parts = [
            str(source.get("book_code") or "").strip(),
            str(source.get("instance_type") or "").strip(),
            str(source.get("pdf_path") or "").strip(),
            str(source.get("page_number") or "").strip(),
            bbox_key,
        ]
        if not all(parts[:4]) or bbox_key in ("[]", "null"):
            return ""
        if str(source.get("ocr_input_mode") or "") == "merged_crops_replacement":
            merged_from = source.get("merged_from_record_ids")
            if isinstance(merged_from, list) and merged_from:
                return "|".join([*parts, json.dumps([str(item) for item in merged_from], separators=(",", ":"))])
        return "|".join(parts)

    def metadata_issues(self, record: StagingProblemRecord) -> list[str]:
        source = dict(record.source or {})
        models = dict(record.models or {})
        issues: list[str] = []
        if not str(source.get("book_code") or "").strip():
            issues.append("missing:source.book_code")
        if not str(source.get("instance_type") or "").strip():
            issues.append("missing:source.instance_type")
        if not str(source.get("pdf_path") or "").strip():
            issues.append("missing:source.pdf_path")
        if source.get("page_number") in (None, ""):
            issues.append("missing:source.page_number")
        bbox = source.get("bbox_px")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            issues.append("invalid:source.bbox_px")
        if not str(record.crop_path or "").strip():
            issues.append("missing:crop_path")
        if not models:
            issues.append("missing:models")
        else:
            stages = models.get("stages")
            if isinstance(stages, dict):
                for stage, payload in stages.items():
                    if not isinstance(payload, dict):
                        issues.append(f"invalid:models.stages.{stage}")
                        continue
                    for required in ("model_id", "provider", "version", "fallback"):
                        if not str(payload.get(required) or "").strip():
                            issues.append(f"missing:models.stages.{stage}.{required}")
        if not dict(record.confidence or {}):
            issues.append("missing:confidence")
        if StageStatus.normalize(record.status, default="") not in StageStatus.values():
            issues.append(f"invalid:status:{record.status}")
        return issues

    def _prepare_record_for_write(self, record: StagingProblemRecord) -> StagingProblemRecord:
        record.record_id = str(record.record_id or record.crop_id or "").strip()
        record.crop_id = str(record.crop_id or record.record_id or "").strip()
        self._record_path(record.record_id)
        original_status = record.status
        record.status = StageStatus.normalize(record.status, default="")
        if record.status not in StageStatus.values():
            raise ValueError(f"Estado staging invalido: {original_status!r}")
        record.ensure_pipeline_steps()
        source = dict(record.source or {})
        source.setdefault("book_code", self.context.book_code)
        source.setdefault("instance_type", self.context.instance_type)
        source.setdefault("pdf_path", self.context.pdf_path)
        source.setdefault("crop_id", record.crop_id)
        record.source = source
        record.confidence = dict(record.confidence or {})
        if not record.confidence:
            record.confidence["pdf_box"] = 0.0
        now = utc_now_text()
        issues = self.metadata_issues(record)
        record.audit = {
            **dict(record.audit or {}),
            "contract_version": PIPELINE_CONTRACT_VERSION,
            "metadata_minima": {
                "schema_version": "staging_metadata_audit_v1",
                "complete": not issues,
                "missing_or_invalid": issues,
                "required": dict(self.required_metadata),
                "updated_at": now,
            },
            "identity_key": self._source_identity_key(record),
            "storage_policy": {
                "target": "staging_only",
                "problemas_write_enabled": False,
            },
        }
        record.touch()
        return record

    def validate_contract(self, records: list[StagingProblemRecord] | None = None) -> dict[str, Any]:
        rows = records if records is not None else self.load_records()
        issues: list[dict[str, Any]] = []
        allowed_statuses = StageStatus.values()
        for row in rows:
            row_status = StageStatus.normalize(row.status, default="")
            if row_status not in allowed_statuses:
                issues.append(
                    {
                        "record_id": row.record_id,
                        "issue": f"invalid:status:{row.status}",
                    }
                )
            metadata_issues = self.metadata_issues(row)
            if metadata_issues:
                issues.append(
                    {
                        "record_id": row.record_id,
                        "issue": "metadata_minima_incomplete",
                        "details": metadata_issues,
                    }
                )
            for step in PipelineStep.ORDER:
                step_status = row.step_status(step, default="")
                if step_status not in allowed_statuses:
                    issues.append(
                        {
                            "record_id": row.record_id,
                            "issue": f"invalid:step_status:{step}",
                            "status": row.steps.get(step, {}).get("status"),
                        }
                    )
            storage_policy = dict(dict(row.audit or {}).get("storage_policy") or {})
            if storage_policy.get("problemas_write_enabled") is True:
                issues.append(
                    {
                        "record_id": row.record_id,
                        "issue": "forbidden:problemas_write_enabled",
                    }
                )
        return {
            "schema_version": "pdf_factory_contract_validation_v1",
            "contract_version": PIPELINE_CONTRACT_VERSION,
            "valid": not issues,
            "issues": issues,
            "records_total": len(rows),
            "required_steps": list(PipelineStep.ORDER),
            "allowed_statuses": sorted(allowed_statuses),
            "policy": build_pipeline_contract()["storage_policy"],
        }

    def _merge_human_review_data(self, incoming: StagingProblemRecord, existing: StagingProblemRecord) -> StagingProblemRecord:
        if existing.review and not incoming.review:
            incoming.review = dict(existing.review)
        if existing.training_examples and not incoming.training_examples:
            incoming.training_examples = [dict(item) for item in existing.training_examples]
        if existing.created_at and not incoming.created_at:
            incoming.created_at = existing.created_at
        return incoming

    def _merge_duplicate_record_data(
        self,
        primary: StagingProblemRecord,
        duplicate: StagingProblemRecord,
    ) -> StagingProblemRecord:
        if duplicate.review and not primary.review:
            primary.review = dict(duplicate.review)
        if duplicate.normalized and not primary.normalized:
            primary.normalized = dict(duplicate.normalized)
        if duplicate.raw_ocr and not primary.raw_ocr:
            primary.raw_ocr = duplicate.raw_ocr
        if duplicate.structured_ocr and not primary.structured_ocr:
            primary.structured_ocr = dict(duplicate.structured_ocr)
        if duplicate.figure_segmentation and not primary.figure_segmentation:
            primary.figure_segmentation = dict(duplicate.figure_segmentation)
        if duplicate.artifacts and not primary.artifacts:
            primary.artifacts = dict(duplicate.artifacts)
        if duplicate.golden_sync and not primary.golden_sync:
            primary.golden_sync = dict(duplicate.golden_sync)
        primary.source = {**dict(duplicate.source or {}), **dict(primary.source or {})}
        primary.models = {**dict(duplicate.models or {}), **dict(primary.models or {})}
        primary.confidence = {**dict(duplicate.confidence or {}), **dict(primary.confidence or {})}
        primary.trace = {**dict(duplicate.trace or {}), **dict(primary.trace or {})}
        primary.audit = {**dict(duplicate.audit or {}), **dict(primary.audit or {})}
        primary.steps = {**dict(duplicate.steps or {}), **dict(primary.steps or {})}
        primary_status = StageStatus.normalize(primary.status)
        duplicate_status = StageStatus.normalize(duplicate.status)
        if primary_status == StageStatus.PENDING and duplicate_status in {
            StageStatus.READY,
            StageStatus.NEEDS_REVIEW,
            StageStatus.PROCESSING,
        }:
            primary.status = duplicate_status
        elif primary_status != StageStatus.READY and duplicate_status == StageStatus.READY:
            primary.status = StageStatus.READY
        seen_examples: set[str] = set()
        merged_examples: list[dict[str, Any]] = []
        for item in [*list(primary.training_examples or []), *list(duplicate.training_examples or [])]:
            if not isinstance(item, dict):
                continue
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key in seen_examples:
                continue
            seen_examples.add(key)
            merged_examples.append(dict(item))
        primary.training_examples = merged_examples[-50:]
        primary.errors = sorted(set([*list(primary.errors or []), *list(duplicate.errors or [])]))
        if duplicate.created_at and (not primary.created_at or duplicate.created_at < primary.created_at):
            primary.created_at = duplicate.created_at
        primary.sync_status_from_steps()
        return primary

    def _coalesce_duplicate_identity(
        self,
        record: StagingProblemRecord,
        identity_to_record: dict[str, StagingProblemRecord],
    ) -> StagingProblemRecord:
        identity = self._source_identity_key(record)
        if not identity:
            return record
        existing = identity_to_record.get(identity)
        if existing is None or existing.record_id == record.record_id:
            identity_to_record[identity] = record
            return record
        record = self._merge_human_review_data(record, existing)
        record.record_id = existing.record_id
        record.crop_id = existing.crop_id or record.crop_id
        identity_to_record[identity] = record
        return record

    def _invalidate_records_cache(self) -> None:
        self._records_cache_signature = None
        self._records_cache_entries = None

    def _records_dir_signature(self) -> tuple[tuple[str, int, int], ...]:
        return self._scan_records_dir_signature()

    def _scan_records_dir_signature(self) -> tuple[tuple[str, int, int], ...]:
        signature: list[tuple[str, int, int]] = []
        for path in sorted(self.records_dir.glob("*.json"), key=lambda item: item.name.lower()):
            try:
                stat = path.stat()
            except OSError:
                continue
            signature.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
        return tuple(signature)

    @staticmethod
    def _clone_record_entries(entries: list[tuple[Path, StagingProblemRecord]]) -> list[tuple[Path, StagingProblemRecord]]:
        return [(Path(path), copy.deepcopy(record)) for path, record in entries]

    def _load_record_entries(
        self,
        signature: tuple[tuple[str, int, int], ...] | None = None,
    ) -> list[tuple[Path, StagingProblemRecord]]:
        signature = signature if signature is not None else self._records_dir_signature()
        if self._records_cache_signature == signature and self._records_cache_entries is not None:
            return self._clone_record_entries(self._records_cache_entries)
        rows: list[tuple[Path, StagingProblemRecord]] = []
        for path in sorted(self.records_dir.glob("*.json"), key=lambda item: item.name.lower()):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                record = StagingProblemRecord.from_dict(payload)
                if not record.record_id:
                    record.record_id = path.stem
                if not record.crop_id:
                    record.crop_id = record.record_id
                rows.append((path, record))
        self._records_cache_signature = signature
        self._records_cache_entries = self._clone_record_entries(rows)
        return rows

    @staticmethod
    def _int_sort_value(value: Any, default: int = 10**9) -> int:
        try:
            number = int(value)
        except Exception:
            return default
        return number if number >= 0 else default

    @classmethod
    def _record_sort_key(cls, record: StagingProblemRecord) -> tuple[int, int, int, int, int, str]:
        source = dict(record.source or {})
        bbox = source.get("bbox_px") or []
        y1 = cls._int_sort_value(bbox[1] if isinstance(bbox, (list, tuple)) and len(bbox) > 1 else None)
        x1 = cls._int_sort_value(bbox[0] if isinstance(bbox, (list, tuple)) and len(bbox) > 0 else None)
        return (
            cls._int_sort_value(source.get("page_number") or source.get("source_page_number")),
            cls._int_sort_value(source.get("source_order")),
            cls._int_sort_value(source.get("box_index") or source.get("page_problem_index") or source.get("problem_index")),
            y1,
            x1,
            str(record.record_id or record.crop_id or ""),
        )

    def load_records(
        self,
        signature: tuple[tuple[str, int, int], ...] | None = None,
    ) -> list[StagingProblemRecord]:
        return sorted((record for _path, record in self._load_record_entries(signature)), key=self._record_sort_key)

    def load_record_entries(
        self,
        signature: tuple[tuple[str, int, int], ...] | None = None,
    ) -> list[tuple[Path, StagingProblemRecord]]:
        return sorted(self._load_record_entries(signature), key=lambda item: self._record_sort_key(item[1]))

    def identity_map_for_records(
        self,
        records: list[StagingProblemRecord] | None = None,
    ) -> dict[str, StagingProblemRecord]:
        rows = records if records is not None else self.load_records()
        by_identity: dict[str, StagingProblemRecord] = {}
        for row in rows:
            identity = self._source_identity_key(row)
            if identity:
                by_identity[identity] = row
        return by_identity

    def _deduplicate_record_entries(
        self,
        entries: list[tuple[Path, StagingProblemRecord]],
    ) -> tuple[list[StagingProblemRecord], dict[str, Any]]:
        by_identity: dict[str, tuple[Path, StagingProblemRecord]] = {}
        canonical_by_path: dict[Path, StagingProblemRecord] = {}
        duplicate_paths: set[Path] = set()
        duplicate_identities: set[str] = set()
        for path, record in entries:
            identity = self._source_identity_key(record)
            if not identity:
                canonical_by_path[path] = record
                continue
            existing = by_identity.get(identity)
            if existing is None:
                by_identity[identity] = (path, record)
                canonical_by_path[path] = record
                continue
            canonical_path, canonical_record = existing
            merged = self._merge_duplicate_record_data(canonical_record, record)
            by_identity[identity] = (canonical_path, merged)
            canonical_by_path[canonical_path] = merged
            duplicate_paths.add(path)
            duplicate_identities.add(identity)

        repaired = 0
        if duplicate_paths:
            canonical_output_paths: set[Path] = set()
            for record in canonical_by_path.values():
                prepared = self._prepare_record_for_write(record)
                output_path = self._write_record_file(prepared)
                canonical_output_paths.add(output_path.resolve())
            for path in duplicate_paths:
                try:
                    resolved = path.resolve()
                except Exception:
                    resolved = path
                if resolved in canonical_output_paths:
                    continue
                try:
                    path.unlink()
                    self._invalidate_records_cache()
                    repaired += 1
                except FileNotFoundError:
                    pass

        rows = self.load_records() if duplicate_paths else sorted((record for _path, record in entries), key=self._record_sort_key)
        return rows, {
            "duplicate_identity_keys_before_repair": sorted(duplicate_identities),
            "duplicate_records_repaired": repaired,
        }

    def summarize_records(
        self,
        records: list[StagingProblemRecord] | None = None,
        *,
        crop_exists_resolver: Any | None = None,
    ) -> dict[str, int]:
        rows = records if records is not None else self.load_records()
        continuation_ids = self._summary_continuation_record_ids(rows)
        summary = {
            "raw_records_total": len(rows),
            "records_total": 0,
            "problems_total": 0,
            "primary_records_total": 0,
            "crops_found": 0,
            "ocr_done": 0,
            "segments_done": 0,
            "normalized_done": 0,
            "needs_review": 0,
            "human_reviewed": 0,
            "ready": 0,
            "errors": 0,
            "subidos_bd": 0,
        }
        for row in rows:
            is_continuation = self._is_summary_continuation_record(row, continuation_ids)
            if not is_continuation:
                summary["records_total"] += 1
                summary["problems_total"] += 1
                summary["primary_records_total"] += 1
            crop_exists = False
            if row.crop_path:
                if crop_exists_resolver is not None:
                    try:
                        crop_exists = bool(crop_exists_resolver(row.crop_path))
                    except Exception:
                        crop_exists = False
                else:
                    crop_exists = Path(row.crop_path).exists()
            if crop_exists:
                if not is_continuation:
                    summary["crops_found"] += 1
            if row.raw_ocr and not is_continuation:
                summary["ocr_done"] += 1
            if row.figure_segmentation and not is_continuation:
                summary["segments_done"] += 1
            if row.normalized and not is_continuation:
                summary["normalized_done"] += 1
            status = StageStatus.normalize(row.status)
            review_status = StageStatus.normalize(str(dict(row.review or {}).get("review_status") or ""))
            if is_continuation:
                pass
            elif status == StageStatus.READY or review_status == StageStatus.READY:
                summary["ready"] += 1
            elif status == StageStatus.HUMAN_REVIEWED or review_status == StageStatus.HUMAN_REVIEWED:
                summary["human_reviewed"] += 1
            elif status == StageStatus.NEEDS_REVIEW or review_status == StageStatus.NEEDS_REVIEW:
                summary["needs_review"] += 1
            if not is_continuation and (status == StageStatus.ERROR or row.errors):
                summary["errors"] += 1
            db_promotion = dict(dict(row.audit or {}).get("db_promotion") or {})
            if not is_continuation and (db_promotion.get("problem_id") or dict(row.artifacts or {}).get("db_problem_id")):
                summary["subidos_bd"] += 1
        return summary

    def _summary_continuation_record_ids(self, rows: list[StagingProblemRecord]) -> set[str]:
        ids: set[str] = set()
        for row in rows:
            normalized = row.normalized if isinstance(row.normalized, dict) else {}
            continuations = normalized.get("continuaciones_fusionadas")
            if not isinstance(continuations, list):
                continue
            for item in continuations:
                if not isinstance(item, dict):
                    continue
                for key in ("record_id", "crop_id"):
                    value = str(item.get(key) or "").strip()
                    if value:
                        ids.add(value)
        return ids

    def _is_summary_continuation_record(
        self,
        row: StagingProblemRecord,
        continuation_ids: set[str] | None = None,
    ) -> bool:
        ids = continuation_ids or set()
        if str(row.record_id or "").strip() in ids or str(row.crop_id or "").strip() in ids:
            return True
        source = row.source if isinstance(row.source, dict) else {}
        if str(source.get("replaced_by_record_id") or "").strip():
            return True
        if str(source.get("merged_into_record_id") or "").strip():
            return True
        normalized = row.normalized if isinstance(row.normalized, dict) else {}
        continuation = normalized.get("continuacion") if isinstance(normalized.get("continuacion"), dict) else {}
        if continuation_flags_enabled(continuation):
            return True
        for value in (
            row.raw_ocr,
            normalized.get("latex_rendered_item"),
            normalized.get("enunciado_latex"),
        ):
            if has_continuation_marker(value):
                return True
        return False

    def _continuation_text_for_record(self, record: StagingProblemRecord) -> str:
        normalized = record.normalized if isinstance(record.normalized, dict) else {}
        for value in (
            record.raw_ocr,
            normalized.get("enunciado_latex"),
            normalized.get("latex_rendered_item"),
        ):
            text = str(value or "").strip()
            if text:
                return strip_continuation_marker(text)
        return ""

    def _continuation_rows_for_parent(
        self,
        parent: StagingProblemRecord,
        rows: list[StagingProblemRecord] | None = None,
    ) -> list[StagingProblemRecord]:
        parent_id = str(parent.record_id or "").strip()
        if not parent_id:
            return []
        all_rows = rows if rows is not None else self.load_records()
        by_id = {str(row.record_id or ""): row for row in all_rows if str(row.record_id or "")}
        out: list[StagingProblemRecord] = []
        seen: set[str] = set()

        def add(row: StagingProblemRecord | None, *, allow_unmarked: bool = False) -> None:
            if row is None:
                return
            row_id = str(row.record_id or "").strip()
            if not row_id or row_id == parent_id or row_id in seen:
                return
            if not allow_unmarked and not self._is_summary_continuation_record(row):
                return
            out.append(row)
            seen.add(row_id)

        normalized = parent.normalized if isinstance(parent.normalized, dict) else {}
        fused = normalized.get("continuaciones_fusionadas") if isinstance(normalized.get("continuaciones_fusionadas"), list) else []
        for item in fused:
            if isinstance(item, dict):
                add(by_id.get(str(item.get("record_id") or "").strip()), allow_unmarked=True)

        for row in all_rows:
            row_normalized = row.normalized if isinstance(row.normalized, dict) else {}
            continuation = row_normalized.get("continuacion") if isinstance(row_normalized.get("continuacion"), dict) else {}
            if str(continuation.get("parent_record_id") or "").strip() == parent_id:
                add(row, allow_unmarked=True)

        try:
            parent_index = next(index for index, row in enumerate(all_rows) if str(row.record_id or "") == parent_id)
        except StopIteration:
            parent_index = -1
        if parent_index >= 0:
            for row in all_rows[parent_index + 1 :]:
                if not self._is_summary_continuation_record(row):
                    break
                add(row)
        return out

    def _attach_detected_continuations(
        self,
        record: StagingProblemRecord,
        rows: list[StagingProblemRecord] | None = None,
    ) -> None:
        if self._is_summary_continuation_record(record):
            return
        rows = rows if rows is not None else self.load_records()
        rows = [row for row in rows if str(row.record_id or "") != str(record.record_id or "")] + [record]
        rows = sorted(rows, key=self._record_sort_key)
        continuation_rows = self._continuation_rows_for_parent(record, rows)
        if not continuation_rows:
            return
        normalized = record.normalized if isinstance(record.normalized, dict) else {}
        existing = normalized.get("continuaciones_fusionadas") if isinstance(normalized.get("continuaciones_fusionadas"), list) else []
        by_id: dict[str, dict[str, Any]] = {}
        for item in existing:
            if not isinstance(item, dict):
                continue
            key = str(item.get("record_id") or item.get("crop_id") or "").strip()
            if key:
                by_id[key] = dict(item)
        for row in continuation_rows:
            key = str(row.record_id or row.crop_id or "").strip()
            if not key:
                continue
            source = dict(row.source or {})
            figure = row.figure_segmentation if isinstance(row.figure_segmentation, dict) else {}
            try:
                segments_total = int(figure.get("segments_total") or 0)
            except Exception:
                segments_total = 0
            by_id[key] = {
                "record_id": str(row.record_id or ""),
                "crop_id": str(row.crop_id or ""),
                "crop_name": Path(str(row.crop_path or "")).name,
                "page_number": source.get("page_number", source.get("source_page_number")),
                "bbox_px": source.get("bbox_px") if isinstance(source.get("bbox_px"), list) else None,
                "has_figure": segments_total > 0,
                "segments_total": segments_total,
                "texto_fusionado": self._continuation_text_for_record(row),
            }
        record.normalized = {
            **dict(normalized),
            "continuaciones_fusionadas": list(by_id.values()),
        }

    def repair_detected_continuation_links(self) -> list[StagingProblemRecord]:
        rows = self.load_records()
        continuation_ids = self._summary_continuation_record_ids(rows)
        changed: list[StagingProblemRecord] = []
        for record in rows:
            if self._is_summary_continuation_record(record, continuation_ids):
                continue
            before = json.dumps(
                (record.normalized or {}).get("continuaciones_fusionadas") or [],
                ensure_ascii=False,
                sort_keys=True,
            )
            self._attach_detected_continuations(record, rows)
            after = json.dumps(
                (record.normalized or {}).get("continuaciones_fusionadas") or [],
                ensure_ascii=False,
                sort_keys=True,
            )
            if before != after:
                record.touch()
                changed.append(record)
        if changed:
            self.upsert_many(changed)
        return changed

    def get_record(self, record_id: str) -> StagingProblemRecord | None:
        try:
            candidates = self._record_path_candidates(record_id)
        except ValueError:
            return None
        for path in candidates:
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                record = StagingProblemRecord.from_dict(payload)
                if str(record.record_id or "") == str(record_id or ""):
                    return record
                if str(record.crop_id or "") == str(record_id or ""):
                    return record
        wanted = str(record_id or "")
        for _path, record in self._load_record_entries():
            if str(record.record_id or "") == wanted or str(record.crop_id or "") == wanted:
                return record
        return None

    def delete_record(self, record_id: str, *, rewrite_manifest: bool = True) -> int:
        with self._record_write_lock():
            return self._delete_record_unlocked(record_id, rewrite_manifest=rewrite_manifest)

    def _delete_record_unlocked(self, record_id: str, *, rewrite_manifest: bool = True) -> int:
        try:
            candidates = self._record_path_candidates(record_id)
        except ValueError:
            return 0
        wanted = str(record_id or "").strip()
        deleted = 0
        seen: set[Path] = set()
        for path in candidates:
            seen.add(path.resolve())
            try:
                path.unlink()
                deleted += 1
            except FileNotFoundError:
                pass
        for path, record in self._load_record_entries():
            if str(record.record_id or "") != wanted and str(record.crop_id or "") != wanted:
                continue
            try:
                resolved = path.resolve()
            except Exception:
                resolved = path
            if resolved in seen:
                continue
            try:
                path.unlink()
                deleted += 1
            except FileNotFoundError:
                pass
        if deleted:
            self._invalidate_records_cache()
            if rewrite_manifest:
                self.rewrite_manifest()
        return deleted

    def upsert_record(
        self,
        record: StagingProblemRecord,
        *,
        rewrite_manifest: bool = True,
        existing_by_identity: dict[str, StagingProblemRecord] | None = None,
    ) -> StagingProblemRecord:
        with self._record_write_lock():
            return self._upsert_record_unlocked(
                record,
                rewrite_manifest=rewrite_manifest,
                existing_by_identity=existing_by_identity,
                preserve_current_problem_solution_artifacts=True,
            )

    def _upsert_record_unlocked(
        self,
        record: StagingProblemRecord,
        *,
        rewrite_manifest: bool = True,
        existing_by_identity: dict[str, StagingProblemRecord] | None = None,
        preserve_current_problem_solution_artifacts: bool = True,
    ) -> StagingProblemRecord:
        existing_by_identity = existing_by_identity if existing_by_identity is not None else self.identity_map_for_records()
        record = self._prepare_record_for_write(record)
        record = self._coalesce_duplicate_identity(record, existing_by_identity)
        record = self._prepare_record_for_write(record)
        if preserve_current_problem_solution_artifacts:
            current = self.get_record(record.record_id)
            if current is not None:
                current_artifacts = dict(current.artifacts or {})
                next_artifacts = dict(record.artifacts or {})
                for key in self.managed_problem_solution_artifact_keys:
                    if key in current_artifacts:
                        next_artifacts[key] = copy.deepcopy(current_artifacts[key])
                    else:
                        next_artifacts.pop(key, None)
                record.artifacts = next_artifacts
        self._write_record_file(record)
        if rewrite_manifest:
            self.rewrite_manifest()
        return record

    def upsert_many(
        self,
        records: list[StagingProblemRecord],
        *,
        existing_by_identity: dict[str, StagingProblemRecord] | None = None,
    ) -> None:
        with self._record_write_lock():
            self._upsert_many_unlocked(records, existing_by_identity=existing_by_identity)

    def _upsert_many_unlocked(
        self,
        records: list[StagingProblemRecord],
        *,
        existing_by_identity: dict[str, StagingProblemRecord] | None = None,
    ) -> None:
        existing_by_identity = existing_by_identity if existing_by_identity is not None else self.identity_map_for_records()
        prepared_by_id: dict[str, StagingProblemRecord] = {}
        for record in records:
            record = self._prepare_record_for_write(record)
            record = self._coalesce_duplicate_identity(record, existing_by_identity)
            record = self._prepare_record_for_write(record)
            current = self.get_record(record.record_id)
            if current is not None:
                current_artifacts = dict(current.artifacts or {})
                next_artifacts = dict(record.artifacts or {})
                for key in self.managed_problem_solution_artifact_keys:
                    if key in current_artifacts:
                        next_artifacts[key] = copy.deepcopy(current_artifacts[key])
                    else:
                        next_artifacts.pop(key, None)
                record.artifacts = next_artifacts
            prepared_by_id[record.record_id] = record
        for record in prepared_by_id.values():
            self._write_record_file(record)
        self.rewrite_manifest()

    def rewrite_manifest(self) -> None:
        rows, dedupe_audit = self._deduplicate_record_entries(self._load_record_entries())
        by_status: dict[str, int] = {}
        by_step_status: dict[str, dict[str, int]] = {step: {} for step in PipelineStep.ORDER}
        identity_counts: dict[str, int] = {}
        metadata_complete = 0
        for row in rows:
            status = StageStatus.normalize(row.status)
            by_status[status] = by_status.get(status, 0) + 1
            if not self.metadata_issues(row):
                metadata_complete += 1
            identity = self._source_identity_key(row)
            if identity:
                identity_counts[identity] = identity_counts.get(identity, 0) + 1
            for step in PipelineStep.ORDER:
                step_status = row.step_status(step)
                bucket = by_step_status.setdefault(step, {})
                bucket[step_status] = bucket.get(step_status, 0) + 1
        duplicate_identity_keys = sorted(key for key, count in identity_counts.items() if count > 1)
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "contract_version": PIPELINE_CONTRACT_VERSION,
            "updated_at": utc_now_text(),
            "context": self.context.to_dict(),
            "records_total": len(rows),
            "by_status": by_status,
            "by_step_status": by_step_status,
            "summary": self.summarize_records(rows),
            "metadata": {
                "required": dict(self.required_metadata),
                "complete_records": metadata_complete,
                "incomplete_records": len(rows) - metadata_complete,
                "duplicate_identity_keys": duplicate_identity_keys,
                "duplicate_identity_total": len(duplicate_identity_keys),
                "duplicate_identity_keys_before_repair": dedupe_audit["duplicate_identity_keys_before_repair"],
                "duplicate_records_repaired": dedupe_audit["duplicate_records_repaired"],
            },
            "records_dir": "records",
            "policy": {
                "target": "staging_only",
                "never_insert_directly_into_problemas": True,
                "default_status_after_normalization": StageStatus.NEEDS_REVIEW,
                "human_corrections_are_training_data": True,
                "promotion_boundary": {
                    "prepared": True,
                    "enabled": False,
                    "explicit_manual_upload_enabled": True,
                    "target_table": "problemas",
                    "candidate_builder": "InstanceStagingStore.build_promotion_candidate",
                    "requires_ready_review": True,
                    "write_operations": [],
                },
            },
            "model_inventory": _build_model_inventory_manifest(),
            "training_contracts": {
                "schema_version": "pdf_factory_training_contracts_v1",
                "raw_outputs_dir": "raw_outputs",
                "review_outputs_dir": "review_outputs",
                "golden_contracts_dir": "golden_contracts",
                "human_review_training_example_schema": "human_review_training_example_v1",
                "golden_contract_schema": "pdf_factory_golden_contract_v1",
                "targets": [
                    "problem_crops_live",
                    "ocr_golden_live",
                    "segment_training_live",
                    "ocr_normalization_golden_live",
                ],
            },
            "contract": build_pipeline_contract(),
            "contract_validation": self.validate_contract(rows),
            "evaluation_matrix": _build_retraining_evaluation_matrix(),
            "server_storage": self.load_server_artifacts(),
        }
        self.manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def update_review(
        self,
        record_id: str,
        normalized: dict[str, Any],
        notes: str = "",
        *,
        mark_ready: bool = False,
        sync_golden: bool = True,
    ) -> StagingProblemRecord:
        record = self.get_record(record_id)
        if record is None:
            raise KeyError(record_id)
        downstream_state = dict(dict(record.audit or {}).get("downstream_state") or {})
        if downstream_state.get("status") == "invalidated":
            reason = str(downstream_state.get("reason") or "source_changed").strip() or "source_changed"
            raise ValueError(f"Regenera staging antes de guardar revision: {reason}.")
        previous_review = dict(record.review or {})
        history = list(previous_review.get("history") or [])
        machine_normalized_before = dict(record.normalized or {})
        if record.normalized:
            history.append(
                {
                    "updated_at": str(previous_review.get("updated_at") or record.updated_at or ""),
                    "normalized": dict(record.normalized),
                    "notes": str(previous_review.get("notes") or ""),
                }
            )
        record.normalized = _repair_normalized_final_latex_number(dict(normalized or {}), record)
        if (
            "continuaciones_fusionadas" not in record.normalized
            and isinstance(machine_normalized_before.get("continuaciones_fusionadas"), list)
        ):
            record.normalized["continuaciones_fusionadas"] = [
                dict(item) for item in machine_normalized_before.get("continuaciones_fusionadas") or [] if isinstance(item, dict)
            ]
        self._attach_detected_continuations(record)
        review_status = StageStatus.READY if mark_ready else StageStatus.NEEDS_REVIEW
        correction_time = utc_now_text()
        record.training_examples = [
            *[dict(item) for item in list(record.training_examples or [])],
            {
                "schema_version": "human_review_training_example_v1",
                "created_at": correction_time,
                "source_record_id": record.record_id,
                "crop_id": record.crop_id,
                "crop_path": record.crop_path,
                "source": dict(record.source or {}),
                "models": dict(record.models or {}),
                "confidence": dict(record.confidence or {}),
                "machine_normalized_before": machine_normalized_before,
                "human_normalized": dict(record.normalized or {}),
                "notes": str(notes or ""),
                "intended_use": "ocr_normalization_training",
            },
        ][-50:]
        record.trace = {
            **dict(record.trace or {}),
            "last_human_correction": {
                "updated_at": correction_time,
                "reviewer": "human",
                "fields": sorted(str(key) for key in record.normalized.keys()),
                "source_record_id": record.record_id,
                "saved_as_training_example": True,
            },
        }
        record.review = {
            **dict(record.review or {}),
            "review_status": review_status,
            "notes": str(notes or ""),
            "history": history[-20:],
            "training_examples_total": len(record.training_examples),
            "updated_at": correction_time,
        }
        record.status = review_status
        record.set_step(
            PipelineStep.REVIEW,
            review_status,
            "revision humana guardada en staging",
            notes_present=bool(str(notes or "").strip()),
        )
        self._write_review_artifacts(record)
        if sync_golden:
            self._sync_review_to_golden_bases(record, notes=str(notes or ""))
        else:
            record.golden_sync = {
                **dict(record.golden_sync or {}),
                "updated_at": utc_now_text(),
                "status": "deferred",
                "reason": "batch_review_save",
                "targets": {},
                "errors": [],
            }
        self._sync_review_to_normalizer_training_bank(record, review_status=review_status)
        self.upsert_record(record)
        return record

    def _sync_review_to_normalizer_training_bank(self, record: StagingProblemRecord, *, review_status: str) -> None:
        try:
            rewrite_training_index = bool(str(os.environ.get("NORMALIZER_TRAINING_BANK_ROOT") or "").strip())
            if StageStatus.normalize(review_status) != StageStatus.READY:
                manifest = remove_normalizer_training_sample(
                    self.context,
                    record,
                    rewrite_index=rewrite_training_index,
                )
            else:
                rows = self.load_records()
                rows = [row for row in rows if row.record_id != record.record_id] + [record]
                manifest = upsert_normalizer_training_sample(
                    self.context,
                    record,
                    staging_root=self.root,
                    all_records=rows,
                    rewrite_index=rewrite_training_index,
                )
            record.artifacts = {
                **dict(record.artifacts or {}),
                "normalizer_training_bank_manifest": str(manifest.get("manifest_path", "")),
                "normalizer_training_samples_total": int(manifest.get("samples_total") or 0),
                "normalizer_training_ready_to_train": bool(manifest.get("ready_to_train")),
            }
        except Exception as exc:
            record.artifacts = {
                **dict(record.artifacts or {}),
                "normalizer_training_bank_error": str(exc),
            }

    def build_promotion_candidate(self, record_id: str) -> dict[str, Any]:
        record = self.get_record(record_id)
        if record is None:
            raise KeyError(record_id)
        metadata_issues = self.metadata_issues(record)
        blocking_issues = list(metadata_issues)
        normalized = dict(record.normalized or {})
        continuation = normalized.get("continuacion") if isinstance(normalized.get("continuacion"), dict) else {}
        continuation_ids = self._summary_continuation_record_ids(self.load_records())
        if not normalized:
            blocking_issues.append("missing:normalized")
        final_latex = str(normalized.get("latex_rendered_item") or "").strip()
        if normalized and not final_latex:
            blocking_issues.append("missing:final_latex")
        if final_latex and not re.match(r"^\s*\\item\s*\[\s*\\textbf\s*\{\s*\d+\s*\.?\s*\}\s*\]", final_latex):
            blocking_issues.append("invalid:final_latex_item")
        if continuation_flags_enabled(continuation) or self._is_summary_continuation_record(record, continuation_ids):
            blocking_issues.append("continuacion:fusionada_con_anterior")
        downstream_state = dict(dict(record.audit or {}).get("downstream_state") or {})
        if downstream_state.get("status") == "invalidated":
            reason = str(downstream_state.get("reason") or "source_changed").strip() or "source_changed"
            blocking_issues.append(f"source_stale:{reason}")
        if StageStatus.normalize(record.status) != StageStatus.READY:
            blocking_issues.append("not_ready:human_review")
        artifacts = dict(record.artifacts or {})
        has_solution_bundle_ref = bool(
            str(artifacts.get("problem_solution_bundle_id") or "").strip()
            or str(artifacts.get("problem_solution_bundle_path") or "").strip()
        )
        problem_solution_state = self._load_problem_solution_state()
        pending_candidate_ids = self._pending_problem_solution_candidate_ids(
            problem_solution_state,
            record.record_id,
        )
        blocking_issues.extend(
            f"problem_solution:pending_candidate_review:{candidate_id}"
            for candidate_id in pending_candidate_ids
        )
        problem_statuses = dict(problem_solution_state.get("problem_statuses") or {})
        problem_solution_review = copy.deepcopy(dict(problem_statuses.get(record.record_id) or {}))
        structure = dict(getattr(self.context, "problem_solution_structure", {}) or {})
        solution_status = str(structure.get("solution_status") or "").strip().lower()
        opted_solution_statuses = {"identified", "external_source", "uncertain", "pending_review"}
        if (
            solution_status in opted_solution_statuses
            and not has_solution_bundle_ref
            and str(problem_solution_review.get("status") or "") != "solutions_absent_confirmed"
        ):
            blocking_issues.append("problem_solution:bundle_or_absence_review_required")
        solution_bundle_summary: dict[str, Any] | None = None
        if has_solution_bundle_ref:
            solution_bundle = self.bundle_for_record(record.record_id)
            if solution_bundle is None:
                blocking_issues.append("solution_bundle:missing")
            else:
                blocking_issues.extend(self.problem_solution_bundle_issues(solution_bundle, record=record))
                solution_bundle_summary = {
                    "bundle_id": str(solution_bundle.get("bundle_id") or ""),
                    "revision": int(solution_bundle.get("revision") or 0),
                    "status": str(solution_bundle.get("status") or ""),
                    "bundle_fingerprint": str(solution_bundle.get("bundle_fingerprint") or ""),
                    "solutions_total": len(list(solution_bundle.get("solutions") or [])),
                }
        blocking_issues = list(dict.fromkeys(blocking_issues))
        return {
            "schema_version": self.candidate_schema_version,
            "created_at": utc_now_text(),
            "promotion_enabled": False,
            "explicit_upload_enabled": not blocking_issues,
            "target_table": "problemas",
            "ready_for_future_promotion": not blocking_issues,
            "blocking_issues": blocking_issues,
            "write_operations": [],
            "sql": None,
            "record_id": record.record_id,
            "crop_id": record.crop_id,
            "payload": {
                "normalized": normalized,
                "source": dict(record.source or {}),
                "crop_path": record.crop_path,
                "models": dict(record.models or {}),
                "confidence": dict(record.confidence or {}),
                "review": dict(record.review or {}),
                "training_examples_total": len(record.training_examples or []),
                "audit": dict(record.audit or {}),
                "problem_solution_bundle": solution_bundle_summary,
                "problem_solution_review": problem_solution_review,
            },
            "policy": {
                "staging_only": True,
                "never_insert_directly_into_problemas": True,
                "requires_explicit_future_promotion_flow": True,
                "automatic_insert": False,
                "explicit_upload_endpoint": "/api/promotion/upload",
            },
        }

    def _sync_review_to_golden_bases(self, record: StagingProblemRecord, *, notes: str = "") -> None:
        record.golden_sync = {
            **dict(record.golden_sync or {}),
            "updated_at": utc_now_text(),
            "status": "pending",
            "targets": {},
            "errors": [],
        }
        live_record_path = self._problem_crop_record_path(record)
        if live_record_path is None:
            contract = self._write_golden_contract(record, notes=notes, reason="missing_problem_crops_live_record")
            record.golden_sync.update({"status": "contract_prepared", "contract_path": str(contract)})
            return

        corrected_text = self._normalized_to_training_text(record.normalized)
        figure_boxes = self._figure_boxes_from_record(record)
        try:
            from modulos.modulo12_auditor_entrenamiento.controlador_auditor_entrenamiento import (
                TrainingAuditController,
            )

            audit = TrainingAuditController()
            crops_root = live_record_path.parent.parent
            live_rows = audit.load_problem_crops_live(crops_root, crop_ids=[record.crop_id])
            if live_rows:
                audit.save_problem_crop_review(
                    live_rows[0],
                    ocr_text=record.raw_ocr,
                    corrected_text=corrected_text,
                    notes=notes,
                    ocr_status="corrected" if corrected_text.strip() else "pending_ocr",
                    figure_segmentation_status="reviewed" if figure_boxes else "pending_figure_segmentation",
                    figure_boxes_px=figure_boxes,
                    root=crops_root,
                )
            ocr_target, ocr_added = audit.import_problem_crops_into_ocr_golden(
                crops_root=crops_root,
                crop_ids=[record.crop_id],
                session_json=str(self.context.resolved_session_path() or ""),
                book_code=self.context.book_code,
                instance_type=self.context.instance_type,
                project_name=self.context.project_name,
            )
            normalizer_target = audit.build_ocr_normalization_golden_base(ocr_golden_dir=ocr_target)
            record.golden_sync["targets"] = {
                "problem_crops_live": str(crops_root),
                "ocr_golden": str(ocr_target),
                "ocr_golden_added": int(ocr_added),
                "ocr_normalization_golden": str(normalizer_target),
            }
            if figure_boxes or record.figure_segmentation:
                segment_target, segment_added, positives, boxes_total = audit.import_problem_crops_into_segment_golden(
                    crops_root=crops_root,
                    crop_ids=[record.crop_id],
                )
                record.golden_sync["targets"] = {
                    **dict(record.golden_sync.get("targets") or {}),
                    "segment_golden": str(segment_target),
                    "segment_golden_added": int(segment_added),
                    "segment_positive_images": int(positives),
                    "segment_boxes": int(boxes_total),
                }
            record.golden_sync["status"] = "synced"
        except Exception as exc:
            contract = self._write_golden_contract(record, notes=notes, reason="golden_api_error")
            record.golden_sync["status"] = "contract_prepared"
            record.golden_sync["contract_path"] = str(contract)
            record.golden_sync["errors"] = [*list(record.golden_sync.get("errors") or []), str(exc)]

    def _write_review_artifacts(self, record: StagingProblemRecord) -> None:
        artifacts_dir = self.artifact_dir("review_outputs", record.record_id, probe_file="training_examples.json")
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        latest_path = artifacts_dir / "latest_review.json"
        examples_path = artifacts_dir / "training_examples.json"
        history_path = artifacts_dir / "review_history.jsonl"
        payload = {
            "schema_version": "pdf_factory_review_artifact_v1",
            "updated_at": utc_now_text(),
            "context": self.context.to_dict(),
            "record_id": record.record_id,
            "crop_id": record.crop_id,
            "crop_path": record.crop_path,
            "source": dict(record.source or {}),
            "raw_ocr": record.raw_ocr,
            "structured_ocr": dict(record.structured_ocr or {}),
            "figure_segmentation": dict(record.figure_segmentation or {}),
            "machine_and_human_normalized": dict(record.normalized or {}),
            "review": dict(record.review or {}),
            "models": dict(record.models or {}),
            "confidence": dict(record.confidence or {}),
            "training_examples": [dict(item) for item in list(record.training_examples or [])],
            "intended_targets": [
                "problem_crops_live",
                "ocr_golden_live",
                "segment_training_live",
                "ocr_normalization_golden_live",
            ],
        }
        latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        examples_path.write_text(
            json.dumps(payload["training_examples"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        record.artifacts = {
            **dict(record.artifacts or {}),
            "review_outputs_schema": "pdf_factory_review_artifact_v1",
            "review_updated_at": payload["updated_at"],
            "latest_review": str(latest_path),
            "training_examples": str(examples_path),
            "review_history": str(history_path),
        }

    def _problem_crop_record_path(self, record: StagingProblemRecord) -> Path | None:
        raw = str(record.source.get("problem_crops_live_record") or "").strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        if path.exists():
            return path.resolve()
        return None

    def _write_golden_contract(self, record: StagingProblemRecord, *, notes: str, reason: str) -> Path:
        contracts_dir = self.root / "golden_contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "pdf_factory_golden_contract_v1",
            "created_at": utc_now_text(),
            "reason": reason,
            "context": self.context.to_dict(),
            "record_id": record.record_id,
            "crop_id": record.crop_id,
            "crop_path": record.crop_path,
            "source": dict(record.source),
            "raw_ocr": record.raw_ocr,
            "structured_ocr": dict(record.structured_ocr),
            "normalized_human": dict(record.normalized),
            "corrected_text": self._normalized_to_training_text(record.normalized),
            "figure_boxes_px": self._figure_boxes_from_record(record),
            "models": dict(record.models),
            "confidence": dict(record.confidence),
            "notes": str(notes or ""),
            "intended_targets": [
                "problem_crops_live",
                "ocr_golden_live",
                "segment_training_live",
                "ocr_normalization_golden_live",
            ],
        }
        path = contracts_dir / f"{self._file_stem_for_dir(contracts_dir, record.record_id)}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def _normalized_to_training_text(normalized: dict[str, Any]) -> str:
        rendered = str(normalized.get("latex_rendered_item") or "").strip()
        if rendered:
            return rendered
        statement = str(normalized.get("enunciado_latex") or "").strip()
        options = normalized.get("alternativas") if isinstance(normalized.get("alternativas"), dict) else {}
        option_text = " ".join(
            f"{label}) {str(options.get(label, '') or '').strip()}"
            for label in ("A", "B", "C", "D", "E")
            if str(options.get(label, "") or "").strip()
        ).strip()
        answer = str(normalized.get("respuesta_correcta") or "").strip()
        parts = [part for part in (statement, option_text, f"[[Clave={answer}]]" if answer else "") if part]
        if parts:
            return "\n".join(parts)
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def _figure_boxes_from_record(record: StagingProblemRecord) -> list[list[int]]:
        segments = record.figure_segmentation.get("segments") if isinstance(record.figure_segmentation, dict) else []
        out: list[list[int]] = []
        for segment in segments if isinstance(segments, list) else []:
            if not isinstance(segment, dict):
                continue
            raw = segment.get("bbox_px")
            if not isinstance(raw, (list, tuple)) or len(raw) < 4:
                continue
            try:
                box = [int(value) for value in raw[:4]]
            except Exception:
                continue
            if box[2] > box[0] and box[3] > box[1]:
                out.append(box)
        return out
