from __future__ import annotations

import csv
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / ".cache" / "book_catalog" / "repository_inventory"
SOURCE = INVENTORY / "whatsapp_books_master_complete.csv"
OUTPUT = INVENTORY / "curated_math_books"

TARGET_COURSES = {
    "Aritmetica",
    "Algebra",
    "Geometria",
    "Trigonometria",
    "Razonamiento matematico",
    "Geometria analitica",
    "Geometria del espacio",
    "Fisica",
    "Quimica",
}

COURSE_RULES = [
    ("Geometria del espacio", r"GEOMETRIA DEL ESPACIO|ESTEREOMETRIA"),
    ("Geometria analitica", r"GEOMETRIA ANALITICA"),
    ("Trigonometria", r"TRIGONOMETR"),
    ("Quimica", r"QUIMICA|BIOQUIMICA|ESTEQUIOMETR|ATOMISTICA"),
    ("Fisica", r"FISICA|MECANICA|ELECTRODINAM|ELECTROSTAT|CINEMATICA|DINAMICA"),
    ("Razonamiento matematico", r"RAZ(?:ONAMIENTO)?[ ._-]*(?:MAT|MATE)|HABILIDAD MATEMATICA"),
    ("Aritmetica", r"ARITMET"),
    ("Algebra", r"ALGEBR"),
    ("Geometria", r"GEOMETR"),
]

STRONG_BOOK = re.compile(
    r"\b(LIBRO|COMPENDIO|MANUAL|COLECCION|TOMO|VOLUMEN|ENCICLOPEDIA|"
    r"PROBLEMARIO|BANCO DE PROBLEMAS|PROBLEMAS (?:SELECTOS|RESUELTOS)|"
    r"PREUNIVERSITARIO|PRE[ -]?UNI|CURSO COMPLETO|TRATADO)\b"
)
PUBLISHER_BOOK = re.compile(
    r"\b(LUMBRERAS|RODO|CONAMAT|RUBINOS|CUZCANO|ORI(H|G)UELA|VESALIUS|"
    r"COLECCION SIGMA|EDITORIAL|SAN MARCOS|RACSO|MEGABYTE|COVEÑAS|COVENAS)\b"
)
CONSULTATION = re.compile(r"\b(FORMULARIO|RESUMEN|APUNTES?|TEORIA|DIAPOSITIVAS?|PPTX?)\b")
SOLUTION = re.compile(r"(?:\bSOL[ ._-]|\bSOLUCIONARIO|\bSOLUCIONES|\bRESPUESTAS|\bCLAVES?|\bRESUELTOS?)")
EXAM = re.compile(r"\b(EXAMEN|SIMULACRO|ADMISION|SUMATIVO)\b")
WEEKLY = re.compile(
    r"\b(SEMANA|SEMANAL|TAREA|DOMICILIARIA|PRACTICAS?|BOLETIN|"
    r"SESION|CLASE|FICHA|SEPARATA|HOJA DE TRABAJO|MATERIAL DE CLASE)\b"
)
NUMBERED_TOPIC = re.compile(r"^(?:0*\d{1,2}|S\s*0*\d{1,2})[ ._-]+")
MULTIPLE_CHOICE_HINT = re.compile(
    r"\b(PROBLEMAS|BANCO|PRACTICA|PREUNIVERSITARIO|PRE[ -]?UNI|ADMISION|SELECTOS)\b"
)
PREUNIVERSITY_COLLECTION = re.compile(
    r"\b(LUMBRERAS|RODO|CONAMAT|RUBINOS|CUZCANO|RACSO|VESALIUS|"
    r"COLECCION EL POSTULANTE|CESAR VALLEJO|ADUNI)\b"
)


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def normalized(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.upper().replace("Ñ", "N")
    return re.sub(r"\s+", " ", text).strip()


def infer_course(title: str, fallback: str) -> str:
    text = normalized(title)
    for course, pattern in COURSE_RULES:
        if re.search(pattern, text):
            return course
    return fallback


def semantic_key(title: str, course: str) -> str:
    text = normalized(Path(title).stem)
    text = re.sub(r"\b(COPIA DE|COPIA|COPY|COMPRESS(?:ED)?|SCANNED|ESCANEADO|NUEVO)\b", " ", text)
    text = re.sub(r"\b(SUPERACADEMY|FREELIBROS ORG)\b", " ", text)
    text = re.sub(r"\(\d+\)$", " ", text)
    text = re.sub(r"\b20\d{2}(?:[-_ ]?[12I]+)?\b", " ", text)
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return f"{course}|{text}"


def classify(title: str) -> tuple[str, str, int]:
    text = normalized(Path(title).stem)
    score = 0

    if SOLUTION.search(text):
        return "excluido", "solucionario_o_claves", -10
    if EXAM.search(text):
        return "excluido", "examen_o_simulacro", -8
    if WEEKLY.search(text):
        return "excluido", "material_semanal_o_clase", -7

    if STRONG_BOOK.search(text):
        score += 5
    if PUBLISHER_BOOK.search(text):
        score += 3
    if MULTIPLE_CHOICE_HINT.search(text):
        score += 2
    if CONSULTATION.search(text):
        score -= 3
    if NUMBERED_TOPIC.search(text) and not PUBLISHER_BOOK.search(text):
        score -= 2
    if len(text.split()) <= 2:
        score -= 1

    if score >= 3:
        return "libro_probable", "senales_fuertes_de_libro", score
    if CONSULTATION.search(text) and score <= 1:
        return "excluido", "consulta_teoria_o_formulario", score
    if NUMBERED_TOPIC.search(text) and not PUBLISHER_BOOK.search(text) and score <= 1:
        return "excluido", "capitulo_o_tema_suelto", score
    return "revision_visual", "titulo_ambiguo", score


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source_rows = load_csv(SOURCE)
    selected: list[dict[str, str]] = []

    for row in source_rows:
        course = infer_course(row.get("title", ""), row.get("course", ""))
        if course not in TARGET_COURSES:
            continue
        bucket, reason, score = classify(row.get("title", ""))
        item = dict(row)
        item["course"] = course
        item["curation_bucket"] = bucket
        item["curation_reason"] = reason
        item["book_score"] = str(score)
        title_normalized = normalized(row.get("title", ""))
        item["multiple_choice_status"] = (
            "probable"
            if MULTIPLE_CHOICE_HINT.search(title_normalized) or PREUNIVERSITY_COLLECTION.search(title_normalized)
            else "por_verificar"
        )
        item["course_scope"] = "mixto" if (
            ("ARITMET" in title_normalized and "ALGEBR" in title_normalized)
            or ("GEOMETR" in title_normalized and "TRIGONOMETR" in title_normalized)
        ) else "un_curso"
        item["semantic_key"] = semantic_key(row.get("title", ""), course)
        item["duplicate_status"] = "unico"
        selected.append(item)

    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in selected:
        groups[row["semantic_key"]].append(row)

    duplicates: list[dict[str, str]] = []
    for group in groups.values():
        if len(group) < 2:
            continue
        ordered = sorted(
            group,
            key=lambda row: (
                int(row.get("book_score", "0")),
                not normalized(row.get("title", "")).startswith("COPIA"),
                bool(row.get("size")),
                row.get("modified_time", ""),
            ),
            reverse=True,
        )
        ordered[0]["duplicate_status"] = "preferido"
        for duplicate in ordered[1:]:
            duplicate["duplicate_status"] = "duplicado_semantico"
            duplicates.append(duplicate)

    candidates = [
        row for row in selected
        if row["curation_bucket"] == "libro_probable" and row["duplicate_status"] != "duplicado_semantico"
    ]
    review = [
        row for row in selected
        if row["curation_bucket"] == "revision_visual" and row["duplicate_status"] != "duplicado_semantico"
    ]
    excluded = [row for row in selected if row["curation_bucket"] == "excluido"]
    priority = [row for row in candidates if row["multiple_choice_status"] == "probable"]

    fields = list(selected[0].keys()) if selected else []
    write_csv(OUTPUT / "01_libros_probables.csv", candidates, fields)
    write_csv(OUTPUT / "02_revision_visual.csv", review, fields)
    write_csv(OUTPUT / "03_material_excluido.csv", excluded, fields)
    write_csv(OUTPUT / "04_duplicados_semanticos.csv", duplicates, fields)
    write_csv(OUTPUT / "05_todos_los_cursos_objetivo.csv", selected, fields)
    write_csv(OUTPUT / "06_prioridad_opcion_multiple.csv", priority, fields)

    course_slugs = {
        "Aritmetica": "aritmetica",
        "Algebra": "algebra",
        "Geometria": "geometria",
        "Trigonometria": "trigonometria",
        "Razonamiento matematico": "razonamiento_matematico",
        "Geometria analitica": "geometria_analitica",
        "Geometria del espacio": "geometria_del_espacio",
        "Fisica": "fisica",
        "Quimica": "quimica",
    }
    for course, slug in course_slugs.items():
        course_rows = sorted(
            (row for row in priority if row["course"] == course),
            key=lambda row: row.get("title", "").casefold(),
        )
        write_csv(OUTPUT / f"curso_{slug}.csv", course_rows, fields)

    course_counts = Counter(row["course"] for row in candidates)
    review_counts = Counter(row["course"] for row in review)
    lines = [
        "# Depuracion de libros de ciencias y matematicas",
        "",
        f"- PDFs de cursos objetivo: {len(selected)}",
        f"- Libros probables no duplicados: {len(candidates)}",
        f"- Pendientes de revision visual: {len(review)}",
        f"- Material excluido: {len(excluded)}",
        f"- Duplicados semanticos separados: {len(duplicates)}",
        f"- Prioridad con opcion multiple probable: {len(priority)}",
        "",
        "## Libros probables por curso",
        "",
        "| Curso | Libros probables | Revision visual |",
        "|---|---:|---:|",
    ]
    for course in sorted(TARGET_COURSES):
        lines.append(f"| {course} | {course_counts[course]} | {review_counts[course]} |")
    lines += ["", "## Prioridad inicial para opcion multiple", ""]
    for course in sorted(TARGET_COURSES):
        lines.append(f"### {course}")
        lines.append("")
        course_priority = sorted(
            (row for row in priority if row["course"] == course),
            key=lambda row: row.get("title", "").casefold(),
        )
        if not course_priority:
            lines.append("- Sin libro confirmado por titulo; requiere revision visual.")
        for row in course_priority:
            scope = " (mixto)" if row.get("course_scope") == "mixto" else ""
            lines.append(f"- [{row.get('title', '')}]({row.get('url', '')}){scope}")
        lines.append("")
    lines += [
        "",
        "## Criterio",
        "",
        "`libro_probable` exige señales como libro, compendio, manual, colección, tomo, volumen, banco de problemas, editorial o preuniversitario.",
        "Semanas, tareas, clases, simulacros, solucionarios, formularios y capítulos sueltos se separan.",
        "La opción múltiple queda marcada como probable o por verificar; la confirmación definitiva requiere inspección visual del PDF.",
    ]
    (OUTPUT / "RESUMEN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Cursos objetivo: {len(selected)}")
    print(f"Libros probables: {len(candidates)}")
    print(f"Revision visual: {len(review)}")
    print(f"Excluidos: {len(excluded)}")
    print(f"Duplicados: {len(duplicates)}")


if __name__ == "__main__":
    main()
