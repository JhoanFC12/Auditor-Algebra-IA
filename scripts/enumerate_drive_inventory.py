from __future__ import annotations

import csv
import json
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import gdown


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / ".cache" / "book_catalog" / "repository_inventory"
PENDING = INVENTORY / "whatsapp_pending_folders.csv"
PENDING_INITIAL = INVENTORY / "whatsapp_pending_folders_initial.csv"
EXISTING = INVENTORY / "whatsapp_drive_pdfs.csv"
CHECKPOINT = INVENTORY / "whatsapp_drive_enumeration_checkpoint.jsonl"
OUTPUT = INVENTORY / "whatsapp_drive_pdfs_complete.csv"
ERRORS = INVENTORY / "whatsapp_drive_enumeration_errors.csv"
MASTER = INVENTORY / "whatsapp_books_master_complete.csv"

WRITE_LOCK = threading.Lock()


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def drive_id(url: str) -> str:
    match = re.search(r"/d/([^/?]+)", url or "")
    return match.group(1) if match else (url or "")


def completed_urls() -> set[str]:
    done: set[str] = set()
    if not CHECKPOINT.exists():
        return done
    with CHECKPOINT.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("status") == "ok":
                done.add(record.get("folder_url", ""))
    return done


def enumerate_folder(item: dict[str, str]) -> dict:
    url = item["url"]
    try:
        files = gdown.download_folder(
            url=url,
            quiet=True,
            remaining_ok=True,
            skip_download=True,
        ) or []
        pdfs = []
        for entry in files:
            title = Path(entry.path).name
            if title.lower().endswith(".pdf"):
                pdfs.append(
                    {
                        "source": "whatsapp",
                        "root_url": item.get("root_url", ""),
                        "parent_url": url,
                        "url": f"https://drive.google.com/file/d/{entry.id}/view",
                        "title": title,
                        "size": "",
                        "modified_time": "",
                        "depth": item.get("depth", ""),
                    }
                )
        result = {"status": "ok", "folder_url": url, "pdfs": pdfs}
    except Exception as exc:  # Network and public-folder parsing errors are recorded.
        result = {"status": "error", "folder_url": url, "error": str(exc), "pdfs": []}

    with WRITE_LOCK:
        with CHECKPOINT.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
    return result


def normalize(text: str) -> str:
    replacements = str.maketrans("ÁÉÍÓÚÜÑ", "AEIOUUN")
    return (text or "").upper().translate(replacements)


def infer_course(title: str) -> str:
    text = normalize(title)
    rules = [
        ("Geometria del espacio", r"GEOMETRIA DEL ESPACIO|ESTEREOMETRIA"),
        ("Geometria analitica", r"GEOMETRIA ANALITICA"),
        ("Trigonometria", r"TRIGONOMETR"),
        ("Geometria", r"GEOMETR"),
        ("Algebra", r"ALGEBR"),
        ("Aritmetica", r"ARITMET"),
        ("Razonamiento matematico", r"RAZ.*MAT|RAZONAMIENTO MAT"),
        ("Fisica", r"FISICA"),
        ("Quimica", r"QUIMICA"),
        ("Biologia", r"BIOLOG|ANATOM"),
        ("Lenguaje", r"LENGUAJE|GRAMATICA"),
        ("Literatura", r"LITERATURA"),
        ("Historia", r"HISTORIA"),
        ("Economia", r"ECONOM"),
        ("Filosofia/Psicologia", r"FILOSOF|PSICOLOG"),
    ]
    for course, pattern in rules:
        if re.search(pattern, text):
            return course
    return "Pendiente"


def infer_type(title: str) -> str:
    text = normalize(title)
    rules = [
        ("solucionario", r"SOLUCION|RESPUESTA|CLAVES"),
        ("examen", r"EXAMEN|SIMULACRO|ADMISION"),
        ("practica", r"PRACTICA|TAREA|PROBLEMAS|SEMINARIO"),
        ("consulta", r"TEORIA|RESUMEN|FORMULARIO|APUNTE"),
        ("libro", r"LIBRO|COMPENDIO|MANUAL|TOMO|VOLUMEN|COLECCION"),
    ]
    for kind, pattern in rules:
        if re.search(pattern, text):
            return kind
    return "pendiente"


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    INVENTORY.mkdir(parents=True, exist_ok=True)
    if PENDING.exists() and not PENDING_INITIAL.exists():
        shutil.copy2(PENDING, PENDING_INITIAL)
    pending_by_url = {row["url"]: row for row in load_csv(PENDING) if row.get("url")}
    done = completed_urls()
    work = [row for url, row in pending_by_url.items() if url not in done]
    print(f"Pendientes: {len(work)} de {len(pending_by_url)}", flush=True)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(enumerate_folder, item) for item in work]
        for index, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            if index % 50 == 0 or index == len(futures):
                errors = sum(item["status"] == "error" for item in results)
                print(f"Procesadas {index}/{len(futures)} | errores {errors}", flush=True)

    checkpoint_results: list[dict] = []
    if CHECKPOINT.exists():
        with CHECKPOINT.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    checkpoint_results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    pdf_by_id: dict[str, dict] = {}
    for row in load_csv(EXISTING):
        pdf_by_id[drive_id(row.get("url", ""))] = row
    for result in checkpoint_results:
        for row in result.get("pdfs", []):
            pdf_by_id[drive_id(row.get("url", ""))] = row

    pdf_rows = sorted(pdf_by_id.values(), key=lambda row: (row.get("title", "").casefold(), row.get("url", "")))
    pdf_fields = ["source", "root_url", "parent_url", "url", "title", "size", "modified_time", "depth"]
    write_csv(OUTPUT, pdf_rows, pdf_fields)

    latest_by_folder = {
        row.get("folder_url", ""): row
        for row in checkpoint_results
        if row.get("folder_url")
    }
    error_rows = [
        {"folder_url": row.get("folder_url", ""), "error": row.get("error", "")}
        for row in latest_by_folder.values()
        if row.get("status") == "error"
    ]
    write_csv(ERRORS, error_rows, ["folder_url", "error"])
    unresolved = [
        pending_by_url[url]
        for url in pending_by_url
        if latest_by_folder.get(url, {}).get("status") != "ok"
    ]
    write_csv(PENDING, unresolved, ["url", "root_url", "depth"])

    master_rows = []
    for row in pdf_rows:
        title = row.get("title", "")
        master_rows.append(
            {
                "drive_id": drive_id(row.get("url", "")),
                "title": title,
                "course": infer_course(title),
                "material_type": infer_type(title),
                "repository_url": row.get("root_url", ""),
                "parent_url": row.get("parent_url", ""),
                "url": row.get("url", ""),
                "size": row.get("size", ""),
                "modified_time": row.get("modified_time", ""),
                "status": "pendiente_revision_visual",
                "priority": "por_definir",
            }
        )
    write_csv(
        MASTER,
        master_rows,
        [
            "drive_id", "title", "course", "material_type", "repository_url",
            "parent_url", "url", "size", "modified_time", "status", "priority",
        ],
    )
    print(f"PDFs unicos consolidados: {len(pdf_rows)}", flush=True)
    print(f"Carpetas con error: {len(error_rows)}", flush=True)


if __name__ == "__main__":
    main()
