from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / ".cache" / "book_catalog" / "repository_inventory" / "whatsapp_books_master_complete.csv"
STATE = ROOT / ".cache" / "book_catalog" / "repository_inventory" / "curated_math_books" / "review_decisions.json"
REPORT = ROOT / ".cache" / "book_catalog" / "repository_inventory" / "filename_classification_report.json"
SOURCE = "filename_rules_v1"


def normalized(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.upper().replace("Ñ", "N")
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


TARGET_RULES = [
    ("Geometria analitica", r"\bGEOMETRIA ANALITICA\b|\bCONICAS?\b|\bPARABOLA\b|\bHIPERBOLA\b", "keyword:geometria_analitica"),
    ("Geometria del espacio", r"\bGEOMETRIA DEL ESPACIO\b|\bESTEREOMETRIA\b|\bPOLIEDROS?\b", "keyword:geometria_espacio"),
    ("Trigonometria", r"\bTRIGONOMETR(?:IA|ICO|ICAS?)\b|\bTRIGO\b|\bIDENTIDADES TRIGONOMETRICAS\b|\bRAZONES TRIGONOMETRICAS\b|\bSISTEMA DE MEDIDAS ANGULARES\b", "keyword:trigonometria"),
    ("Quimica", r"\bQUIMICA\b|\bESTEQUIOMETRIA\b|\bATOMISTICA\b|\bENLACE QUIMICO\b|\bNOMENCLATURA QUIMICA\b|\bHIDROCARBUROS?\b|\bACIDOS? Y BASES?\b|\bGRUPOS FUNCIONALES\b|\bTABLA PERIODICA\b", "keyword:quimica"),
    ("Fisica", r"\bFISICA\b|\bANALISIS DIMENSIONAL\b|\bCINEMATICA\b|\bDINAMICA\b|\bESTATICA\b|\bHIDROSTATICA\b|\bELECTRODINAMICA\b|\bELECTROSTATICA\b|\bELECTROMAGNETISMO\b|\bCALORIMETRIA\b|\bTERMODINAMICA\b|\bFENOMENOS TERMICOS\b|\bTEMPERATURA CALOR\b|\bDILATACION TERMICA\b|\bCANTIDAD DE MOVIMIENTO\b|\bPROPAGACION DE LA LUZ\b|\bONDAS ELECTROMAGNETICAS\b|\bMOVIMIENTO ARMONICO SIMPLE\b|\bMCU\b|\bMRU\b|\bMRUV\b|\bMOVIMIENTO PARABOLICO\b", "keyword:fisica"),
    ("Razonamiento matematico", r"\bRAZONAMIENTO MATEMATICO\b|\bRAZ MAT(?:EMATICO)?\b|\bHABILIDAD MATEMATICA\b|\bAPTITUD ?MAT(?:EMATICA|E)\b|\bRAZ LOGICO\b|\bCONTEO DE FIGURAS\b|\bOPERACIONES MATEMATICAS\b|\bRM\b", "keyword:razonamiento_matematico"),
    ("Aritmetica", r"\bARITMETICA\b|\bNUMERACION\b|\bDIVISIBILIDAD\b|\bNUMEROS PRIMOS\b|\bMCD\b|\bMCM\b|\bRAZONES Y PROPORCIONES\b|\bREGLA DE TRES\b|\bPORCENTAJES\b|\bTANTO POR CIENTO\b|\bPROMEDIOS\b", "keyword:aritmetica"),
    ("Algebra", r"\bALGEBRA\b|\bLEYES DE EXPONENTES\b|\bECUACIONES EXPONENCIALES\b|\bPOLINOMIOS?\b|\bFACTORIZACION\b|\bPRODUCTOS NOTABLES\b|\bMATRICES\b|\bDETERMINANTES\b|\bINECUACIONES\b|\bECUACIONES POLINOMIALES\b|\bSISTEMAS? DE ECUACIONES\b", "keyword:algebra"),
    ("Geometria", r"\bGEOMETRIA\b|\bTRIANGULOS?\b|\bCUADRILATEROS?\b|\bPOLIGONOS?\b|\bCIRCUNFERENCIA\b|\bCONGRUENCIA DE TRIANGULOS\b|\bSEMEJANZA DE TRIANGULOS\b|\bPUNTOS NOTABLES\b|\bAREAS DE REGIONES TRIANGULARES\b|\bAREAS REGIONES TRIANGULARES\b|\bRELACIONES METRICAS\b|\bLINEA RECTA Y ANGULOS\b", "keyword:geometria"),
]

ACADEMY_CODES = [
    ("Algebra", "AL"),
    ("Aritmetica", "AR"),
    ("Aritmetica", "A"),
    ("Geometria", "G"),
    ("Geometria", "GE"),
    ("Trigonometria", "TR"),
    ("Trigonometria", "T"),
    ("Razonamiento matematico", "RM"),
    ("Fisica", "FI"),
    ("Quimica", "QU"),
    ("Quimica", "Q"),
]
ACADEMY = r"(?:ACV|AUNI\d*|AUN|RUNI|SUNI|SUN|IUNI|VCV|SCV|ENU|INTUNI|AVUNI|BCIENCIAS)"

OUTSIDE_RULES = [
    (r"\bRAZONAMIENTO VERBAL\b|\bRAZ VERBAL\b", "outside:razonamiento_verbal"),
    (r"\bLENGUAJE\b|\bGRAMATICA\b|\bORTOGRAFIA\b", "outside:lenguaje"),
    (r"\bLITERATURA\b", "outside:literatura"),
    (r"\bHISTORIA\b", "outside:historia"),
    (r"\bGEOGRAFIA\b|\bGEODINAMICA\b", "outside:geografia"),
    (r"\bECONOMIA\b", "outside:economia"),
    (r"\bFILOSOFIA\b|\bPSICOLOGIA\b", "outside:filosofia_psicologia"),
    (r"\bBIOLOGIA\b|\bANATOMIA\b|\bGENETICA\b|\bECOLOGIA\b", "outside:biologia"),
    (r"\bCIVICA\b|\bEDUCACION CIVICA\b", "outside:civica"),
    (r"\bINGLES\b", "outside:ingles"),
    (r"\bCOMUNICACION\b", "outside:comunicacion"),
    (r"\bVERBAL\b|\bSINONIMIA\b", "outside:verbal"),
    (r"\bCARTOGRAFIA\b|\bBIOMAS?\b", "outside:geografia_tema"),
    (r"\bAXIOLOGIA\b|\bETAPA COSMOLOGICA\b|\bPERIODO ONTOLOGICO\b|\bSILOGISMO\b", "outside:filosofia_tema"),
    (r"\bDERECHOS HUMANOS\b", "outside:civica_tema"),
    (r"\bCIENCIA HISTORICA\b|\bCOMUNIDAD PRIMITIVA\b|\bINCAS?\b|\bTAHUANTINSUYO\b", "outside:historia_tema"),
]
OUTSIDE_CODES = {"RV", "LE", "LI", "HP", "HU", "HI", "H", "EC", "PS", "IF", "F", "DD", "BI", "AN", "IN", "I", "CI", "C"}


def academy_code(text: str) -> str | None:
    for course, code in ACADEMY_CODES:
        prefix = r"^" if len(code) == 1 else r"(?:^| )"
        if re.search(
            rf"{prefix}{code} (?:{ACADEMY}|SEM(?:ANA)? ?\d+)(?: |$)|"
            rf"(?:^| ){ACADEMY}(?: TS\d+)? {code}(?: |$)",
            text,
        ):
            return course
    return None


def outside_code(text: str) -> str | None:
    for code in OUTSIDE_CODES:
        prefix = r"^" if len(code) == 1 else r"(?:^| )"
        if re.search(
            rf"{prefix}{code} (?:{ACADEMY}|SEM(?:ANA)? ?\d+)(?: |$)|"
            rf"(?:^| ){ACADEMY}(?: TS\d+)? {code}(?: |$)",
            text,
        ):
            return f"outside:code_{code.lower()}"
    return None


def classify_title(title: str) -> tuple[str, str] | None:
    text = normalized(Path(title or "").stem)
    if not text:
        return None
    for course, pattern, reason in TARGET_RULES:
        if re.search(pattern, text):
            return course, reason
    code_course = academy_code(text)
    if code_course:
        return code_course, f"code:{code_course}"
    for pattern, reason in OUTSIDE_RULES:
        if re.search(pattern, text):
            return "__outside__", reason
    reason = outside_code(text)
    if reason:
        return "__outside__", reason
    return None


def run(*, apply: bool) -> dict:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    decisions = state.setdefault("decisions", {})
    counts = Counter()
    reasons = Counter()
    samples: dict[str, list[str]] = defaultdict(list)

    with CATALOG.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))

    for row in rows:
        item_id = (row.get("drive_id") or "").strip()
        if not item_id or item_id in decisions:
            continue
        result = classify_title(row.get("title", ""))
        if not result:
            counts["doubtful"] += 1
            continue
        course, reason = result
        reasons[reason] += 1
        if len(samples[reason]) < 8:
            samples[reason].append(row.get("title", ""))
        timestamp = datetime.now(timezone.utc).isoformat()
        if course == "__outside__":
            counts["outside_excluded"] += 1
            decisions[item_id] = {
                "review_state": "excluido",
                "confirmed_course": "Pendiente",
                "material_type": "otro",
                "multiple_choice": "por_verificar",
                "notes": f"Excluido por nombre del PDF ({reason}).",
                "reviewed_at": timestamp,
                "classification_source": SOURCE,
                "classification_reason": reason,
            }
        else:
            counts[course] += 1
            decisions[item_id] = {
                "review_state": "confirmado",
                "confirmed_course": course,
                "material_type": "libro_problemas",
                "multiple_choice": "por_verificar",
                "notes": f"Curso inferido por nombre del PDF ({reason}).",
                "reviewed_at": timestamp,
                "classification_source": SOURCE,
                "classification_reason": reason,
            }

    report = {
        "schema_version": "filename_classification_report_v1",
        "apply": apply,
        "catalog_total": len(rows),
        "classified": sum(value for key, value in counts.items() if key not in {"doubtful", "outside_excluded"}),
        "outside_excluded": counts["outside_excluded"],
        "doubtful": counts["doubtful"],
        "by_course": {key: value for key, value in counts.items() if key not in {"doubtful", "outside_excluded"}},
        "by_reason": dict(reasons.most_common()),
        "samples": dict(samples),
    }
    cumulative = [
        decision
        for decision in decisions.values()
        if decision.get("classification_source") == SOURCE
    ]
    cumulative_courses = Counter(
        decision.get("confirmed_course", "Pendiente")
        for decision in cumulative
        if decision.get("review_state") == "confirmado"
    )
    report["cumulative"] = {
        "total": len(cumulative),
        "confirmed": sum(decision.get("review_state") == "confirmado" for decision in cumulative),
        "excluded": sum(decision.get("review_state") == "excluido" for decision in cumulative),
        "by_course": dict(cumulative_courses),
        "by_reason": dict(Counter(decision.get("classification_reason", "") for decision in cumulative).most_common()),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if apply:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = STATE.with_name(f"{STATE.stem}.before-filename-{stamp}{STATE.suffix}")
        shutil.copy2(STATE, backup)
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = STATE.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(STATE)
        report["backup"] = str(backup)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Clasifica PDFs pendientes usando solo su nombre")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
