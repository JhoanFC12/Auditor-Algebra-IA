from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Tuple

from ..controlador_transcriptor import PersistableItem, TranscriptorController
from ..domain.image_binding import ImageBinding
from ..persistence.session_store import SessionStore
from ..services.dataset_exporter import DatasetExporter, ExportReport, TrainingDatasetBundle
from ..services.vision.transcription_service import TranscriptionService
from ..state import ItemState, SourceImageState, TranscriptorSessionState
from .item_processing import ItemProcessingWorkflow
from .guards import require_db_name, require_items, require_output, require_sources


@dataclass
class BatchOCRResult:
    ok_count: int = 0
    error_count: int = 0
    skipped_count: int = 0
    detected_items_total: int = 0
    updated_items: List[Tuple[str, str, List[str]]] = field(default_factory=list)
    raw_outputs_by_label: Dict[str, str] = field(default_factory=dict)
    geometry_pass_by_label: Dict[str, str] = field(default_factory=dict)
    geometry_pass_payload_by_label: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    diagnostics_by_label: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    provider_fallbacks: List[Dict[str, str]] = field(default_factory=list)


class TranscriptorWorkflow:
    def __init__(
        self,
        *,
        controller: TranscriptorController | None = None,
        session_store: SessionStore | None = None,
        dataset_exporter: DatasetExporter | None = None,
        transcription_service: TranscriptionService | None = None,
        item_processing: ItemProcessingWorkflow | None = None,
    ) -> None:
        self.controller = controller or TranscriptorController()
        self.session_store = session_store or SessionStore()
        self.dataset_exporter = dataset_exporter or DatasetExporter()
        self.transcription_service = transcription_service or TranscriptionService()
        self.item_processing = item_processing or ItemProcessingWorkflow(
            transcription_service=self.transcription_service
        )

    def load_images(self, state: TranscriptorSessionState, paths: Iterable[Tuple[str, str]]) -> None:
        state.source_images = [
            SourceImageState(label=str(label), path=str(path), source_key=str(label))
            for label, path in paths
        ]

    def remove_images(self, state: TranscriptorSessionState, labels: Iterable[str]) -> None:
        remove = {str(v) for v in labels}
        state.source_images = [img for img in state.source_images if img.label not in remove]

    def segment_selected(self, state: TranscriptorSessionState, labels: Iterable[str]) -> List[str]:
        guard = require_sources(state)
        return [] if guard.ok else list(guard.errors)

    def transcribe_selected(self, state: TranscriptorSessionState, labels: Iterable[str]) -> List[str]:
        guard = require_sources(state)
        return [] if guard.ok else list(guard.errors)

    def apply_keys(self, state: TranscriptorSessionState, raw_keys: str) -> None:
        state.metadata["step3_last_claves_input"] = str(raw_keys or "")

    def run_reasoning(self, state: TranscriptorSessionState) -> List[str]:
        guard = require_items(state)
        return [] if guard.ok else list(guard.errors)

    def run_formatting(self, state: TranscriptorSessionState) -> List[str]:
        guard = require_items(state)
        return [] if guard.ok else list(guard.errors)

    def save_session(self, state: TranscriptorSessionState, path: Path) -> None:
        self.session_store.dump(state, path)

    def load_session(self, path: Path) -> TranscriptorSessionState:
        return self.session_store.load(path)

    def save_tex(self, state: TranscriptorSessionState, path: Path) -> None:
        guard = require_output(state)
        if not guard.ok:
            raise ValueError("; ".join(guard.errors))
        items = [line.strip() for line in (state.output_text or "").splitlines() if line.strip()]
        self.controller.exportar_a_tex(items=items, out_path=path)

    def save_db(
        self,
        state: TranscriptorSessionState,
        db_name: str,
        *,
        libro_codigo: str = "",
        instancia_tipo: str = "",
        pdf_path: str = "",
        solution_paths_by_number: Dict[int, Any] | None = None,
    ) -> dict:
        db_guard = require_db_name(db_name)
        if not db_guard.ok:
            raise ValueError("; ".join(db_guard.errors))
        item_guard = require_items(state)
        if not item_guard.ok:
            raise ValueError("; ".join(item_guard.errors))
        clean_code = str(libro_codigo or "").strip()
        clean_instance = str(instancia_tipo or "").strip()
        clean_pdf = str(pdf_path or "").strip()
        normalized_solution_paths: Dict[int, List[List[str]]] = {}
        for raw_key, raw_value in (solution_paths_by_number or {}).items():
            try:
                key = int(raw_key)
            except Exception:
                continue
            if key <= 0:
                continue
            if isinstance(raw_value, dict):
                raw_groups = [raw_value]
            elif isinstance(raw_value, (list, tuple, set)):
                raw_groups = list(raw_value)
            else:
                raw_groups = [raw_value]
            contains_nested = any(isinstance(v, (list, tuple, set, dict)) for v in raw_groups)
            if not contains_nested:
                raw_groups = [raw_groups]
            normalized_groups: List[List[str]] = []
            for raw_group in raw_groups:
                if isinstance(raw_group, dict):
                    raw_group = raw_group.get("images") if "images" in raw_group else raw_group.get("paths")
                if isinstance(raw_group, (list, tuple, set)):
                    iterable = list(raw_group)
                else:
                    iterable = [raw_group]
                deduped: List[str] = []
                for raw_path in iterable:
                    clean_path = str(raw_path or "").strip()
                    if clean_path and clean_path not in deduped:
                        deduped.append(clean_path)
                if deduped:
                    normalized_groups.append(deduped)
            if normalized_groups:
                normalized_solution_paths[key] = normalized_groups
        items = [
            PersistableItem(
                archivo_origen=clean_pdf or item.archivo_origen,
                item_latex=item.item_text,
                imagenes=list(item.image_paths),
                libro_codigo=clean_code,
                instancia_tipo=clean_instance,
                soluciones=normalized_solution_paths.get(
                    int(self.controller.parsear_numero_original(item.item_text or "") or 0),
                    [],
                ),
            )
            for item in state.items
        ]
        return self.controller.insert_items(db_name, items=items)

    def export_dataset(self, state: TranscriptorSessionState, out_dir: Path) -> ExportReport:
        bundle: TrainingDatasetBundle = self.dataset_exporter.build_training_dataset(state)
        return self.dataset_exporter.export_training_dataset(bundle, out_dir)

    def refresh_items(self, state: TranscriptorSessionState, items: Iterable[Tuple[str, str, List[str]]], *, corrected: Iterable[int] = ()) -> None:
        corrected_set = {int(v) for v in corrected}
        state.items = []
        for idx, raw in enumerate(items, start=1):
            archivo_origen = str(raw[0] if len(raw) > 0 else "")
            item_text = str(raw[1] if len(raw) > 1 else "")
            image_paths = [str(v) for v in (raw[2] if len(raw) > 2 else [])]
            image_binding = ImageBinding.from_dict(raw[3] if len(raw) > 3 else {})
            state.items.append(
                ItemState(
                    archivo_origen=archivo_origen,
                    item_text=item_text,
                    image_paths=image_paths,
                    corrected=idx in corrected_set,
                    image_binding=image_binding,
                )
            )

    def _prepare_batch_context(self, **kwargs: Any) -> Dict[str, Any]:
        return dict(kwargs)

    def _resolve_effective_paths(self, paths: Iterable[Tuple[str, Path]]) -> List[Tuple[str, Path]]:
        return [(str(label), Path(path)) for label, path in paths]

    def _extract_raw_for_image(self, *, image_path: Path, context: Dict[str, Any]) -> Any:
        return self.transcription_service.extract_text(image_path, context)

    def _ingest_raw_output_for_image(
        self,
        *,
        label: str,
        path: Path,
        raw_output: str,
        image_index: int,
        context: Dict[str, Any],
    ) -> Tuple[List[Tuple[str, str, List[str]]], List[Dict[str, Any]]]:
        ingest_fn = context.get("ingest_impl")
        if callable(ingest_fn):
            result = ingest_fn(
                label=label,
                path=path,
                raw_output=raw_output,
                image_index=image_index,
                context=context,
            )
            if isinstance(result, dict):
                items = list(result.get("items", []) or [])
                diagnostics = list(result.get("diagnostics", []) or [])
                return items, diagnostics
        return [], []

    def _process_items_after_raw_ocr(
        self,
        *,
        raw_items: List[Tuple[str, str, List[str]]],
        context: Dict[str, Any],
    ) -> List[Tuple[str, str, List[str]]]:
        processed: List[Tuple[str, str, List[str]]] = []
        for archivo, item_text, image_paths in raw_items:
            item_result = self.item_processing.process_item(item_text, context)
            processed.append((archivo, item_result.final_item, list(image_paths or [])))
        return processed

    def _finalize_batch_result(self, result: BatchOCRResult) -> BatchOCRResult:
        result.detected_items_total = len(result.updated_items)
        return result

    def run_direct_ocr_batch(
        self,
        *,
        paths: Iterable[Tuple[str, Path]],
        context: Dict[str, Any],
        on_progress: Callable[[int, int, str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> BatchOCRResult:
        resolved = self._resolve_effective_paths(paths)
        result = BatchOCRResult()
        batch_ctx = self._prepare_batch_context(**context)
        total = len(resolved)
        for idx, (label, path) in enumerate(resolved, start=1):
            if on_progress is not None:
                on_progress(idx, total, label)
            try:
                raw_result = self._extract_raw_for_image(image_path=path, context=batch_ctx)
                raw_output = str(raw_result.raw_text or "")
                result.raw_outputs_by_label[str(label)] = raw_output
                raw_items, diagnostics = self._ingest_raw_output_for_image(
                    label=label,
                    path=path,
                    raw_output=raw_output,
                    image_index=idx,
                    context=batch_ctx,
                )
                processed = self._process_items_after_raw_ocr(raw_items=raw_items, context=batch_ctx)
                result.updated_items.extend(processed)
                if diagnostics:
                    result.diagnostics_by_label[str(label)] = list(diagnostics)
                result.ok_count += 1
                if on_log is not None:
                    on_log(f"{label}: OCR procesado.")
            except Exception as exc:
                result.error_count += 1
                result.warnings.append(f"{label}: {exc}")
                if on_log is not None:
                    on_log(f"{label}: error OCR ({exc})")
        return self._finalize_batch_result(result)
