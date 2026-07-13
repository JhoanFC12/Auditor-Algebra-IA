from __future__ import annotations

import json
import hashlib
import locale
import os
import re
import shutil
import subprocess
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pypdf import PdfReader


CATALOG_SCHEMA_VERSION = "book_visual_catalog_v1"
THEME_SCHEMA_VERSION = "book_catalog_themes_v1"
DEFAULT_SOURCE_ROOT = Path(r"E:\Banco de Preguntas")
DEFAULT_OUTPUT_ROOT = Path(".cache") / "book_catalog"
PAGE_LABELS = (
    "portada",
    "indice",
    "teoria",
    "ejemplos",
    "problemas_propuestos",
    "problemas_resueltos",
    "solucionario",
    "mixta",
    "dudosa",
)
THEME_SEGMENT_LABELS = (
    "teoria",
    "ejemplos",
    "problemas_propuestos",
    "problemas_resueltos",
    "solucionario",
)
DEFAULT_BOOTSTRAP_LABEL = "dudosa"
DEFAULT_LABEL_SOURCE = "bootstrap_visual_stub"
VISUAL_FINGERPRINT_PAGES = (1, 2, 3)
VISUAL_DUPLICATE_MAX_DISTANCE = 10
PART_RANGE_RE = re.compile(r"\b\d{1,4}\s*(?:-{2,}|a|al)\s*\d{1,4}\b", re.IGNORECASE)
PART_EXPLICIT_PAGE_RANGE_RE = re.compile(
    r"\b(?:p|pag|pagina|page)\.?\s*\d{1,4}\s*-\s*\d{1,4}\b",
    re.IGNORECASE,
)
PART_PROBLEM_RE = re.compile(r"\bproblema\s*\d+(?:[._-]\d+)?\b", re.IGNORECASE)
PART_MARKER_RE = re.compile(r"\b(?:parte|part|fragmento|recorte|crop|pagina|page)\s*\d+\b", re.IGNORECASE)
PART_FOLDER_MARKERS = ("_img", "imagenes", "images", "crops", "recortes", "paginas")
COURSE_FOLDERS = (
    "Libros de Algebra",
    "Geometria",
    "Geometria Analitica",
    "Trigonometria",
    "Aritmetica",
    "Examenes y Concursos",
    "Por Clasificar",
)
COURSE_KEYWORDS = (
    ("Geometria Analitica", ("geometria analitica", "3 geometria analitica")),
    ("Libros de Algebra", ("algebra", "1 algebra", "libros algebra")),
    ("Geometria", ("geometria", "2 geometria", "libros geometria")),
    ("Trigonometria", ("trigonometria", "4 trigonometria", "libros trigonometria")),
    ("Aritmetica", ("aritmetica", "5 aritmetica")),
    (
        "Examenes y Concursos",
        ("examen", "examenes", "concursos", "conamat", "deco", "unt examenes", "olimpiada"),
    ),
)
INSTITUTION_OR_EDITORIAL_NAMES = {
    "aduni": "Aduni",
    "ceprevi": "CEPREVI",
    "cepunt": "CEPUNT",
    "conamat": "CONAMAT",
    "deco": "DECO",
    "eureka": "Eureka",
    "rodo": "Rodo",
    "rubinos": "Rubinos",
    "vesalius": "Vesalius",
}
GENERIC_PATH_PARTS = {
    "img",
    "imagenes",
    "imagen",
    "material",
    "materiales",
    "recopilacion",
    "seminarios",
    "solucionario",
    "solucionarios",
}
COLLECTION_ONLY_NAMES = {
    "cuzcano",
}
CONTACT_SHEET_BG = "#f5f1e8"
CONTACT_SHEET_TEXT = "#1d1d1d"
LABEL_COLORS = {
    "portada": "#264653",
    "indice": "#355070",
    "teoria": "#386641",
    "ejemplos": "#6a994e",
    "problemas_propuestos": "#bc6c25",
    "problemas_resueltos": "#9c6644",
    "solucionario": "#7f5539",
    "mixta": "#6d597a",
    "dudosa": "#c1121f",
}
DEFAULT_TESSERACT_CMD = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
DEFAULT_OCR_LANG = "spa"
THEMES_MARKER_START = "<!-- book_catalog_themes:start -->"
THEMES_MARKER_END = "<!-- book_catalog_themes:end -->"
COMMON_MOJIBAKE_REPLACEMENTS = {
    "Ã¡": "á",
    "Ã©": "é",
    "Ã­": "í",
    "Ã³": "ó",
    "Ãº": "ú",
    "Ã±": "ñ",
    "Ã": "Á",
    "Ã‰": "É",
    "Ã": "Í",
    "Ã“": "Ó",
    "Ãš": "Ú",
    "Ã‘": "Ñ",
    "Â°": "°",
    "Âº": "º",
}


@dataclass(frozen=True)
class InventoryRecord:
    schema_version: str
    book_id: str
    source_root: str
    pdf_path: str
    pdf_relpath: str
    pdf_hash_sha256: str
    file_size_bytes: int
    modified_at: str
    discovered_at: str
    page_count: int | None
    metadata_title: str = ""
    metadata_author: str = ""
    bibliographic_title: str = ""
    bibliographic_author: str = ""
    bibliographic_editorial: str = ""
    bibliographic_collection: str = ""
    material_type: str = ""
    bibliographic_source: str = ""
    bibliographic_status: str = "pending_review"
    bibliographic_notes: str = ""
    inventory_status: str = "ok"
    notes: str = ""


@dataclass(frozen=True)
class PdfListingRecord:
    index: int
    course: str
    source_top_folder: str
    pdf_path: str
    pdf_relpath: str
    file_name: str
    file_size_bytes: int
    modified_at: str
    bibliographic_title: str = ""
    bibliographic_author: str = ""
    bibliographic_editorial: str = ""
    bibliographic_collection: str = ""
    material_type: str = ""
    bibliographic_status: str = "pending_review"
    part_candidate: bool = False
    part_reason: str = ""
    general_candidate_path: str = ""


@dataclass(frozen=True)
class DuplicateMatch:
    match_type: str
    canonical_book_id: str
    canonical_pdf_path: str
    candidate_book_id: str
    candidate_pdf_path: str
    reason: str
    visual_distance: int | None = None


@dataclass(frozen=True)
class PartMatch:
    match_type: str
    general_pdf_path: str
    part_pdf_path: str
    reason: str


@dataclass(frozen=True)
class OcrClassification:
    label: str
    confidence: float
    reason: str
    scores: dict[str, int]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_slug(value: str, fallback: str = "book") -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").strip()).encode("ascii", "ignore").decode("ascii")
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in normalized)
    while "--" in text:
        text = text.replace("--", "-")
    text = text.strip("-")
    return text or fallback


def normalize_for_match(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    normalized = "".join(ch.lower() if ch.isalnum() else " " for ch in normalized)
    return " ".join(normalized.split())


def clean_bibliographic_segment(value: str) -> str:
    text = str(value or "").strip()
    if text.lower().endswith(".pdf"):
        text = text[:-4]
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"^\s*\d+(?:[.)\-_ ]+)?", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ._-")
    return text


def _is_meaningful_pdf_metadata(value: str) -> bool:
    text = normalize_for_match(value)
    if not text or len(text) < 3:
        return False
    if text in {"xpp", "binder", "scan", "camscanner", "untitled"}:
        return False
    return True


def infer_material_type(*, pdf_path: str | Path, pdf_relpath: str = "") -> str:
    haystack = normalize_for_match(f"{pdf_relpath} {Path(str(pdf_path)).name}")
    if any(token in haystack for token in ("examen", "ordin", "admision", "conamat", "deco", "concurso", "unt examenes")):
        return "examen_concurso"
    if "solucionario" in haystack or "resolucion" in haystack:
        return "solucionario"
    if "boletin" in haystack:
        return "boletin"
    if "seminario" in haystack:
        return "seminario"
    if "practica" in haystack or "problemas" in haystack:
        return "practica"
    if "teoria" in haystack or "teorico" in haystack:
        return "teoria"
    return "libro"


def infer_bibliographic_fields(
    *,
    pdf_path: str | Path,
    pdf_relpath: str = "",
    metadata_title: str = "",
    metadata_author: str = "",
) -> dict[str, str]:
    relpath = str(pdf_relpath or "").replace("\\", "/")
    parts = [part for part in relpath.split("/") if part]
    folder_parts = parts[:-1]
    cleaned_folders = [clean_bibliographic_segment(part) for part in folder_parts[1:]]
    cleaned_folders = [part for part in cleaned_folders if part]
    normalized_folders = [normalize_for_match(part) for part in cleaned_folders]

    metadata_title_used = _is_meaningful_pdf_metadata(metadata_title) and not metadata_title.lower().strip().endswith(".pdf")
    metadata_author_used = _is_meaningful_pdf_metadata(metadata_author)
    title = metadata_title.strip() if metadata_title_used else clean_bibliographic_segment(Path(str(pdf_path)).name)
    author = metadata_author.strip() if metadata_author_used else ""
    editorial = ""
    collection = cleaned_folders[0] if cleaned_folders else ""

    for original, normalized in zip(cleaned_folders, normalized_folders):
        if normalized.startswith("editorial "):
            editorial = clean_bibliographic_segment(original.replace("Editorial", "", 1))
            break
        for key, display in INSTITUTION_OR_EDITORIAL_NAMES.items():
            if key in normalized:
                editorial = display
                break
        if editorial:
            break

    if not author:
        author_candidates = []
        for original, normalized in zip(cleaned_folders, normalized_folders):
            if normalized in GENERIC_PATH_PARTS:
                continue
            if normalized in COLLECTION_ONLY_NAMES:
                continue
            if any(key in normalized for key in INSTITUTION_OR_EDITORIAL_NAMES):
                continue
            if "editorial" in normalized:
                continue
            author_candidates.append(original)
        if len(author_candidates) >= 2:
            author = author_candidates[-1]
        elif len(author_candidates) == 1 and not editorial:
            author = author_candidates[0]

    sources = []
    if metadata_title_used or metadata_author_used:
        sources.append("pdf_metadata")
    if cleaned_folders:
        sources.append("path")
    if "pdf_metadata" in sources and "path" in sources:
        status = "mixed_pdf_path"
    elif "pdf_metadata" in sources:
        status = "metadata_pdf"
    elif sources:
        status = "inferred_from_path"
    else:
        status = "pending_review"
    notes = "Campos inferidos desde ruta/nombre; validar visualmente portada e indice."
    if status == "metadata_pdf":
        notes = "Campos obtenidos parcialmente desde metadatos PDF; validar porque muchos escaneos tienen metadatos imprecisos."
    return {
        "bibliographic_title": title,
        "bibliographic_author": author,
        "bibliographic_editorial": editorial,
        "bibliographic_collection": collection,
        "material_type": infer_material_type(pdf_path=pdf_path, pdf_relpath=pdf_relpath),
        "bibliographic_source": "+".join(sources) if sources else "pending",
        "bibliographic_status": status,
        "bibliographic_notes": notes,
    }


def infer_course_folder(*, pdf_path: str | Path, pdf_relpath: str = "") -> str:
    rel_text = str(pdf_relpath or "").replace("\\", "/")
    source = f"{rel_text} {Path(str(pdf_path)).name}"
    haystack = normalize_for_match(source)
    for folder, keywords in COURSE_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return folder
    return "Por Clasificar"


def detect_part_reason(*, pdf_path: str | Path, pdf_relpath: str = "") -> str:
    relpath = str(pdf_relpath or pdf_path).replace("\\", "/")
    name = Path(str(pdf_path)).stem
    normalized_name = normalize_for_match(name)
    normalized_rel = normalize_for_match(relpath)
    parts = [normalize_for_match(part) for part in relpath.split("/")]
    if any(marker in part for marker in PART_FOLDER_MARKERS for part in parts[:-1]):
        return "Carpeta de imagenes/recortes detectada."
    if PART_PROBLEM_RE.search(normalized_name):
        return "PDF individual de problema detectado."
    if PART_EXPLICIT_PAGE_RANGE_RE.search(normalized_name):
        return "Nombre con rango explicito de paginas detectado."
    if PART_RANGE_RE.search(name) or PART_RANGE_RE.search(normalized_name):
        return "Nombre con rango de paginas detectado."
    if PART_MARKER_RE.search(normalized_name):
        return "Nombre de parte/fragmento detectado."
    # Vesalius-style extracted page ranges often use many separators before page numbers.
    if re.search(r"\d{1,4}\s*-{4,}\s*\d{1,4}", name):
        return "Nombre con rango de paginas separado por guiones detectado."
    if "solucionario imagenes" in normalized_rel:
        return "PDF asociado a carpeta de imagenes de solucionario."
    return ""


def is_part_candidate(*, pdf_path: str | Path, pdf_relpath: str = "") -> bool:
    return bool(detect_part_reason(pdf_path=pdf_path, pdf_relpath=pdf_relpath))


def _candidate_general_score(candidate: Path, part_path: Path) -> tuple[int, int]:
    try:
        size = int(candidate.stat().st_size)
    except OSError:
        size = 0
    part_stem = normalize_for_match(part_path.stem)
    candidate_stem = normalize_for_match(candidate.stem)
    token_overlap = len(set(part_stem.split()) & set(candidate_stem.split()))
    return (token_overlap, size)


def _directory_pdfs(directory: Path, cache: dict[Path, tuple[Path, ...]] | None = None) -> tuple[Path, ...]:
    try:
        key = directory.resolve()
    except OSError:
        key = directory
    if cache is not None and key in cache:
        return cache[key]
    try:
        pdfs = tuple(directory.glob("*.pdf"))
    except OSError:
        pdfs = ()
    if cache is not None:
        cache[key] = pdfs
    return pdfs


def find_general_pdf_for_part(
    pdf_path: Path,
    *,
    source_root: Path | None = None,
    directory_pdf_cache: dict[Path, tuple[Path, ...]] | None = None,
) -> Path | None:
    pdf_path = pdf_path.resolve()
    search_dirs: list[Path] = []
    current = pdf_path.parent
    root = source_root.resolve() if source_root else None
    for _ in range(4):
        if current in search_dirs:
            break
        search_dirs.append(current)
        if root is not None and current == root:
            break
        if current.parent == current:
            break
        current = current.parent

    candidates: list[Path] = []
    for directory in search_dirs:
        for candidate in _directory_pdfs(directory, directory_pdf_cache):
            try:
                if candidate.resolve() == pdf_path:
                    continue
            except OSError:
                continue
            rel_for_candidate = relative_to_root(candidate, root) if root else str(candidate)
            if is_part_candidate(pdf_path=candidate, pdf_relpath=rel_for_candidate):
                continue
            candidates.append(candidate.resolve())
    if not candidates:
        return None
    candidates = sorted(set(candidates), key=lambda item: _candidate_general_score(item, pdf_path), reverse=True)
    return candidates[0]


def find_part_match(*, pdf_path: Path, source_root: Path, pdf_relpath: str = "") -> PartMatch | None:
    reason = detect_part_reason(pdf_path=pdf_path, pdf_relpath=pdf_relpath)
    if not reason:
        return None
    general = find_general_pdf_for_part(pdf_path, source_root=source_root)
    if general is None:
        return None
    return PartMatch(
        match_type="part_of_general_pdf",
        general_pdf_path=str(general),
        part_pdf_path=str(pdf_path.resolve()),
        reason=reason,
    )


def write_part_record(output_root: Path, part: PartMatch) -> Path:
    parts_path = output_root.resolve() / "parts_rejected.jsonl"
    parts_path.parent.mkdir(parents=True, exist_ok=True)
    row = asdict(part)
    row["detected_at"] = utc_now_iso()
    existing_keys: set[tuple[str, str, str]] = set()
    if parts_path.exists():
        with parts_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                try:
                    existing = json.loads(line)
                except Exception:
                    continue
                existing_keys.add(
                    (
                        str(existing.get("part_pdf_path", "")),
                        str(existing.get("general_pdf_path", "")),
                        str(existing.get("match_type", "")),
                    )
                )
    key = (part.part_pdf_path, part.general_pdf_path, part.match_type)
    if key in existing_keys:
        return parts_path
    with parts_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return parts_path


def render_parts_markdown(output_root: Path) -> Path:
    parts_path = output_root.resolve() / "parts_rejected.jsonl"
    markdown_path = output_root.resolve() / "Partes rechazadas.md"
    rows: list[dict[str, Any]] = []
    if parts_path.exists():
        with parts_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    lines = ["# Partes rechazadas", ""]
    lines.append(f"Total registros: `{len(rows)}`")
    lines.append("")
    if rows:
        lines.append("| parte | PDF general | motivo |")
        lines.append("|---|---|---|")
        for row in rows:
            lines.append(
                f"| `{row.get('part_pdf_path', '')}` | `{row.get('general_pdf_path', '')}` | {row.get('reason', '')} |"
            )
    else:
        lines.append("Sin partes rechazadas hasta ahora.")
    markdown_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return markdown_path


def markdown_relpath(from_dir: Path, target: Path) -> str:
    return os.path.relpath(target, start=from_dir).replace("\\", "/")


def file_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def file_mtime_iso(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat(timespec="seconds")


def build_book_id(pdf_path: Path, pdf_hash_sha256: str) -> str:
    return f"{safe_slug(pdf_path.stem, fallback='libro')}-{pdf_hash_sha256[:10]}"


def relative_to_root(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except Exception:
        return str(path.resolve()).replace("\\", "/")


def iter_pdf_paths(source_root: Path) -> list[Path]:
    source_root = source_root.resolve()
    rg_cmd = shutil.which("rg")
    if rg_cmd:
        result = subprocess.run(
            [rg_cmd, "--files", str(source_root), "-g", "*.pdf"],
            check=True,
            capture_output=True,
        )
        encodings = ["utf-8-sig", locale.getpreferredencoding(False), "cp1252", "cp850"]
        best_text = ""
        best_score = -1
        for encoding in dict.fromkeys(encodings):
            try:
                text = result.stdout.decode(encoding)
            except UnicodeDecodeError:
                continue
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            exists_score = sum(1 for line in lines[:200] if Path(line).exists())
            replacement_penalty = text.count("\ufffd")
            score = exists_score - replacement_penalty
            if score > best_score:
                best_score = score
                best_text = text
        return sorted(Path(line.strip()) for line in best_text.splitlines() if line.strip())
    return sorted(source_root.rglob("*.pdf"))


def build_pdf_listing(source_root: Path) -> list[PdfListingRecord]:
    source_root = source_root.resolve()
    records: list[PdfListingRecord] = []
    directory_pdf_cache: dict[Path, tuple[Path, ...]] = {}
    for index, pdf_path in enumerate(iter_pdf_paths(source_root), start=1):
        pdf_path = pdf_path.resolve()
        relpath = relative_to_root(pdf_path, source_root)
        top_folder = relpath.split("/", 1)[0] if "/" in relpath else ""
        try:
            stat = pdf_path.stat()
            file_size_bytes = int(stat.st_size)
            modified_at = file_mtime_iso(pdf_path)
        except OSError:
            file_size_bytes = 0
            modified_at = ""
        bibliographic = infer_bibliographic_fields(pdf_path=pdf_path, pdf_relpath=relpath)
        part_reason = detect_part_reason(pdf_path=pdf_path, pdf_relpath=relpath)
        general_candidate = (
            find_general_pdf_for_part(
                pdf_path,
                source_root=source_root,
                directory_pdf_cache=directory_pdf_cache,
            )
            if part_reason
            else None
        )
        confirmed_part = bool(part_reason and general_candidate)
        records.append(
            PdfListingRecord(
                index=index,
                course=infer_course_folder(pdf_path=pdf_path, pdf_relpath=relpath),
                source_top_folder=top_folder,
                pdf_path=str(pdf_path),
                pdf_relpath=relpath,
                file_name=pdf_path.name,
                file_size_bytes=file_size_bytes,
                modified_at=modified_at,
                bibliographic_title=bibliographic["bibliographic_title"],
                bibliographic_author=bibliographic["bibliographic_author"],
                bibliographic_editorial=bibliographic["bibliographic_editorial"],
                bibliographic_collection=bibliographic["bibliographic_collection"],
                material_type=bibliographic["material_type"],
                bibliographic_status=bibliographic["bibliographic_status"],
                part_candidate=confirmed_part,
                part_reason=part_reason if confirmed_part else "",
                general_candidate_path=str(general_candidate) if general_candidate else "",
            )
        )
    return records


def _format_size_mb(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.2f}"


def render_pdf_listing_markdown(records: list[PdfListingRecord], *, title: str = "Listado PDF") -> str:
    lines: list[str] = [f"# {title}", ""]
    lines.append(f"Total PDF: `{len(records)}`")
    lines.append("")
    if records:
        top_folders = sorted({record.source_top_folder or "(raiz)" for record in records})
        lines.append("## Resumen por carpeta fuente")
        lines.append("")
        lines.append("| carpeta | PDFs |")
        lines.append("|---|---:|")
        for folder in top_folders:
            total = sum(1 for record in records if (record.source_top_folder or "(raiz)") == folder)
            lines.append(f"| `{folder}` | {total} |")
        lines.append("")
    for course in COURSE_FOLDERS:
        rows = [record for record in records if record.course == course]
        lines.append(f"## {course}")
        lines.append("")
        lines.append(f"Total: `{len(rows)}`")
        lines.append("")
        if not rows:
            lines.append("Sin PDFs encontrados.")
            lines.append("")
            continue
        lines.append("| # | Titulo | Autor | Editorial/Fuente | Tipo | Fragmento | PDF general | PDF | MB |")
        lines.append("|---:|---|---|---|---|---|---|---|---:|")
        for record in rows:
            rel = record.pdf_relpath.replace("|", "\\|")
            title = record.bibliographic_title.replace("|", "\\|")
            author = (record.bibliographic_author or "-").replace("|", "\\|")
            editorial = (record.bibliographic_editorial or record.bibliographic_collection or "-").replace("|", "\\|")
            general = (record.general_candidate_path or "-").replace("|", "\\|")
            part_flag = "si" if record.part_candidate else "no"
            lines.append(
                f"| {record.index} | `{title}` | `{author}` | `{editorial}` | "
                f"`{record.material_type}` | `{part_flag}` | `{general}` | `{rel}` | {_format_size_mb(record.file_size_bytes)} |"
            )
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_pdf_listing(*, source_root: Path, output_root: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    records = build_pdf_listing(source_root)
    output_root.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_root / "pdf_listing.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    markdown_path = output_root / "Listado PDF.md"
    markdown_path.write_text(render_pdf_listing_markdown(records), encoding="utf-8")

    courses_root = output_root / "Cursos"
    for course in COURSE_FOLDERS:
        course_dir = courses_root / course
        course_dir.mkdir(parents=True, exist_ok=True)
        course_records = [record for record in records if record.course == course]
        (course_dir / "00 Listado PDF.md").write_text(
            render_pdf_listing_markdown(course_records, title=f"Listado PDF - {course}"),
            encoding="utf-8",
        )
    return {
        "records": records,
        "jsonl_path": jsonl_path,
        "markdown_path": markdown_path,
        "total": len(records),
        "by_course": {course: sum(1 for record in records if record.course == course) for course in COURSE_FOLDERS},
    }


def parse_page_spec(page_spec: str | None, page_count: int) -> list[int]:
    if not page_spec:
        return list(range(1, page_count + 1))
    pages: set[int] = set()
    for raw_part in str(page_spec).split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_raw, end_raw = part.split("-", 1)
            start = max(1, int(start_raw))
            end = min(page_count, int(end_raw))
            if end < start:
                raise ValueError(f"Rango de paginas invalido: {part}")
            pages.update(range(start, end + 1))
            continue
        page = int(part)
        if page < 1 or page > page_count:
            raise ValueError(f"Pagina fuera de rango: {page}")
        pages.add(page)
    if not pages:
        raise ValueError("No se resolvieron paginas para procesar.")
    return sorted(pages)


def apply_page_limit(page_numbers: list[int], page_limit: int | None) -> list[int]:
    if page_limit is None or page_limit <= 0:
        return list(page_numbers)
    return list(page_numbers[:page_limit])


def load_pdf_metadata(pdf_path: Path) -> tuple[int | None, str, str, str]:
    try:
        reader = PdfReader(str(pdf_path))
        metadata = reader.metadata or {}
        title = str(getattr(metadata, "title", "") or metadata.get("/Title") or "").strip()
        author = str(getattr(metadata, "author", "") or metadata.get("/Author") or "").strip()
        return len(reader.pages), title, author, ""
    except Exception as exc:
        return None, "", "", str(exc)


def build_inventory_record(pdf_path: Path, *, source_root: Path) -> InventoryRecord:
    pdf_path = pdf_path.resolve()
    pdf_hash_sha256 = file_sha256(pdf_path)
    page_count, title, author, metadata_error = load_pdf_metadata(pdf_path)
    notes = metadata_error.strip()
    relpath = relative_to_root(pdf_path, source_root)
    bibliographic = infer_bibliographic_fields(
        pdf_path=pdf_path,
        pdf_relpath=relpath,
        metadata_title=title,
        metadata_author=author,
    )
    return InventoryRecord(
        schema_version=CATALOG_SCHEMA_VERSION,
        book_id=build_book_id(pdf_path, pdf_hash_sha256),
        source_root=str(source_root.resolve()),
        pdf_path=str(pdf_path),
        pdf_relpath=relpath,
        pdf_hash_sha256=pdf_hash_sha256,
        file_size_bytes=int(pdf_path.stat().st_size),
        modified_at=file_mtime_iso(pdf_path),
        discovered_at=utc_now_iso(),
        page_count=page_count,
        metadata_title=title,
        metadata_author=author,
        bibliographic_title=bibliographic["bibliographic_title"],
        bibliographic_author=bibliographic["bibliographic_author"],
        bibliographic_editorial=bibliographic["bibliographic_editorial"],
        bibliographic_collection=bibliographic["bibliographic_collection"],
        material_type=bibliographic["material_type"],
        bibliographic_source=bibliographic["bibliographic_source"],
        bibliographic_status=bibliographic["bibliographic_status"],
        bibliographic_notes=bibliographic["bibliographic_notes"],
        inventory_status="ok" if not metadata_error else "metadata_warning",
        notes=notes,
    )


def scan_pdf_inventory(source_root: Path, *, limit: int | None = None) -> list[InventoryRecord]:
    source_root = source_root.resolve()
    pdf_paths = sorted(source_root.rglob("*.pdf"))
    if limit is not None and limit > 0:
        pdf_paths = pdf_paths[:limit]
    return [build_inventory_record(path, source_root=source_root) for path in pdf_paths]


def write_inventory(records: list[InventoryRecord], inventory_path: Path) -> Path:
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    with inventory_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    return inventory_path


def upsert_inventory_record(record: InventoryRecord, inventory_path: Path) -> Path:
    rows: list[dict[str, Any]] = []
    if inventory_path.exists():
        with inventory_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    replaced = False
    for idx, row in enumerate(rows):
        if str(row.get("pdf_path") or "").strip().lower() == record.pdf_path.lower():
            rows[idx] = asdict(record)
            replaced = True
            break
    if not replaced:
        rows.append(asdict(record))
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    with inventory_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return inventory_path


def resolve_pdftoppm() -> str:
    command = shutil.which("pdftoppm")
    if command:
        return command
    fallback = Path(r"C:\Users\Danny Fabián\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdftoppm.exe")
    if fallback.exists():
        return str(fallback)
    raise FileNotFoundError("No se encontro pdftoppm en PATH ni en la ruta de MiKTeX esperada.")


def average_image_hash(image_path: Path, *, size: int = 8) -> str:
    with Image.open(image_path) as image:
        gray = ImageOps.grayscale(image)
        small = gray.resize((size, size), Image.Resampling.LANCZOS)
        pixels = list(small.getdata())
    average = sum(pixels) / max(1, len(pixels))
    bits = "".join("1" if value >= average else "0" for value in pixels)
    return f"{int(bits, 2):0{size * size // 4}x}"


def hamming_hex(first: str, second: str) -> int:
    try:
        return (int(first, 16) ^ int(second, 16)).bit_count()
    except Exception:
        return 10**9


def visual_signature_distance(first: list[str], second: list[str]) -> int:
    if not first or not second:
        return 10**9
    total = 0
    compared = 0
    for left, right in zip(first, second):
        total += hamming_hex(left, right)
        compared += 1
    if compared == 0:
        return 10**9
    return total


def _signature_to_text(signature: list[str]) -> str:
    return "|".join(signature)


def _signature_from_text(value: str) -> list[str]:
    return [part for part in str(value or "").split("|") if part]


def compute_visual_signature_from_page_images(page_image_paths: list[Path]) -> list[str]:
    signature: list[str] = []
    for image_path in page_image_paths[: len(VISUAL_FINGERPRINT_PAGES)]:
        if image_path.exists():
            signature.append(average_image_hash(image_path))
    return signature


def compute_pdf_visual_signature(
    *,
    pdf_path: Path,
    output_root: Path,
    book_id: str,
    page_count: int | None,
    force: bool = False,
) -> list[str]:
    fingerprint_dir = output_root.resolve() / "_fingerprints" / book_id
    pdftoppm_cmd = resolve_pdftoppm()
    page_paths: list[Path] = []
    max_page = int(page_count or max(VISUAL_FINGERPRINT_PAGES))
    for page_number in VISUAL_FINGERPRINT_PAGES:
        if page_number > max_page:
            continue
        image_path = fingerprint_dir / f"page-{page_number:04d}.png"
        render_page_png(
            pdftoppm_cmd=pdftoppm_cmd,
            pdf_path=pdf_path,
            page_number=page_number,
            output_path=image_path,
            dpi=55,
            force=force,
        )
        page_paths.append(image_path)
    return compute_visual_signature_from_page_images(page_paths)


def load_known_book_manifests(output_root: Path) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    books_root = output_root.resolve() / "books"
    if not books_root.exists():
        return manifests
    for manifest_path in books_root.glob("*/book.json"):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        payload["_manifest_path"] = str(manifest_path)
        manifests.append(payload)
    return manifests


def find_duplicate_match(
    *,
    record: InventoryRecord,
    output_root: Path,
    visual_signature: list[str] | None = None,
) -> DuplicateMatch | None:
    for payload in load_known_book_manifests(output_root):
        canonical_book_id = str(payload.get("book_id") or "").strip()
        canonical_pdf_path = str(payload.get("pdf_path") or "").strip()
        if canonical_book_id == record.book_id or canonical_pdf_path.lower() == record.pdf_path.lower():
            continue
        if str(payload.get("pdf_hash_sha256") or "").strip() == record.pdf_hash_sha256:
            return DuplicateMatch(
                match_type="exact_hash",
                canonical_book_id=canonical_book_id,
                canonical_pdf_path=canonical_pdf_path,
                candidate_book_id=record.book_id,
                candidate_pdf_path=record.pdf_path,
                reason="Mismo SHA-256 del PDF que un libro ya trabajado.",
            )
    if visual_signature:
        for payload in load_known_book_manifests(output_root):
            canonical_book_id = str(payload.get("book_id") or "").strip()
            canonical_pdf_path = str(payload.get("pdf_path") or "").strip()
            if canonical_book_id == record.book_id or canonical_pdf_path.lower() == record.pdf_path.lower():
                continue
            existing_signature = _signature_from_text(str(payload.get("visual_signature") or ""))
            if not existing_signature:
                continue
            distance = visual_signature_distance(existing_signature, visual_signature)
            if distance <= VISUAL_DUPLICATE_MAX_DISTANCE:
                return DuplicateMatch(
                    match_type="visual_fingerprint",
                    canonical_book_id=canonical_book_id,
                    canonical_pdf_path=canonical_pdf_path,
                    candidate_book_id=record.book_id,
                    candidate_pdf_path=record.pdf_path,
                    reason="Huella visual inicial muy similar a un libro ya trabajado.",
                    visual_distance=distance,
                )
    return None


def write_duplicate_record(output_root: Path, duplicate: DuplicateMatch) -> Path:
    duplicates_path = output_root.resolve() / "duplicates.jsonl"
    duplicates_path.parent.mkdir(parents=True, exist_ok=True)
    row = asdict(duplicate)
    row["detected_at"] = utc_now_iso()
    with duplicates_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return duplicates_path


def render_duplicate_markdown(output_root: Path) -> Path:
    duplicates_path = output_root.resolve() / "duplicates.jsonl"
    markdown_path = output_root.resolve() / "Duplicados.md"
    rows: list[dict[str, Any]] = []
    if duplicates_path.exists():
        with duplicates_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    lines = ["# Duplicados rechazados", ""]
    lines.append(f"Total registros: `{len(rows)}`")
    lines.append("")
    if rows:
        lines.append("| tipo | candidato | canonico | distancia | motivo |")
        lines.append("|---|---|---|---:|---|")
        for row in rows:
            distance = row.get("visual_distance")
            distance_text = "" if distance is None else str(distance)
            lines.append(
                f"| `{row.get('match_type', '')}` | `{row.get('candidate_pdf_path', '')}` | "
                f"`{row.get('canonical_pdf_path', '')}` | {distance_text} | {row.get('reason', '')} |"
            )
    else:
        lines.append("Sin duplicados rechazados hasta ahora.")
    markdown_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return markdown_path


def render_page_png(
    *,
    pdftoppm_cmd: str,
    pdf_path: Path,
    page_number: int,
    output_path: Path,
    dpi: int,
    force: bool,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not force:
        return output_path
    output_base = output_path.with_suffix("")
    cmd = [
        pdftoppm_cmd,
        "-png",
        "-r",
        str(int(dpi)),
        "-f",
        str(int(page_number)),
        "-l",
        str(int(page_number)),
        "-singlefile",
        str(pdf_path),
        str(output_base),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    if not output_path.exists():
        raise FileNotFoundError(f"pdftoppm no genero la pagina esperada: {output_path}")
    return output_path


def build_thumbnail(image_path: Path, thumbnail_path: Path, *, width: int = 320, force: bool = False) -> tuple[int, int]:
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as image:
        width_px, height_px = image.size
        if force or not thumbnail_path.exists():
            thumb = image.convert("RGB")
            thumb.thumbnail((int(width), int(width * 1.6)), Image.Resampling.LANCZOS)
            thumb.save(thumbnail_path, format="JPEG", quality=88, optimize=True)
    return int(width_px), int(height_px)


def build_page_record(
    *,
    book_id: str,
    pdf_path: Path,
    pdf_hash_sha256: str,
    page_count: int | None,
    page_number: int,
    image_path: Path,
    thumbnail_path: Path,
    image_width: int,
    image_height: int,
    book_dir: Path,
    analyzed_at: str,
    render_dpi: int,
) -> dict[str, Any]:
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "book_id": book_id,
        "pdf_path": str(pdf_path),
        "pdf_hash_sha256": pdf_hash_sha256,
        "page_count": page_count,
        "page_number": int(page_number),
        "page_label": DEFAULT_BOOTSTRAP_LABEL,
        "label_source": DEFAULT_LABEL_SOURCE,
        "label_confidence": 0.0,
        "review_status": "pending_human_review",
        "notes": "Etiqueta inicial conservadora. Revisar visualmente desde contact sheet u Obsidian.",
        "render_dpi": int(render_dpi),
        "image_width": int(image_width),
        "image_height": int(image_height),
        "image_path": str(image_path.relative_to(book_dir)).replace("\\", "/"),
        "thumbnail_path": str(thumbnail_path.relative_to(book_dir)).replace("\\", "/"),
        "analyzed_at": analyzed_at,
    }


def resolve_tesseract_cmd(tesseract_cmd: str | Path | None = None) -> str:
    if tesseract_cmd:
        path = Path(tesseract_cmd)
        if path.exists():
            return str(path)
    env_value = os.environ.get("BOOK_CATALOG_TESSERACT_CMD") or ""
    if env_value and Path(env_value).exists():
        return env_value
    found = shutil.which("tesseract")
    if found:
        return found
    if DEFAULT_TESSERACT_CMD.exists():
        return str(DEFAULT_TESSERACT_CMD)
    raise RuntimeError(
        "No se encontro tesseract.exe. Instala Tesseract OCR o pasa --tesseract-cmd."
    )


def resolve_tessdata_dir(output_root: Path, tessdata_dir: str | Path | None = None) -> Path | None:
    if tessdata_dir:
        path = Path(tessdata_dir).expanduser().resolve()
        if path.exists():
            return path
    cache_path = output_root.resolve() / "tessdata"
    if cache_path.exists():
        return cache_path
    return None


def ocr_image_text(
    *,
    image_path: Path,
    tesseract_cmd: str,
    tessdata_dir: Path | None = None,
    lang: str = DEFAULT_OCR_LANG,
) -> str:
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    config_parts = ["--psm", "6"]
    if tessdata_dir is not None:
        config_parts.extend(["--tessdata-dir", str(tessdata_dir)])
    with Image.open(image_path) as image:
        text = pytesseract.image_to_string(image, lang=lang, config=" ".join(config_parts))
    return text.strip()


def _score_keywords(text: str, keywords: tuple[str, ...]) -> int:
    return sum(text.count(keyword) for keyword in keywords)


def classify_ocr_text(*, text: str, page_number: int, page_count: int | None = None) -> OcrClassification:
    ascii_text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    normalized = normalize_for_match(text)
    chars = len(normalized)
    numbered_items = len(re.findall(r"(?:^|\s)\d{1,3}\s*[\.)]", ascii_text))
    alternatives = len(re.findall(r"\b[abcde]\s*[\.)]", ascii_text))
    scores = {
        "indice": _score_keywords(
            normalized,
            ("indice", "contenido", "contenidos", "tabla de contenido", "sumario"),
        ),
        "solucionario": _score_keywords(
            normalized,
            ("solucionario", "clave de respuestas", "claves", "respuestas", "respuesta"),
        ),
        "problemas_resueltos": _score_keywords(
            normalized,
            ("problema resuelto", "problemas resueltos", "resolucion", "resuelto", "solucion"),
        ),
        "problemas_propuestos": _score_keywords(
            normalized,
            (
                "problemas propuestos",
                "ejercicios propuestos",
                "ejercicios",
                "problemas",
                "practica",
                "calcular",
                "hallar",
                "indique",
                "resolver",
                "determinar",
                "valor de",
            ),
        ),
        "ejemplos": _score_keywords(normalized, ("ejemplo", "ejemplos", "aplicacion")),
        "teoria": _score_keywords(
            normalized,
            (
                "definicion",
                "teorema",
                "propiedad",
                "propiedades",
                "regla",
                "formula",
                "concepto",
                "observacion",
                "corolario",
            ),
        ),
    }
    if numbered_items >= 3:
        scores["problemas_propuestos"] += min(5, numbered_items // 2)
    if alternatives >= 6:
        scores["problemas_propuestos"] += min(5, alternatives // 4)
    if chars < 25:
        return OcrClassification(
            label="dudosa",
            confidence=0.1,
            reason="OCR insuficiente para clasificar.",
            scores=scores,
        )
    if page_number <= 2 and chars < 450 and max(scores.values() or [0]) <= 1:
        return OcrClassification(
            label="portada",
            confidence=0.55,
            reason="Pagina inicial con poco texto OCR.",
            scores=scores,
        )
    theory_signal = scores["teoria"] + scores["ejemplos"]
    problem_signal = scores["problemas_propuestos"] + scores["problemas_resueltos"] + scores["solucionario"]
    if theory_signal >= 2 and problem_signal >= 2:
        return OcrClassification(
            label="mixta",
            confidence=0.62,
            reason="Se detectaron senales de teoria y problemas en la misma pagina.",
            scores=scores,
        )
    priority = (
        "indice",
        "solucionario",
        "problemas_resueltos",
        "problemas_propuestos",
        "ejemplos",
        "teoria",
    )
    best_label = max(priority, key=lambda label: scores[label])
    best_score = scores[best_label]
    if best_score <= 0:
        return OcrClassification(
            label="dudosa",
            confidence=0.2,
            reason="Sin palabras clave suficientes.",
            scores=scores,
        )
    confidence = min(0.88, 0.35 + best_score * 0.13)
    return OcrClassification(
        label=best_label,
        confidence=confidence,
        reason=f"Etiqueta sugerida por OCR local: {best_label}.",
        scores=scores,
    )


def write_ocr_jsonl(ocr_rows: list[dict[str, Any]], ocr_jsonl_path: Path) -> Path:
    ocr_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with ocr_jsonl_path.open("w", encoding="utf-8") as handle:
        for row in ocr_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return ocr_jsonl_path


def write_pages_jsonl(page_rows: list[dict[str, Any]], pages_jsonl_path: Path) -> Path:
    pages_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with pages_jsonl_path.open("w", encoding="utf-8") as handle:
        for row in page_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return pages_jsonl_path


def build_label_ranges(page_rows: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_rows = sorted(page_rows, key=lambda row: int(row.get("page_number") or 0))
    ranges: list[dict[str, Any]] = []
    label_counts: dict[str, int] = {label: 0 for label in PAGE_LABELS}
    for row in normalized_rows:
        label = str(row.get("page_label") or DEFAULT_BOOTSTRAP_LABEL)
        label_counts[label] = int(label_counts.get(label, 0)) + 1
        page_number = int(row.get("page_number") or 0)
        if not ranges:
            ranges.append({"label": label, "start_page": page_number, "end_page": page_number, "pages_total": 1})
            continue
        last = ranges[-1]
        if label == last["label"] and page_number == int(last["end_page"]) + 1:
            last["end_page"] = page_number
            last["pages_total"] = int(last["pages_total"]) + 1
        else:
            ranges.append({"label": label, "start_page": page_number, "end_page": page_number, "pages_total": 1})
    return {"ranges": ranges, "label_counts": label_counts}


def write_ranges_json(
    *,
    book_id: str,
    pdf_path: Path,
    pdf_hash_sha256: str,
    page_rows: list[dict[str, Any]],
    ranges_json_path: Path,
    processed_pages_total: int,
    total_pdf_pages: int | None,
    analyzed_at: str,
) -> Path:
    payload = build_label_ranges(page_rows)
    output = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "book_id": book_id,
        "pdf_path": str(pdf_path),
        "pdf_hash_sha256": pdf_hash_sha256,
        "processed_pages_total": int(processed_pages_total),
        "total_pdf_pages": total_pdf_pages,
        "generated_at": analyzed_at,
        "ranges": payload["ranges"],
        "label_counts": payload["label_counts"],
    }
    ranges_json_path.parent.mkdir(parents=True, exist_ok=True)
    ranges_json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return ranges_json_path


def render_contact_sheets(
    *,
    book_dir: Path,
    page_rows: list[dict[str, Any]],
    columns: int = 4,
    rows_per_sheet: int = 3,
    force: bool = False,
) -> list[dict[str, Any]]:
    out_dir = book_dir / "contact_sheets"
    out_dir.mkdir(parents=True, exist_ok=True)
    pages_per_sheet = max(1, int(columns) * int(rows_per_sheet))
    cell_width = 320
    cell_height = 260
    card_inner_width = cell_width - 24
    card_inner_height = cell_height - 70
    font = ImageFont.load_default()
    sheet_rows: list[dict[str, Any]] = []
    for sheet_index in range(0, len(page_rows), pages_per_sheet):
        chunk = page_rows[sheet_index : sheet_index + pages_per_sheet]
        chunk_number = (sheet_index // pages_per_sheet) + 1
        sheet_path = out_dir / f"contact_sheet_{chunk_number:03d}.png"
        if not sheet_path.exists() or force:
            canvas = Image.new(
                "RGB",
                (columns * cell_width + 32, rows_per_sheet * cell_height + 32),
                color=CONTACT_SHEET_BG,
            )
            draw = ImageDraw.Draw(canvas)
            for item_index, row in enumerate(chunk):
                column = item_index % columns
                row_index = item_index // columns
                origin_x = 16 + column * cell_width
                origin_y = 16 + row_index * cell_height
                page_label = str(row.get("page_label") or DEFAULT_BOOTSTRAP_LABEL)
                accent = LABEL_COLORS.get(page_label, LABEL_COLORS[DEFAULT_BOOTSTRAP_LABEL])
                draw.rounded_rectangle(
                    (origin_x, origin_y, origin_x + cell_width - 16, origin_y + cell_height - 16),
                    radius=14,
                    fill="white",
                    outline=accent,
                    width=3,
                )
                draw.rectangle((origin_x, origin_y, origin_x + cell_width - 16, origin_y + 28), fill=accent)
                title = f"p.{int(row['page_number']):03d}  {page_label}"
                draw.text((origin_x + 10, origin_y + 8), title, fill="white", font=font)
                thumb_path = book_dir / str(row["thumbnail_path"])
                with Image.open(thumb_path) as thumb:
                    thumb_rgb = thumb.convert("RGB")
                    fitted = ImageOps.contain(
                        thumb_rgb,
                        (card_inner_width, card_inner_height),
                        method=Image.Resampling.LANCZOS,
                    )
                paste_x = origin_x + 12 + max(0, (card_inner_width - fitted.width) // 2)
                paste_y = origin_y + 40 + max(0, (card_inner_height - fitted.height) // 2)
                canvas.paste(fitted, (paste_x, paste_y))
            canvas.save(sheet_path, format="PNG")
        sheet_rows.append(
            {
                "sheet_index": chunk_number,
                "image_path": str(sheet_path.relative_to(book_dir)).replace("\\", "/"),
                "pages_total": len(chunk),
                "start_page": int(chunk[0]["page_number"]),
                "end_page": int(chunk[-1]["page_number"]),
            }
        )
    return sheet_rows


def render_obsidian_markdown(
    *,
    record: InventoryRecord,
    book_dir: Path,
    page_rows: list[dict[str, Any]],
    range_payload: dict[str, Any],
    contact_sheets: list[dict[str, Any]],
    analyzed_at: str,
) -> str:
    counts = range_payload.get("label_counts") or {}
    bibliographic = infer_bibliographic_fields(
        pdf_path=record.pdf_path,
        pdf_relpath=record.pdf_relpath,
        metadata_title=record.metadata_title,
        metadata_author=record.metadata_author,
    )
    title = record.bibliographic_title or bibliographic["bibliographic_title"]
    author = record.bibliographic_author or bibliographic["bibliographic_author"]
    editorial = record.bibliographic_editorial or bibliographic["bibliographic_editorial"]
    collection = record.bibliographic_collection or bibliographic["bibliographic_collection"]
    material_type = record.material_type or bibliographic["material_type"]
    bibliographic_status = record.bibliographic_status or bibliographic["bibliographic_status"]
    bibliographic_notes = record.bibliographic_notes or bibliographic["bibliographic_notes"]
    lines: list[str] = []
    lines.append("---")
    lines.append(f"catalog_schema: {CATALOG_SCHEMA_VERSION}")
    lines.append(f"book_id: {record.book_id}")
    lines.append(f"titulo: \"{title}\"")
    lines.append(f"autor: \"{author}\"")
    lines.append(f"editorial: \"{editorial}\"")
    lines.append(f"coleccion: \"{collection}\"")
    lines.append(f"tipo_material: {material_type}")
    lines.append(f"estado_bibliografico: {bibliographic_status}")
    lines.append(f"pdf_hash_sha256: {record.pdf_hash_sha256}")
    lines.append(f"page_count: {record.page_count if record.page_count is not None else 'null'}")
    lines.append(f"processed_pages_total: {len(page_rows)}")
    lines.append(f"analyzed_at: {analyzed_at}")
    lines.append("labels:")
    for label in PAGE_LABELS:
        lines.append(f"  - {label}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")
    lines.append("## Bibliografia")
    lines.append("")
    lines.append(f"- `titulo`: `{title}`")
    lines.append(f"- `autor`: `{author or 'pendiente'}`")
    lines.append(f"- `editorial`: `{editorial or 'pendiente'}`")
    lines.append(f"- `coleccion_fuente`: `{collection or 'pendiente'}`")
    lines.append(f"- `tipo_material`: `{material_type}`")
    lines.append(f"- `estado_bibliografico`: `{bibliographic_status}`")
    lines.append(f"- `nota`: {bibliographic_notes}")
    lines.append("")
    lines.append("## Fuente")
    lines.append("")
    lines.append(f"- `ruta_pdf`: `{record.pdf_path}`")
    lines.append(f"- `ruta_relativa`: `{record.pdf_relpath}`")
    lines.append(f"- `source_root`: `{record.source_root}`")
    lines.append(f"- `book_id`: `{record.book_id}`")
    lines.append(f"- `hash_pdf`: `{record.pdf_hash_sha256}`")
    lines.append(f"- `paginas_pdf`: `{record.page_count}`")
    lines.append(f"- `paginas_procesadas`: `{len(page_rows)}`")
    lines.append(f"- `inventario_status`: `{record.inventory_status}`")
    lines.append("")
    lines.append("## Resumen de etiquetas")
    lines.append("")
    lines.append("| etiqueta | paginas |")
    lines.append("|---|---:|")
    for label in PAGE_LABELS:
        lines.append(f"| `{label}` | {int(counts.get(label, 0) or 0)} |")
    lines.append("")
    lines.append("## Rangos procesados")
    lines.append("")
    lines.append("| inicio | fin | etiqueta | paginas |")
    lines.append("|---:|---:|---|---:|")
    for row in range_payload.get("ranges") or []:
        lines.append(
            f"| {int(row['start_page'])} | {int(row['end_page'])} | `{row['label']}` | {int(row['pages_total'])} |"
        )
    lines.append("")
    lines.append("## Contact Sheets")
    lines.append("")
    for sheet in contact_sheets:
        lines.append(
            f"- `sheet_{int(sheet['sheet_index']):03d}` paginas `{int(sheet['start_page'])}-{int(sheet['end_page'])}`"
        )
        lines.append(f"  ![]({sheet['image_path']})")
    lines.append("")
    lines.append("## Paginas procesadas")
    lines.append("")
    for row in page_rows:
        page_number = int(row["page_number"])
        ocr_link = ""
        if row.get("ocr_text_path"):
            ocr_link = f" [ocr]({row['ocr_text_path']})"
        lines.append(
            f"- p.{page_number:03d} `{row['page_label']}` "
            f"[png]({row['image_path']}) [thumb]({row['thumbnail_path']}){ocr_link}"
        )
    lines.append("")
    lines.append("## Notas de revision")
    lines.append("")
    if any(str(row.get("label_source") or "") == "local_tesseract_heuristic" for row in page_rows):
        lines.append("- Etiquetas sugeridas con OCR local Tesseract y heuristicas; requieren revision humana.")
        lines.append("- No se llamo a Hugging Face ni se modifico la base de datos.")
    else:
        lines.append("- Esta fase no aplica OCR ni BD; solo deja evidencia visual y estructura trazable.")
        lines.append("- La etiqueta inicial de todas las paginas procesadas es `dudosa` hasta revision humana o heuristica posterior.")
    return "\n".join(lines).strip() + "\n"


def write_obsidian_markdown(
    *,
    record: InventoryRecord,
    book_dir: Path,
    page_rows: list[dict[str, Any]],
    range_payload: dict[str, Any],
    contact_sheets: list[dict[str, Any]],
    analyzed_at: str,
) -> Path:
    note_path = book_dir / "obsidian.md"
    note_path.write_text(
        render_obsidian_markdown(
            record=record,
            book_dir=book_dir,
            page_rows=page_rows,
            range_payload=range_payload,
            contact_sheets=contact_sheets,
            analyzed_at=analyzed_at,
        ),
        encoding="utf-8",
    )
    return note_path


def repair_common_mojibake(value: str) -> str:
    text = str(value or "")
    for broken, fixed in COMMON_MOJIBAKE_REPLACEMENTS.items():
        text = text.replace(broken, fixed)
    if "Ã" in text or "Â" in text:
        try:
            candidate = text.encode("latin1").decode("utf-8")
        except UnicodeError:
            candidate = text
        if candidate.count("Ã") + candidate.count("Â") < text.count("Ã") + text.count("Â"):
            text = candidate
    return text


def clean_theme_name(value: str) -> str:
    text = repair_common_mojibake(value)
    text = clean_bibliographic_segment(text)
    text = re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", text).strip()
    text = re.sub(r"\s+", " ", text).strip(" ._-")
    if text and text.upper() == text:
        text = text.title()
    return text


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_jsonl_file(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _book_title_for_theme(book_dir: Path, book_json: dict[str, Any], record: InventoryRecord | None) -> str:
    bibliographic = book_json.get("bibliographic") if isinstance(book_json.get("bibliographic"), dict) else {}
    candidates = [
        bibliographic.get("title", ""),
        book_json.get("title", ""),
        record.bibliographic_title if record else "",
        record.metadata_title if record else "",
        Path(str(book_json.get("pdf_path") or (record.pdf_path if record else book_dir.name))).stem,
    ]
    for candidate in candidates:
        name = clean_theme_name(str(candidate or ""))
        if name:
            return name
    return ""


def _is_generic_theme_name(theme_name: str, *, book_json: dict[str, Any], record: InventoryRecord | None) -> bool:
    normalized = normalize_for_match(theme_name)
    if not normalized or len(normalized) < 4:
        return True
    generic_exact = {
        "nuevo documento",
        "nuevodocumento 03 30 2020 07 42 36",
        "pre uni algebra",
        "ceprevi algebra",
        "cuzcano celso gaspar",
        "aritmetica 2025",
        "2 me julio orihuela",
        "me julio orihuela",
    }
    if normalized in generic_exact:
        return True
    generic_prefixes = (
        "algebra 6 preun volumen",
        "algebra 7 preun volumen",
        "ordin 2019",
        "ordinario 2019",
    )
    if any(normalized.startswith(prefix) for prefix in generic_prefixes):
        return True
    bibliographic = book_json.get("bibliographic") if isinstance(book_json.get("bibliographic"), dict) else {}
    material_type = str(bibliographic.get("material_type") or (record.material_type if record else "")).strip()
    if material_type == "examen_concurso":
        return True
    return False


def _valid_page_span(start_page: Any, end_page: Any, *, page_count: int | None = None) -> tuple[int, int] | None:
    try:
        start = int(start_page)
        end = int(end_page)
    except (TypeError, ValueError):
        return None
    if start < 1 or end < start:
        return None
    if page_count is not None:
        if start > page_count:
            return None
        end = min(end, page_count)
    return start, end


def _segments_from_ranges(
    ranges_payload: dict[str, Any],
    *,
    start_page: int,
    end_page: int,
    page_count: int | None = None,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for row in ranges_payload.get("ranges") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        if label not in THEME_SEGMENT_LABELS:
            continue
        span = _valid_page_span(row.get("start_page"), row.get("end_page"), page_count=page_count)
        if span is None:
            continue
        row_start, row_end = span
        clipped_start = max(start_page, row_start)
        clipped_end = min(end_page, row_end)
        if clipped_start > clipped_end:
            continue
        segment = {"label": label, "start_page": clipped_start, "end_page": clipped_end}
        if segments and segments[-1]["label"] == label and int(segments[-1]["end_page"]) + 1 == clipped_start:
            segments[-1]["end_page"] = clipped_end
        else:
            segments.append(segment)
    return segments


def _all_processed_page_numbers(book_dir: Path, book_json: dict[str, Any]) -> list[int]:
    numbers: list[int] = []
    for value in book_json.get("processed_page_numbers") or []:
        try:
            numbers.append(int(value))
        except (TypeError, ValueError):
            continue
    if numbers:
        return sorted(set(numbers))
    rows = _read_jsonl_file(book_dir / "pages.jsonl")
    for row in rows:
        try:
            numbers.append(int(row.get("page_number") or 0))
        except (TypeError, ValueError):
            continue
    return sorted({number for number in numbers if number > 0})


def _read_index_ocr_text(book_dir: Path) -> str:
    chunks: list[str] = []
    ocr_jsonl = book_dir / "ocr" / "pages_ocr.jsonl"
    for row in _read_jsonl_file(ocr_jsonl):
        label = str(row.get("label") or "")
        if label not in {"indice", "mixta", "dudosa"}:
            continue
        text_path = row.get("ocr_text_path")
        if not text_path:
            continue
        path = book_dir / str(text_path)
        if path.exists():
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    if chunks:
        return "\n".join(chunks)
    ocr_dir = book_dir / "ocr"
    if ocr_dir.exists():
        for path in sorted(ocr_dir.glob("page-*.txt"))[:12]:
            chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def _extract_index_theme_candidates(book_dir: Path, *, page_count: int | None) -> list[dict[str, Any]]:
    text = repair_common_mojibake(_read_index_ocr_text(book_dir))
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    marker = re.compile(
        r"^\s*(?:UNIDAD|Unidad|CAP[IÍ]TULO|Cap[ií]tulo|TEMA|Tema)\s*"
        r"(?P<number>\d{1,3})\s*[-.:)]?\s*(?P<title>.+?)\s*$",
        re.IGNORECASE,
    )
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        match = marker.match(line)
        if not match:
            continue
        title_text = match.group("title")
        page_match = re.search(r"(?:\.{2,}|\s{2,}| )(?P<page>\d{1,4})\s*$", title_text)
        start_page: int | None = None
        if page_match:
            try:
                start_page = int(page_match.group("page"))
            except ValueError:
                start_page = None
            title_text = title_text[: page_match.start()].strip()
        title_text = re.sub(r"\.{2,}.*$", "", title_text).strip(" .-:")
        title = clean_theme_name(title_text)
        if not title:
            continue
        key = normalize_for_match(f"{match.group('number')} {title}")
        if key in seen:
            continue
        seen.add(key)
        if page_count is not None and start_page is not None and not (1 <= start_page <= page_count):
            start_page = None
        candidates.append(
            {
                "number": int(match.group("number")),
                "theme_name": title,
                "start_page": start_page,
                "source": "indice",
            }
        )
    candidates.sort(key=lambda item: (int(item.get("number") or 0), item.get("theme_name") or ""))
    return candidates


def _themes_from_index_candidates(
    candidates: list[dict[str, Any]],
    *,
    ranges_payload: dict[str, Any],
    page_count: int | None,
) -> list[dict[str, Any]]:
    candidates_with_pages = [item for item in candidates if item.get("start_page") is not None]
    if len(candidates_with_pages) < 2:
        return []
    candidates_with_pages.sort(key=lambda item: int(item["start_page"]))
    starts = [int(item["start_page"]) for item in candidates_with_pages]
    if any(current >= nxt for current, nxt in zip(starts, starts[1:])):
        return []
    end_bound = page_count or max(starts)
    themes: list[dict[str, Any]] = []
    for index, item in enumerate(candidates_with_pages):
        start_page = int(item["start_page"])
        end_page = (int(candidates_with_pages[index + 1]["start_page"]) - 1) if index + 1 < len(candidates_with_pages) else end_bound
        if end_page < start_page:
            continue
        themes.append(
            {
                "theme_name": str(item["theme_name"]),
                "start_page": start_page,
                "end_page": end_page,
                "confidence": "medium",
                "source": "indice",
                "segments": _segments_from_ranges(
                    ranges_payload,
                    start_page=start_page,
                    end_page=end_page,
                    page_count=page_count,
                ),
            }
        )
    return themes


def build_theme_payload(
    *,
    book_dir: Path,
    record: InventoryRecord | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    book_dir = book_dir.resolve()
    book_json = _read_json_file(book_dir / "book.json")
    ranges_payload = _read_json_file(book_dir / "ranges.json")
    book_id = str(book_json.get("book_id") or (record.book_id if record else book_dir.name))
    page_count: int | None = None
    for value in (book_json.get("page_count"), record.page_count if record else None):
        try:
            if value is not None:
                page_count = int(value)
                break
        except (TypeError, ValueError):
            continue
    page_numbers = _all_processed_page_numbers(book_dir, book_json)
    processed_start = min(page_numbers) if page_numbers else 1
    processed_end = max(page_numbers) if page_numbers else (page_count or 1)
    title = _book_title_for_theme(book_dir, book_json, record)
    candidates = _extract_index_theme_candidates(book_dir, page_count=page_count)
    index_themes = _themes_from_index_candidates(candidates, ranges_payload=ranges_payload, page_count=page_count)
    themes: list[dict[str, Any]]
    status: str
    pending_reason = ""
    if index_themes:
        themes = index_themes
        status = "multi_tema"
    elif title and not _is_generic_theme_name(title, book_json=book_json, record=record):
        start_page = processed_start
        end_page = page_count or processed_end
        themes = [
            {
                "theme_name": title,
                "start_page": start_page,
                "end_page": end_page,
                "confidence": "high",
                "source": "single-topic-book",
                "segments": _segments_from_ranges(
                    ranges_payload,
                    start_page=start_page,
                    end_page=end_page,
                    page_count=page_count,
                ),
            }
        ]
        status = "tema_unico"
    else:
        themes = []
        status = "pendiente"
        pending_reason = (
            "No hay indice con paginas confiables ni titulo mono-tema suficientemente claro; no se inventaron temas."
        )
    return {
        "schema_version": THEME_SCHEMA_VERSION,
        "book_id": book_id,
        "generated_at": generated_at or utc_now_iso(),
        "status": status,
        "themes": themes,
        "theme_candidates": candidates,
        "pending_reason": pending_reason,
    }


def render_themes_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("## Temas estructurados")
    lines.append("")
    lines.append(THEMES_MARKER_START)
    lines.append("")
    lines.append(f"- `schema`: `{payload.get('schema_version')}`")
    lines.append(f"- `estado`: `{payload.get('status')}`")
    if payload.get("pending_reason"):
        lines.append(f"- `pendiente`: {payload['pending_reason']}")
    lines.append("")
    themes = payload.get("themes") or []
    if themes:
        lines.append("| tema | inicio | fin | confianza | fuente | segmentos |")
        lines.append("|---|---:|---:|---|---|---|")
        for theme in themes:
            segments = []
            for segment in theme.get("segments") or []:
                segments.append(f"{segment.get('label')}:{segment.get('start_page')}-{segment.get('end_page')}")
            lines.append(
                "| "
                + str(theme.get("theme_name") or "")
                + f" | {theme.get('start_page')} | {theme.get('end_page')} | "
                + str(theme.get("confidence") or "")
                + " | "
                + str(theme.get("source") or "")
                + " | "
                + ("; ".join(segments) if segments else "-")
                + " |"
            )
    else:
        lines.append("Sin temas estructurados confiables por ahora.")
    candidates = payload.get("theme_candidates") or []
    if candidates and not themes:
        lines.append("")
        lines.append("### Candidatos detectados")
        lines.append("")
        for candidate in candidates:
            page_text = candidate.get("start_page") if candidate.get("start_page") is not None else "sin_pagina"
            lines.append(f"- `{candidate.get('number')}` {candidate.get('theme_name')} (`{page_text}`)")
    lines.append("")
    lines.append(THEMES_MARKER_END)
    return "\n".join(lines).strip() + "\n"


def update_obsidian_themes_section(book_dir: Path, payload: dict[str, Any]) -> Path:
    note_path = book_dir / "obsidian.md"
    block = render_themes_markdown(payload)
    if note_path.exists():
        text = note_path.read_text(encoding="utf-8", errors="ignore")
    else:
        text = f"# {payload.get('book_id') or book_dir.name}\n"
    if THEMES_MARKER_START in text and THEMES_MARKER_END in text:
        pattern = re.compile(
            r"## Temas estructurados\s*\n\s*"
            + re.escape(THEMES_MARKER_START)
            + r".*?"
            + re.escape(THEMES_MARKER_END),
            re.DOTALL,
        )
        text = pattern.sub(block.strip(), text)
        if not text.endswith("\n"):
            text += "\n"
    else:
        text = text.rstrip() + "\n\n" + block
    note_path.write_text(text, encoding="utf-8")
    return note_path


def write_themes_json(
    *,
    book_dir: Path,
    record: InventoryRecord | None = None,
    update_obsidian: bool = True,
    generated_at: str | None = None,
) -> dict[str, Any]:
    payload = build_theme_payload(book_dir=book_dir, record=record, generated_at=generated_at)
    output_path = book_dir / "themes.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if update_obsidian:
        update_obsidian_themes_section(book_dir, payload)
    payload["themes_json"] = str(output_path)
    return payload


def export_book_themes(
    output_root: Path,
    *,
    book_ids: list[str] | None = None,
    update_obsidian: bool = True,
) -> dict[str, Any]:
    output_root = output_root.resolve()
    records = records_by_book_id(output_root)
    books_root = output_root / "books"
    selected_ids = list(book_ids or [])
    if not selected_ids and books_root.exists():
        selected_ids = [path.name for path in sorted(books_root.iterdir()) if path.is_dir()]
    deduped_ids: list[str] = []
    for book_id in selected_ids:
        if book_id and book_id not in deduped_ids:
            deduped_ids.append(book_id)
    summary = {"multi_tema": 0, "tema_unico": 0, "pendiente": 0, "missing": 0}
    outputs: list[dict[str, Any]] = []
    generated_at = utc_now_iso()
    for book_id in deduped_ids:
        book_dir = books_root / book_id
        if not book_dir.exists():
            summary["missing"] += 1
            outputs.append({"book_id": book_id, "status": "missing", "themes_total": 0})
            continue
        payload = write_themes_json(
            book_dir=book_dir,
            record=records.get(book_id),
            update_obsidian=update_obsidian,
            generated_at=generated_at,
        )
        status = str(payload.get("status") or "pendiente")
        summary[status] = int(summary.get(status, 0)) + 1
        outputs.append(
            {
                "book_id": book_id,
                "status": status,
                "themes_total": len(payload.get("themes") or []),
                "themes_json": payload.get("themes_json"),
            }
        )
    return {
        "output_root": str(output_root),
        "generated_at": generated_at,
        "summary": summary,
        "books": outputs,
    }


def render_course_book_note(
    *,
    record: InventoryRecord,
    output_root: Path,
    course_note_path: Path,
) -> str:
    course_folder = infer_course_folder(pdf_path=record.pdf_path, pdf_relpath=record.pdf_relpath)
    book_dir = output_root / "books" / record.book_id
    ranges_path = book_dir / "ranges.json"
    book_note_path = book_dir / "obsidian.md"
    contact_sheets_dir = book_dir / "contact_sheets"
    note_dir = course_note_path.parent
    ranges_payload: dict[str, Any] = {}
    if ranges_path.exists():
        try:
            ranges_payload = json.loads(ranges_path.read_text(encoding="utf-8"))
        except Exception:
            ranges_payload = {}
    contact_sheets = sorted(contact_sheets_dir.glob("*.png")) if contact_sheets_dir.exists() else []
    counts = ranges_payload.get("label_counts") if isinstance(ranges_payload.get("label_counts"), dict) else {}
    bibliographic = infer_bibliographic_fields(
        pdf_path=record.pdf_path,
        pdf_relpath=record.pdf_relpath,
        metadata_title=record.metadata_title,
        metadata_author=record.metadata_author,
    )
    title = record.bibliographic_title or bibliographic["bibliographic_title"]
    author = record.bibliographic_author or bibliographic["bibliographic_author"]
    editorial = record.bibliographic_editorial or bibliographic["bibliographic_editorial"]
    collection = record.bibliographic_collection or bibliographic["bibliographic_collection"]
    material_type = record.material_type or bibliographic["material_type"]
    bibliographic_status = record.bibliographic_status or bibliographic["bibliographic_status"]
    bibliographic_notes = record.bibliographic_notes or bibliographic["bibliographic_notes"]
    lines: list[str] = []
    lines.append("---")
    lines.append(f"catalog_schema: {CATALOG_SCHEMA_VERSION}")
    lines.append(f"book_id: {record.book_id}")
    lines.append(f"curso: {course_folder}")
    lines.append(f"titulo: \"{title}\"")
    lines.append(f"autor: \"{author}\"")
    lines.append(f"editorial: \"{editorial}\"")
    lines.append(f"coleccion: \"{collection}\"")
    lines.append(f"tipo_material: {material_type}")
    lines.append(f"estado_bibliografico: {bibliographic_status}")
    lines.append(f"pdf_hash_sha256: {record.pdf_hash_sha256}")
    lines.append(f"page_count: {record.page_count if record.page_count is not None else 'null'}")
    lines.append(f"pdf_path: \"{record.pdf_path}\"")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")
    lines.append("## Bibliografia")
    lines.append("")
    lines.append(f"- `titulo`: `{title}`")
    lines.append(f"- `autor`: `{author or 'pendiente'}`")
    lines.append(f"- `editorial`: `{editorial or 'pendiente'}`")
    lines.append(f"- `coleccion_fuente`: `{collection or 'pendiente'}`")
    lines.append(f"- `tipo_material`: `{material_type}`")
    lines.append(f"- `estado_bibliografico`: `{bibliographic_status}`")
    lines.append(f"- `nota`: {bibliographic_notes}")
    lines.append("")
    lines.append("## Estado")
    lines.append("")
    lines.append(f"- `curso`: `{course_folder}`")
    lines.append(f"- `book_id`: `{record.book_id}`")
    lines.append(f"- `paginas_pdf`: `{record.page_count}`")
    lines.append(f"- `ruta_pdf`: `{record.pdf_path}`")
    if book_note_path.exists():
        lines.append(f"- `nota_visual`: [{record.book_id}]({markdown_relpath(note_dir, book_note_path)})")
    else:
        lines.append("- `nota_visual`: pendiente de procesar")
    lines.append("")
    lines.append("## Conteo visual")
    lines.append("")
    lines.append("| etiqueta | paginas |")
    lines.append("|---|---:|")
    for label in PAGE_LABELS:
        lines.append(f"| `{label}` | {int(counts.get(label, 0) or 0)} |")
    if contact_sheets:
        lines.append("")
        lines.append("## Laminas")
        lines.append("")
        for sheet_path in contact_sheets:
            lines.append(f"![]({markdown_relpath(note_dir, sheet_path)})")
    lines.append("")
    lines.append("## Revision")
    lines.append("")
    lines.append("- [ ] Revisar portada e indice")
    lines.append("- [ ] Marcar rangos de teoria y ejemplos")
    lines.append("- [ ] Marcar problemas propuestos/resueltos y solucionario")
    return "\n".join(lines).strip() + "\n"


def load_inventory(inventory_path: Path) -> list[InventoryRecord]:
    records: list[InventoryRecord] = []
    if not inventory_path.exists():
        return records
    with inventory_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                bibliographic = infer_bibliographic_fields(
                    pdf_path=payload.get("pdf_path", ""),
                    pdf_relpath=payload.get("pdf_relpath", ""),
                    metadata_title=payload.get("metadata_title", ""),
                    metadata_author=payload.get("metadata_author", ""),
                )
                for key, value in bibliographic.items():
                    payload.setdefault(key, value)
                records.append(InventoryRecord(**payload))
            except Exception:
                continue
    return records


def write_vault_scaffold(output_root: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    obsidian_dir = output_root / ".obsidian"
    courses_root = output_root / "Cursos"
    obsidian_dir.mkdir(parents=True, exist_ok=True)
    courses_root.mkdir(parents=True, exist_ok=True)
    for folder in COURSE_FOLDERS:
        (courses_root / folder).mkdir(parents=True, exist_ok=True)
    app_config = obsidian_dir / "app.json"
    if not app_config.exists():
        app_config.write_text(json.dumps({"legacyEditor": False}, indent=2), encoding="utf-8")
    return {"vault_root": output_root, "courses_root": courses_root, "obsidian_dir": obsidian_dir}


def organize_vault(output_root: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    scaffold = write_vault_scaffold(output_root)
    records = load_inventory(output_root / "inventory.jsonl")
    notes_by_course: dict[str, list[tuple[InventoryRecord, Path]]] = {folder: [] for folder in COURSE_FOLDERS}
    for record in records:
        course_folder = infer_course_folder(pdf_path=record.pdf_path, pdf_relpath=record.pdf_relpath)
        note_path = scaffold["courses_root"] / course_folder / f"{record.book_id}.md"
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(
            render_course_book_note(record=record, output_root=output_root, course_note_path=note_path),
            encoding="utf-8",
        )
        notes_by_course.setdefault(course_folder, []).append((record, note_path))

    pdf_listing_path = output_root / "Listado PDF.md"
    duplicates_path = output_root / "Duplicados.md"
    parts_path = output_root / "Partes rechazadas.md"
    index_lines = ["# Catalogo Visual de Libros", ""]
    if pdf_listing_path.exists():
        index_lines.append(f"- [Listado PDF completo]({markdown_relpath(output_root, pdf_listing_path)})")
    if duplicates_path.exists():
        index_lines.append(f"- [Duplicados rechazados]({markdown_relpath(output_root, duplicates_path)})")
    if parts_path.exists():
        index_lines.append(f"- [Partes rechazadas]({markdown_relpath(output_root, parts_path)})")
    if pdf_listing_path.exists() or duplicates_path.exists() or parts_path.exists():
        index_lines.append("")
    for course in COURSE_FOLDERS:
        course_dir = scaffold["courses_root"] / course
        rows = sorted(
            notes_by_course.get(course, []),
            key=lambda item: (item[0].bibliographic_title or Path(item[0].pdf_path).stem).lower(),
        )
        course_index = course_dir / "README.md"
        course_lines = [f"# {course}", ""]
        if rows:
            course_lines.append("| Libro | Autor | Editorial/Fuente | Tipo |")
            course_lines.append("|---|---|---|---|")
            for record, note_path in rows:
                title = record.bibliographic_title or Path(record.pdf_path).stem
                author = record.bibliographic_author or "-"
                editorial = record.bibliographic_editorial or record.bibliographic_collection or "-"
                course_lines.append(
                    f"| [{title}]({note_path.name}) | `{author}` | `{editorial}` | `{record.material_type or '-'}` |"
                )
        else:
            course_lines.append("Sin libros inventariados todavia.")
        course_index.write_text("\n".join(course_lines).strip() + "\n", encoding="utf-8")
        index_lines.append(f"## {course}")
        index_lines.append("")
        if rows:
            for record, note_path in rows:
                rel = markdown_relpath(output_root, note_path)
                title = record.bibliographic_title or Path(record.pdf_path).stem
                author = f" - {record.bibliographic_author}" if record.bibliographic_author else ""
                index_lines.append(f"- [{title}]({rel}){author}")
        else:
            index_lines.append("- Sin libros inventariados todavia.")
        index_lines.append("")
    index_path = output_root / "Indice General.md"
    index_path.write_text("\n".join(index_lines).strip() + "\n", encoding="utf-8")
    return {
        "vault_root": output_root,
        "index_path": index_path,
        "courses_root": scaffold["courses_root"],
        "records_total": len(records),
        "notes_total": sum(len(rows) for rows in notes_by_course.values()),
        "notes_by_course": {course: len(rows) for course, rows in notes_by_course.items()},
    }


def write_book_manifest(
    *,
    record: InventoryRecord,
    book_dir: Path,
    page_rows: list[dict[str, Any]],
    contact_sheets: list[dict[str, Any]],
    analyzed_at: str,
    render_dpi: int,
    visual_signature: list[str] | None = None,
) -> Path:
    manifest_path = book_dir / "book.json"
    payload = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "book_id": record.book_id,
        "bibliographic": {
            "title": record.bibliographic_title,
            "author": record.bibliographic_author,
            "editorial": record.bibliographic_editorial,
            "collection": record.bibliographic_collection,
            "material_type": record.material_type,
            "source": record.bibliographic_source,
            "status": record.bibliographic_status,
            "notes": record.bibliographic_notes,
        },
        "pdf_path": record.pdf_path,
        "pdf_relpath": record.pdf_relpath,
        "pdf_hash_sha256": record.pdf_hash_sha256,
        "page_count": record.page_count,
        "processed_pages_total": len(page_rows),
        "processed_page_numbers": [int(row["page_number"]) for row in page_rows],
        "contact_sheets_total": len(contact_sheets),
        "render_dpi": int(render_dpi),
        "visual_signature": _signature_to_text(visual_signature or []),
        "visual_signature_pages": list(VISUAL_FINGERPRINT_PAGES),
        "analyzed_at": analyzed_at,
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def process_pdf(
    *,
    pdf_path: Path,
    source_root: Path,
    output_root: Path,
    page_spec: str | None = None,
    page_limit: int | None = None,
    dpi: int = 120,
    thumbnail_width: int = 320,
    contact_sheet_columns: int = 4,
    contact_sheet_rows: int = 3,
    force: bool = False,
    allow_duplicates: bool = False,
    allow_parts: bool = False,
) -> dict[str, Any]:
    record = build_inventory_record(pdf_path, source_root=source_root)
    if record.page_count is None:
        raise RuntimeError(f"No se pudo resolver la cantidad de paginas para: {pdf_path}")
    output_root = output_root.resolve()
    part_match = None if allow_parts else find_part_match(
        pdf_path=Path(record.pdf_path),
        source_root=source_root,
        pdf_relpath=record.pdf_relpath,
    )
    if part_match is not None:
        parts_path = write_part_record(output_root, part_match)
        parts_markdown = render_parts_markdown(output_root)
        organize_vault(output_root)
        return {
            "status": "part_skipped",
            "record": record,
            "part": part_match,
            "parts_jsonl": parts_path,
            "parts_markdown": parts_markdown,
            "processed_pages_total": 0,
        }
    duplicate = None if allow_duplicates else find_duplicate_match(record=record, output_root=output_root)
    visual_signature: list[str] = []
    if duplicate is None:
        visual_signature = compute_pdf_visual_signature(
            pdf_path=Path(record.pdf_path),
            output_root=output_root,
            book_id=record.book_id,
            page_count=record.page_count,
            force=force,
        )
        duplicate = None if allow_duplicates else find_duplicate_match(
            record=record,
            output_root=output_root,
            visual_signature=visual_signature,
        )
    if duplicate is not None:
        duplicates_path = write_duplicate_record(output_root, duplicate)
        duplicate_markdown = render_duplicate_markdown(output_root)
        organize_vault(output_root)
        return {
            "status": "duplicate_skipped",
            "record": record,
            "duplicate": duplicate,
            "duplicates_jsonl": duplicates_path,
            "duplicates_markdown": duplicate_markdown,
            "processed_pages_total": 0,
        }
    page_numbers = parse_page_spec(page_spec, record.page_count)
    page_numbers = apply_page_limit(page_numbers, page_limit)
    analyzed_at = utc_now_iso()
    book_dir = output_root / "books" / record.book_id
    pages_dir = book_dir / "pages"
    thumbs_dir = book_dir / "thumbnails"
    pdftoppm_cmd = resolve_pdftoppm()

    page_rows: list[dict[str, Any]] = []
    for page_number in page_numbers:
        image_path = pages_dir / f"page-{page_number:04d}.png"
        thumb_path = thumbs_dir / f"page-{page_number:04d}.jpg"
        render_page_png(
            pdftoppm_cmd=pdftoppm_cmd,
            pdf_path=Path(record.pdf_path),
            page_number=page_number,
            output_path=image_path,
            dpi=dpi,
            force=force,
        )
        width_px, height_px = build_thumbnail(
            image_path,
            thumb_path,
            width=thumbnail_width,
            force=force,
        )
        page_rows.append(
            build_page_record(
                book_id=record.book_id,
                pdf_path=Path(record.pdf_path),
                pdf_hash_sha256=record.pdf_hash_sha256,
                page_count=record.page_count,
                page_number=page_number,
                image_path=image_path,
                thumbnail_path=thumb_path,
                image_width=width_px,
                image_height=height_px,
                book_dir=book_dir,
                analyzed_at=analyzed_at,
                render_dpi=dpi,
            )
        )

    pages_path = write_pages_jsonl(page_rows, book_dir / "pages.jsonl")
    range_payload = build_label_ranges(page_rows)
    ranges_path = write_ranges_json(
        book_id=record.book_id,
        pdf_path=Path(record.pdf_path),
        pdf_hash_sha256=record.pdf_hash_sha256,
        page_rows=page_rows,
        ranges_json_path=book_dir / "ranges.json",
        processed_pages_total=len(page_rows),
        total_pdf_pages=record.page_count,
        analyzed_at=analyzed_at,
    )
    contact_sheets = render_contact_sheets(
        book_dir=book_dir,
        page_rows=page_rows,
        columns=contact_sheet_columns,
        rows_per_sheet=contact_sheet_rows,
        force=force,
    )
    note_path = write_obsidian_markdown(
        record=record,
        book_dir=book_dir,
        page_rows=page_rows,
        range_payload=range_payload,
        contact_sheets=contact_sheets,
        analyzed_at=analyzed_at,
    )
    manifest_path = write_book_manifest(
        record=record,
        book_dir=book_dir,
        page_rows=page_rows,
        contact_sheets=contact_sheets,
        analyzed_at=analyzed_at,
        render_dpi=dpi,
        visual_signature=visual_signature,
    )
    inventory_path = upsert_inventory_record(record, output_root / "inventory.jsonl")
    duplicate_markdown = render_duplicate_markdown(output_root)
    parts_markdown = render_parts_markdown(output_root)
    vault_summary = organize_vault(output_root)
    return {
        "status": "processed",
        "record": record,
        "book_dir": book_dir,
        "pages_jsonl": pages_path,
        "ranges_json": ranges_path,
        "obsidian_md": note_path,
        "book_manifest": manifest_path,
        "inventory_jsonl": inventory_path,
        "vault_index": vault_summary["index_path"],
        "duplicates_markdown": duplicate_markdown,
        "parts_markdown": parts_markdown,
        "contact_sheets": contact_sheets,
        "processed_pages_total": len(page_rows),
    }


def classify_book_pages_with_ocr(
    *,
    record: InventoryRecord,
    output_root: Path,
    page_spec: str | None = None,
    page_limit: int | None = None,
    dpi: int = 150,
    thumbnail_width: int = 320,
    contact_sheet_columns: int = 4,
    contact_sheet_rows: int = 3,
    force_render: bool = False,
    force_ocr: bool = False,
    tesseract_cmd: str | Path | None = None,
    tessdata_dir: str | Path | None = None,
    lang: str = DEFAULT_OCR_LANG,
    workers: int = 1,
) -> dict[str, Any]:
    if record.page_count is None:
        raise RuntimeError(f"No se pudo resolver la cantidad de paginas para: {record.pdf_path}")
    output_root = output_root.resolve()
    book_dir = output_root / "books" / record.book_id
    pages_dir = book_dir / "pages"
    thumbs_dir = book_dir / "thumbnails"
    ocr_dir = book_dir / "ocr"
    page_numbers = parse_page_spec(page_spec, record.page_count)
    page_numbers = apply_page_limit(page_numbers, page_limit)
    analyzed_at = utc_now_iso()
    pdftoppm_cmd = resolve_pdftoppm()
    tesseract = resolve_tesseract_cmd(tesseract_cmd)
    tessdata = resolve_tessdata_dir(output_root, tessdata_dir)

    def process_one_page(page_number: int) -> tuple[dict[str, Any], dict[str, Any]]:
        image_path = pages_dir / f"page-{page_number:04d}.png"
        thumb_path = thumbs_dir / f"page-{page_number:04d}.jpg"
        ocr_text_path = ocr_dir / f"page-{page_number:04d}.txt"
        render_page_png(
            pdftoppm_cmd=pdftoppm_cmd,
            pdf_path=Path(record.pdf_path),
            page_number=page_number,
            output_path=image_path,
            dpi=dpi,
            force=force_render,
        )
        width_px, height_px = build_thumbnail(
            image_path,
            thumb_path,
            width=thumbnail_width,
            force=force_render,
        )
        if force_ocr or not ocr_text_path.exists():
            ocr_text_path.parent.mkdir(parents=True, exist_ok=True)
            text = ocr_image_text(
                image_path=image_path,
                tesseract_cmd=tesseract,
                tessdata_dir=tessdata,
                lang=lang,
            )
            ocr_text_path.write_text(text, encoding="utf-8")
        else:
            text = ocr_text_path.read_text(encoding="utf-8", errors="ignore")
        classification = classify_ocr_text(
            text=text,
            page_number=page_number,
            page_count=record.page_count,
        )
        row = build_page_record(
            book_id=record.book_id,
            pdf_path=Path(record.pdf_path),
            pdf_hash_sha256=record.pdf_hash_sha256,
            page_count=record.page_count,
            page_number=page_number,
            image_path=image_path,
            thumbnail_path=thumb_path,
            image_width=width_px,
            image_height=height_px,
            book_dir=book_dir,
            analyzed_at=analyzed_at,
            render_dpi=dpi,
        )
        row.update(
            {
                "page_label": classification.label,
                "label_source": "local_tesseract_heuristic",
                "label_confidence": classification.confidence,
                "review_status": "pending_human_review",
                "notes": classification.reason,
                "ocr_text_path": str(ocr_text_path.relative_to(book_dir)).replace("\\", "/"),
                "ocr_chars": len(text),
            }
        )
        ocr_row = {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "book_id": record.book_id,
            "pdf_path": record.pdf_path,
            "pdf_hash_sha256": record.pdf_hash_sha256,
            "page_number": page_number,
            "ocr_engine": "tesseract",
            "ocr_lang": lang,
            "ocr_text_path": str(ocr_text_path.relative_to(book_dir)).replace("\\", "/"),
            "ocr_chars": len(text),
            "label": classification.label,
            "confidence": classification.confidence,
            "reason": classification.reason,
            "scores": classification.scores,
            "analyzed_at": analyzed_at,
        }
        return row, ocr_row

    page_rows: list[dict[str, Any]] = []
    ocr_rows: list[dict[str, Any]] = []
    if int(workers or 1) <= 1:
        for page_number in page_numbers:
            page_row, ocr_row = process_one_page(page_number)
            page_rows.append(page_row)
            ocr_rows.append(ocr_row)
    else:
        max_workers = max(1, int(workers))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(process_one_page, page_number): page_number for page_number in page_numbers}
            for future in as_completed(futures):
                page_row, ocr_row = future.result()
                page_rows.append(page_row)
                ocr_rows.append(ocr_row)
        page_rows.sort(key=lambda row: int(row["page_number"]))
        ocr_rows.sort(key=lambda row: int(row["page_number"]))

    pages_path = write_pages_jsonl(page_rows, book_dir / "pages.jsonl")
    ocr_jsonl_path = write_ocr_jsonl(ocr_rows, ocr_dir / "pages_ocr.jsonl")
    range_payload = build_label_ranges(page_rows)
    ranges_path = write_ranges_json(
        book_id=record.book_id,
        pdf_path=Path(record.pdf_path),
        pdf_hash_sha256=record.pdf_hash_sha256,
        page_rows=page_rows,
        ranges_json_path=book_dir / "ranges.json",
        processed_pages_total=len(page_rows),
        total_pdf_pages=record.page_count,
        analyzed_at=analyzed_at,
    )
    contact_sheets = render_contact_sheets(
        book_dir=book_dir,
        page_rows=page_rows,
        columns=contact_sheet_columns,
        rows_per_sheet=contact_sheet_rows,
        force=True,
    )
    note_path = write_obsidian_markdown(
        record=record,
        book_dir=book_dir,
        page_rows=page_rows,
        range_payload=range_payload,
        contact_sheets=contact_sheets,
        analyzed_at=analyzed_at,
    )
    existing_manifest = book_dir / "book.json"
    visual_signature: list[str] = []
    if existing_manifest.exists():
        try:
            payload = json.loads(existing_manifest.read_text(encoding="utf-8"))
            visual_signature = _signature_from_text(str(payload.get("visual_signature") or ""))
        except Exception:
            visual_signature = []
    manifest_path = write_book_manifest(
        record=record,
        book_dir=book_dir,
        page_rows=page_rows,
        contact_sheets=contact_sheets,
        analyzed_at=analyzed_at,
        render_dpi=dpi,
        visual_signature=visual_signature,
    )
    inventory_path = upsert_inventory_record(record, output_root / "inventory.jsonl")
    vault_summary = organize_vault(output_root)
    return {
        "status": "ocr_classified",
        "record": record,
        "book_dir": book_dir,
        "pages_jsonl": pages_path,
        "ocr_jsonl": ocr_jsonl_path,
        "ranges_json": ranges_path,
        "obsidian_md": note_path,
        "book_manifest": manifest_path,
        "inventory_jsonl": inventory_path,
        "vault_index": vault_summary["index_path"],
        "contact_sheets": contact_sheets,
        "processed_pages_total": len(page_rows),
        "label_counts": range_payload["label_counts"],
    }


def first_book_ids_from_index(output_root: Path, *, limit: int) -> list[str]:
    index_path = output_root.resolve() / "Indice General.md"
    if not index_path.exists():
        return []
    text = index_path.read_text(encoding="utf-8", errors="ignore")
    ids: list[str] = []
    for match in re.finditer(r"\]\(Cursos/[^)]+/([^)\\/]+)\.md\)", text):
        book_id = match.group(1).strip()
        if book_id and book_id not in ids:
            ids.append(book_id)
        if len(ids) >= limit:
            break
    return ids


def records_by_book_id(output_root: Path) -> dict[str, InventoryRecord]:
    return {record.book_id: record for record in load_inventory(output_root.resolve() / "inventory.jsonl")}
