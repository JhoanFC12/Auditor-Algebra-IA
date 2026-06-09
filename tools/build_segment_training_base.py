from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
ITEM_NUM_RE = re.compile(r"\\item\[\s*\\textbf\{(\d+)\.\}\s*\]")
TAG_RE = re.compile(r"\[\[([^\]=]+)=([^\]]*)\]\]")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _canon_path(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve()).lower()
    except Exception:
        return str(value or "").strip().lower()


def _safe_name(value: str, fallback: str = "sample") -> str:
    text = (value or "").strip()
    if not text:
        return fallback
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in text)
    cleaned = cleaned.strip("._")
    return cleaned or fallback


def _hash_to_split(key: str) -> str:
    raw = int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16) % 100
    if raw < 80:
        return "train"
    if raw < 90:
        return "val"
    return "test"


def _extract_item_num(item_text: str) -> Optional[int]:
    match = ITEM_NUM_RE.search(item_text or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _extract_tags(item_text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, value in TAG_RE.findall(item_text or ""):
        out[str(key).strip()] = str(value).strip()
    return out


def _read_debug_items(debug_dir: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not debug_dir or not debug_dir.exists():
        return out
    for path in sorted(debug_dir.glob("*.json")):
        try:
            payload = _load_json(path)
        except Exception:
            continue
        report_items = (((payload.get("report") or {}).get("items")) or [])
        if not isinstance(report_items, list) or not report_items:
            continue
        first = report_items[0]
        if not isinstance(first, dict):
            continue
        item = dict(first.get("item") or {})
        out[path.stem] = item
    return out


def _copy_image(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _resolve_bundle_path(base_dir: Path, raw_value: Any) -> Path:
    raw = str(raw_value or "").strip()
    if not raw:
        return Path("")
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p
    return (base_dir / p).resolve()


@dataclass
class SessionPair:
    session_json: Path
    session_images_json: Path


def _find_session_pairs(roots: Iterable[Path]) -> List[SessionPair]:
    pairs: List[SessionPair] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for session_path in root.rglob("*.session.json"):
            if session_path.name.endswith(".session__images.json"):
                continue
            images_path = session_path.with_name(session_path.name.replace(".session.json", ".session__images.json"))
            if not images_path.exists():
                continue
            key = _canon_path(str(session_path))
            if key in seen:
                continue
            seen.add(key)
            pairs.append(SessionPair(session_json=session_path, session_images_json=images_path))
    return sorted(pairs, key=lambda x: str(x.session_json).lower())


def _build_item_maps(session_payload: Dict[str, Any]) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    by_num: Dict[int, Dict[str, Any]] = {}
    by_source: Dict[str, Dict[str, Any]] = {}
    for raw in session_payload.get("items", []) or []:
        if not isinstance(raw, dict):
            continue
        item_text = str(raw.get("item") or "")
        item_num = _extract_item_num(item_text)
        source_stem = str(raw.get("archivo_origen") or "").strip()
        tags = _extract_tags(item_text)
        row = {
            "item_text": item_text,
            "item_num": item_num,
            "source_stem": source_stem,
            "tags": tags,
            "imagenes": list(raw.get("imagenes") or []),
            "image_binding": dict(raw.get("image_binding") or {}),
        }
        if item_num is not None:
            by_num[item_num] = row
        if source_stem:
            by_source[source_stem] = row
    return by_num, by_source


def _resolve_binding(binding_map: Any, segment_idx_1based: int) -> Dict[str, Any]:
    if not isinstance(binding_map, dict):
        return {}
    candidates = [
        str(segment_idx_1based - 1),
        str(segment_idx_1based),
        int(segment_idx_1based - 1),
        int(segment_idx_1based),
    ]
    for key in candidates:
        if key in binding_map and isinstance(binding_map[key], dict):
            return dict(binding_map[key])
    return {}


def _iter_segments_from_session(
    *,
    session_pair: SessionPair,
    session_payload: Dict[str, Any],
    images_payload: Dict[str, Any],
) -> Iterable[Dict[str, Any]]:
    session_bundle = images_payload.get("session_bundle") or session_payload.get("session_bundle") or {}
    bundle_base = session_pair.session_images_json.parent
    segments_dir = _resolve_bundle_path(bundle_base, session_bundle.get("segments_dir"))
    debug_dir = segments_dir.parent / "scan_pipeline_debug" if segments_dir else None
    debug_items = _read_debug_items(debug_dir)

    item_by_num, item_by_source = _build_item_maps(session_payload)
    bindings_by_source = dict(images_payload.get("segment_item_bindings_by_source") or {})
    preview_images = dict(images_payload.get("preview_images") or {})

    if not segments_dir.exists():
        return

    for manifest_path in sorted(segments_dir.rglob("segments_manifest.json")):
        try:
            manifest = _load_json(manifest_path)
        except Exception:
            continue
        source_path = Path(str(manifest.get("source_path") or "")).expanduser()
        source_key = _canon_path(str(source_path))
        source_stem = str(manifest.get("source_stem") or manifest_path.parent.name)
        binding_map = bindings_by_source.get(source_key, {})
        debug_item = debug_items.get(source_stem, {})
        source_item = item_by_source.get(source_stem, {})
        segments = manifest.get("segments") or []
        if not isinstance(segments, list):
            continue

        for seg in segments:
            if not isinstance(seg, dict):
                continue
            idx = int(seg.get("idx") or 0)
            if idx <= 0:
                continue
            file_name = str(seg.get("file_name") or "").strip()
            if not file_name:
                continue
            segment_image_path = manifest_path.parent / file_name
            if not segment_image_path.exists():
                continue
            binding = _resolve_binding(binding_map, idx)
            marker_name = str(binding.get("marker_name") or "")
            item_num = binding.get("item_num")
            try:
                item_num = int(item_num) if item_num is not None else None
            except Exception:
                item_num = None
            if item_num is None:
                fallback_num = source_item.get("item_num")
                try:
                    item_num = int(fallback_num) if fallback_num is not None else None
                except Exception:
                    item_num = None
            item_payload = item_by_num.get(item_num or -1, {})
            item_text = str(item_payload.get("item_text") or source_item.get("item_text") or "")
            tags = dict(item_payload.get("tags") or source_item.get("tags") or {})
            image_binding = dict(item_payload.get("image_binding") or source_item.get("image_binding") or {})
            preview_rel = str(preview_images.get(marker_name) or "")
            preview_abs = ""
            if preview_rel:
                preview_abs = str((session_pair.session_images_json.parent / preview_rel).resolve())

            yield {
                "session_json": str(session_pair.session_json),
                "session_images_json": str(session_pair.session_images_json),
                "book_code": str(session_payload.get("book_code") or images_payload.get("book_code") or ""),
                "instance_type": str(session_payload.get("instance_type") or images_payload.get("instance_type") or ""),
                "source_path": str(source_path),
                "source_stem": source_stem,
                "segment_idx": idx,
                "segment_bbox_px": list(seg.get("bbox_px") or []),
                "segment_image_path": str(segment_image_path.resolve()),
                "marker_name": marker_name,
                "binding_confirmed": bool(binding.get("confirmed", False)),
                "binding_slot": str(binding.get("slot") or ""),
                "binding_crop_path": str(binding.get("crop_path") or ""),
                "binding_updated_at": str(binding.get("updated_at") or ""),
                "item_num": item_num,
                "item_text": item_text,
                "item_tags": tags,
                "item_image_binding_status": str(image_binding.get("status") or ""),
                "item_image_binding_origin": str(image_binding.get("origin") or ""),
                "item_declared_marker_name": str(image_binding.get("marker_name") or ""),
                "preview_image_path": preview_abs,
                "debug_item": debug_item,
            }


def _record_to_topology_prompt(row: Dict[str, Any]) -> Dict[str, Any]:
    debug_item = dict(row.get("debug_item") or {})
    statement = str(debug_item.get("statement") or "").strip()
    options = dict(debug_item.get("options") or {})
    text_parts: List[str] = []
    if statement:
        text_parts.append(f"Enunciado: {statement}")
    if options:
        options_text = " | ".join(f"{k}) {v}" for k, v in sorted(options.items()))
        text_parts.append(f"Opciones: {options_text}")
    text_context = "\n".join(text_parts).strip()
    prompt = (
        "Describe topologicamente la figura geometrica de la imagen. "
        "Enumera puntos, segmentos, rectas, circunferencias, regiones y relaciones relevantes "
        "(colinealidad, paralelismo, perpendicularidad, igualdad, tangencia, inscripcion, interseccion). "
        "Devuelve un JSON con claves entities, relations y notes."
    )
    metadata = {
        "book_code": row.get("book_code"),
        "instance_type": row.get("instance_type"),
        "item_num": row.get("item_num"),
        "slot": row.get("binding_slot"),
        "marker_name": row.get("marker_name"),
        "source_stem": row.get("source_stem"),
        "segment_idx": row.get("segment_idx"),
    }
    if text_context:
        metadata["problem_context"] = text_context
    return {
        "id": row["record_id"],
        "input_image_path": row["copied_image_rel"],
        "prompt": prompt,
        "completion": "",
        "metadata": metadata,
    }


def build_segment_training_base(*, roots: List[Path], out_root: Path, seed: int, copy_mode: str) -> Path:
    random.seed(seed)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = out_root / f"segment_training_base_{ts}"
    images_dir = out_dir / "images"
    splits_dir = out_dir / "splits"
    images_dir.mkdir(parents=True, exist_ok=True)
    for split in ("train", "val", "test"):
        (splits_dir / split).mkdir(parents=True, exist_ok=True)

    session_pairs = _find_session_pairs(roots)
    rows: List[Dict[str, Any]] = []
    topology_rows: List[Dict[str, Any]] = []
    counts_by_book: Counter[str] = Counter()
    counts_by_status: Counter[str] = Counter()
    counts_by_split: Counter[str] = Counter()
    session_errors: List[str] = []

    for pair in session_pairs:
        try:
            session_payload = _load_json(pair.session_json)
            images_payload = _load_json(pair.session_images_json)
        except Exception as exc:
            session_errors.append(f"{pair.session_json}: {exc}")
            continue

        for raw in _iter_segments_from_session(
            session_pair=pair,
            session_payload=session_payload,
            images_payload=images_payload,
        ):
            split = _hash_to_split(
                f"{raw.get('book_code')}|{raw.get('instance_type')}|{raw.get('source_stem')}|{raw.get('segment_idx')}"
            )
            base_name = "_".join(
                [
                    _safe_name(str(raw.get("book_code") or "book"), "book"),
                    _safe_name(str(raw.get("instance_type") or "inst"), "inst"),
                    _safe_name(str(raw.get("source_stem") or "source"), "source"),
                    f"seg{int(raw.get('segment_idx') or 0):02d}",
                ]
            )
            ext = Path(str(raw["segment_image_path"])).suffix.lower() or ".png"
            dst_name = f"{base_name}{ext}"
            dst_abs = images_dir / dst_name
            if not dst_abs.exists():
                _copy_image(Path(str(raw["segment_image_path"])), dst_abs)
            split_abs = splits_dir / split / dst_name
            if not split_abs.exists():
                if copy_mode == "copy":
                    _copy_image(dst_abs, split_abs)
                else:
                    split_abs.parent.mkdir(parents=True, exist_ok=True)
                    if not split_abs.exists():
                        shutil.copy2(dst_abs, split_abs)

            debug_item = dict(raw.get("debug_item") or {})
            tags = dict(raw.get("item_tags") or {})
            problem_course = tags.get("curso") or str(debug_item.get("curso") or "")
            problem_topic = tags.get("tema") or str(debug_item.get("tema") or "")
            status = "confirmed" if raw.get("binding_confirmed") else (
                raw.get("item_image_binding_status") or "unbound"
            )
            record_id = hashlib.sha1(
                f"{raw.get('session_json')}|{raw.get('source_stem')}|{raw.get('segment_idx')}".encode("utf-8")
            ).hexdigest()[:16]

            row = {
                "record_id": record_id,
                "split": split,
                "book_code": raw.get("book_code"),
                "instance_type": raw.get("instance_type"),
                "session_json": raw.get("session_json"),
                "session_images_json": raw.get("session_images_json"),
                "source_path": raw.get("source_path"),
                "source_stem": raw.get("source_stem"),
                "segment_idx": raw.get("segment_idx"),
                "segment_bbox_px": raw.get("segment_bbox_px"),
                "segment_image_path": raw.get("segment_image_path"),
                "copied_image_rel": str(dst_abs.relative_to(out_dir)).replace("\\", "/"),
                "split_image_rel": str(split_abs.relative_to(out_dir)).replace("\\", "/"),
                "item_num": raw.get("item_num"),
                "slot": raw.get("binding_slot"),
                "marker_name": raw.get("marker_name"),
                "binding_confirmed": raw.get("binding_confirmed"),
                "binding_status": status,
                "binding_crop_path": raw.get("binding_crop_path"),
                "binding_updated_at": raw.get("binding_updated_at"),
                "preview_image_path": raw.get("preview_image_path"),
                "curso": problem_course,
                "tema": problem_topic,
                "tags": tags,
                "item_text": raw.get("item_text"),
                "debug_statement": debug_item.get("statement") or "",
                "debug_options": debug_item.get("options") or {},
                "debug_has_figure": debug_item.get("has_figure"),
                "debug_figure_tag": debug_item.get("figure_tag") or "",
                "topology_description": "",
                "difficulty_label": "",
                "difficulty_rationale": "",
            }
            rows.append(row)
            counts_by_book[str(raw.get("book_code") or "SIN_LIBRO")] += 1
            counts_by_status[str(status or "unknown")] += 1
            counts_by_split[split] += 1
            topology_rows.append(_record_to_topology_prompt(row))

    records_jsonl = out_dir / "records_all.jsonl"
    confirmed_jsonl = out_dir / "records_confirmed.jsonl"
    topology_jsonl = out_dir / "topology_annotation_queue.jsonl"
    csv_path = out_dir / "records_all.csv"

    records_jsonl.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )
    confirmed_rows = [row for row in rows if row.get("binding_confirmed")]
    confirmed_jsonl.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in confirmed_rows) + ("\n" if confirmed_rows else ""),
        encoding="utf-8",
    )
    topology_jsonl.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in topology_rows) + ("\n" if topology_rows else ""),
        encoding="utf-8",
    )

    csv_fields = [
        "record_id",
        "split",
        "book_code",
        "instance_type",
        "source_stem",
        "segment_idx",
        "item_num",
        "slot",
        "marker_name",
        "binding_confirmed",
        "binding_status",
        "curso",
        "tema",
        "copied_image_rel",
        "debug_statement",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=csv_fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in csv_fields})

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "roots": [str(root) for root in roots],
        "session_pairs_total": len(session_pairs),
        "records_total": len(rows),
        "records_confirmed": len(confirmed_rows),
        "topology_queue_total": len(topology_rows),
        "counts_by_book": dict(sorted(counts_by_book.items())),
        "counts_by_status": dict(sorted(counts_by_status.items())),
        "counts_by_split": dict(sorted(counts_by_split.items())),
        "files": {
            "records_all_jsonl": records_jsonl.name,
            "records_confirmed_jsonl": confirmed_jsonl.name,
            "topology_annotation_queue_jsonl": topology_jsonl.name,
            "records_all_csv": csv_path.name,
        },
        "notes": [
            "records_all.jsonl incluye todos los segmentos detectados en sesiones con manifest e imagen.",
            "records_confirmed.jsonl filtra los segmentos con binding confirmado a item/slot.",
            "topology_annotation_queue.jsonl sirve como cola inicial para describir figuras geométricas.",
            "Las columnas topology_description y difficulty_* quedan vacías para anotación posterior.",
        ],
    }
    if session_errors:
        manifest["session_errors"] = session_errors[:50]
        manifest["session_errors_total"] = len(session_errors)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    readme = out_dir / "README.txt"
    readme.write_text(
        "\n".join(
            [
                "BASE DE ENTRENAMIENTO DE SEGMENTOS",
                "",
                "Contenido principal:",
                "- records_all.jsonl: todos los segmentos exportados.",
                "- records_confirmed.jsonl: solo bindings confirmados.",
                "- topology_annotation_queue.jsonl: cola de anotacion para descripcion topologica.",
                "- images/: copia canonica de los recortes.",
                "- splits/train|val|test: recortes repartidos para entrenamiento.",
                "",
                "Uso sugerido:",
                "1. Empezar con records_confirmed.jsonl para un primer fine tuning mas limpio.",
                "2. Usar topology_annotation_queue.jsonl para generar o revisar descripciones de figuras.",
                "3. Llenar topology_description y difficulty_* antes de entrenar un razonador mas fino.",
            ]
        ),
        encoding="utf-8",
    )
    return out_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Construye una base de entrenamiento agregada desde sesiones segmentadas del Transcriptor IA."
    )
    parser.add_argument(
        "--roots",
        nargs="+",
        required=True,
        help="Una o mas rutas raiz con libros/sesiones/temporales.",
    )
    parser.add_argument(
        "--out-root",
        default="E:/Github/Auditor-IA/.cache/transcriptor_runs/datasets",
        help="Carpeta raiz de salida.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Semilla para reproducibilidad.")
    parser.add_argument(
        "--copy-mode",
        choices=("copy",),
        default="copy",
        help="Modo de materializacion de imagenes. Por ahora solo copy.",
    )
    args = parser.parse_args()

    roots = [Path(p).expanduser().resolve() for p in args.roots]
    out_root = Path(args.out_root).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    out_dir = build_segment_training_base(
        roots=roots,
        out_root=out_root,
        seed=int(args.seed),
        copy_mode=str(args.copy_mode),
    )
    manifest = _load_json(out_dir / "manifest.json")
    print(f"[OK] Base creada: {out_dir}")
    print(
        "[RESUMEN] sesiones={sessions} records={records} confirmed={confirmed} topology_queue={queue}".format(
            sessions=manifest.get("session_pairs_total", 0),
            records=manifest.get("records_total", 0),
            confirmed=manifest.get("records_confirmed", 0),
            queue=manifest.get("topology_queue_total", 0),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
