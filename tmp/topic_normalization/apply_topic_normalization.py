from __future__ import annotations

import csv
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from database.connection import DatabaseManager

OUT = Path('tmp/topic_normalization')
OUT.mkdir(parents=True, exist_ok=True)
STAMP = datetime.now().strftime('%Y%m%d_%H%M%S')
BACKUP = OUT / f'problemas_topics_backup_{STAMP}.csv'
REPORT = OUT / f'topic_normalization_report_{STAMP}.csv'
UNMAPPED = OUT / f'topic_unmapped_{STAMP}.csv'

TARGET_COURSES = {
    'algebra', 'geometria', 'trigonometria', 'geometria analitica', 'geometria del espacio', 'aritmetica'
}

COURSE_MAP = {
    'algebra': 'Algebra',
    'lgebra': 'Algebra',
    '�lgebra': 'Algebra',
    'geometria': 'Geometria',
    'trigonometria': 'Trigonometria',
    'geometria analitica': 'Geometria Analitica',
    'geometria del espacio': 'Geometria del Espacio',
    'aritmetica': 'Aritmetica',
}

TOPICS = {
    'Algebra': {
        'conceptos basicos': 'Conceptos Basicos',
        'expresiones algebraicas': 'Expresiones Algebraicas',
        'operaciones basicas': 'Operaciones Basicas',
        'operaciones algebraicas': 'Operaciones Basicas',
        'valor numerico': 'Valor Numerico',
        'productos notables': 'Productos Notables',
        'cocientes notables': 'Cocientes Notables',
        'division algebraica': 'Division Algebraica',
        'factorizacion': 'Factorizacion',
        'radicacion': 'Radicacion',
        'polinomios': 'Polinomios',
        'polinomios especiales': 'Polinomios Especiales',
        'teoria de grados': 'Teoria de Grados',
        'grados': 'Teoria de Grados',
        'binomio de newton': 'Binomio de Newton',
        'combinatoria': 'Combinatoria',
        'factorial': 'Factorial',
        'sumatorias': 'Sumatorias',
        'numeros complejos': 'Numeros Complejos',
        'fracciones parciales': 'Fracciones Parciales',
        'intervalos': 'Intervalos',
        'desigualdades': 'Desigualdades',
        'ecuaciones lineales': 'Ecuaciones Lineales',
        'ecuaciones de primer grado': 'Ecuaciones Lineales',
        'ecuaciones de primer y segundo grado': 'Ecuaciones Cuadraticas',
        'ecuaciones cuadraticas': 'Ecuaciones Cuadraticas',
        'ecuacion de grado superior': 'Ecuaciones de Grado Superior',
        'ecuaciones de grado superior': 'Ecuaciones de Grado Superior',
        'ecuaciones exponenciales': 'Ecuaciones Exponenciales',
        'sistema de ecuaciones': 'Sistemas de Ecuaciones Lineales',
        'sistema de ecuaciones lineales': 'Sistemas de Ecuaciones Lineales',
        'sistemas de ecuaciones lineales': 'Sistemas de Ecuaciones Lineales',
        'sistema de ecuaciones no lineales': 'Sistemas de Ecuaciones No Lineales',
        'sistemas de ecuaciones no lineales': 'Sistemas de Ecuaciones No Lineales',
        'teoria de ecuaciones': 'Teoria de Ecuaciones',
        'inecuaciones': 'Inecuaciones',
        'inecuaciones lineales': 'Inecuaciones Lineales',
        'inecuaciones cuadraticas': 'Inecuaciones Cuadraticas',
        'inecuaciones polinomiales': 'Inecuaciones Polinomiales',
        'inecuaciones racionales': 'Inecuaciones Racionales',
        'inecuaciones irracionales': 'Inecuaciones Irracionales',
        'inecuaciones exponenciales': 'Inecuaciones Exponenciales',
        'sistemas de inecuaciones': 'Sistemas de Inecuaciones',
        'funciones': 'Funciones',
        'formula de leibniz': 'Formula de Leibniz',
    },
    'Geometria': {
        'segmentos': 'Segmentos',
        'angulos': 'Angulos',
        'angulos entre rectas paralelas': 'Angulos entre Rectas Paralelas',
        'regiones convexas': 'Regiones Convexas',
        'conjuntos convexos': 'Regiones Convexas',
        'triangulos': 'Triangulos',
        'triangulo': 'Triangulos',
        'clasificacion de triangulos': 'Clasificacion de Triangulos',
        'lineas notables': 'Lineas Notables',
        'congruencia de triangulos': 'Congruencia de Triangulos',
        'aplicaciones de congruencia': 'Aplicaciones de Congruencia',
        'semejanza': 'Semejanza de Triangulos',
        'semejanza de triangulos': 'Semejanza de Triangulos',
        'proporcionalidad': 'Proporcionalidad de Segmentos',
        'proporcionalidad de segmentos': 'Proporcionalidad de Segmentos',
        'relaciones metricas': 'Relaciones Metricas',
        'relaciones metricas en el triangulo rectangulo': 'Relaciones Metricas en el Triangulo Rectangulo',
        'trazos auxiliares': 'Trazos Auxiliares',
        'desigualdades geometricas': 'Desigualdades Geometricas',
        'cuadrilateros': 'Cuadrilateros',
        'cuadrilatero inscrito e inscriptible': 'Cuadrilatero Inscrito e Inscriptible',
        'poligonos': 'Poligonos',
        'poligonos regulares': 'Poligonos Regulares',
        'circunferencia': 'Circunferencias',
        'circunferencias': 'Circunferencias',
        'posiciones relativas entre dos circunferencias': 'Posiciones Relativas entre Circunferencias',
        'relaciones metricas en la circunferencia': 'Relaciones Metricas en la Circunferencia',
        'areas sombreadas': 'Areas de Regiones',
        'areas de regiones': 'Areas de Regiones',
        'areas de regiones triangulares': 'Areas de Regiones Triangulares',
        'areas de regiones cuadrangulares': 'Areas de Regiones Cuadrangulares',
        'areas de regiones circulares': 'Areas de Regiones Circulares',
        'perimetros': 'Perimetros',
    },
    'Trigonometria': {
        'sistemas de medidas angulares': 'Sistemas de Medidas Angulares',
        'angulo trigonometrico': 'Angulo Trigonometrico',
        'angulos en posicion normal': 'Angulos en Posicion Normal',
        'reduccion al primer cuadrante': 'Reduccion al Primer Cuadrante',
        'razones trigonometricas de angulos agudos': 'Razones Trigonometricas de Angulos Agudos',
        'circunferencia trigonometrica': 'Circunferencia Trigonometrica',
        'funciones trigonometricas': 'Funciones Trigonometricas',
        'funciones trigonometricas inversas': 'Funciones Trigonometricas Inversas',
        'identidades trigonometricas': 'Identidades Trigonometricas',
        'arco compuesto': 'Arco Compuesto',
        'arcos multiples': 'Arcos Multiples',
        'transformaciones trigonometricas': 'Transformaciones Trigonometricas',
        'ecuaciones trigonometricas': 'Ecuaciones Trigonometricas',
        'inecuaciones trigonometricas': 'Inecuaciones Trigonometricas',
        'resolucion de triangulos oblicuangulos y cuadrilateros': 'Resolucion de Triangulos Oblicuangulos y Cuadrilateros',
        'longitud de arco': 'Longitud de Arco',
        'sector circular': 'Sector Circular',
        'angulos verticales y horizontales': 'Angulos Verticales y Horizontales',
        'rosa nautica': 'Rosa Nautica',
        'areas de triangulos': 'Areas de Triangulos',
    },
    'Geometria Analitica': {
        'plano cartesiano': 'Plano Cartesiano',
        'recta': 'Recta',
        'circunferencia': 'Circunferencia',
        'parabola': 'Parabola',
        'elipse': 'Elipse',
        'hiperbola': 'Hiperbola',
        'lugares geometricos': 'Lugares Geometricos',
    },
    'Geometria del Espacio': {
        'rectas y planos': 'Rectas y Planos',
        'rectas y plano': 'Rectas y Planos',
        'angulos diedros': 'Angulos Diedros',
        'angulos triedros': 'Angulos Triedros',
        'poliedros': 'Poliedros',
        'poliedros regulares': 'Poliedros Regulares',
        'prismas': 'Prismas',
        'piramide': 'Piramide',
        'cilindro': 'Cilindro',
        'cono': 'Cono',
        'esfera': 'Esfera',
        'esfera y pappus': 'Esfera y Pappus',
    },
    'Aritmetica': {
        'numeracion': 'Numeracion',
        'cuatro operaciones': 'Cuatro Operaciones',
        'divisibilidad': 'Divisibilidad',
        'numeros primos': 'Numeros Primos',
        'mcd y mcm': 'MCD y MCM',
        'fracciones': 'Fracciones',
        'decimales': 'Decimales',
        'razones y proporciones': 'Razones y Proporciones',
        'regla de tres': 'Regla de Tres',
        'porcentajes': 'Porcentajes',
        'promedios': 'Promedios',
        'mezclas': 'Mezclas',
        'edades': 'Edades',
        'moviles': 'Moviles',
        'cronometria': 'Cronometria',
        'sucesiones numericas': 'Sucesiones Numericas',
        'conteo': 'Conteo',
        'probabilidades': 'Probabilidades',
    },
}

WEEK_GEOMETRY = {
    'semana_1_tri_ngulos': 'Triangulos',
    'semana_2_l_neas_notables_asociadas_al_tri_ngulo': 'Lineas Notables',
    'semana_3_congruencia_de_tri_ngulos': 'Congruencia de Triangulos',
    'semana_4_aplicaciones_de_congruencia': 'Aplicaciones de Congruencia',
    'semana_5_cuadril_teros_1': 'Cuadrilateros',
    'semana_6_cuadril_teros_2': 'Cuadrilateros',
    'semana_7_pol_gonos': 'Poligonos',
    'semana_8_circunferencia': 'Circunferencias',
    'semana_9_circunferencia_2': 'Circunferencias',
    'semana_10_proporcionalidad_de_segmentos': 'Proporcionalidad de Segmentos',
    'semana_11_semejanza_de_tri_ngulos': 'Semejanza de Triangulos',
    'semana_12_puntos_notables': 'Lineas Notables',
    'semana_13_relaciones_m_tricas': 'Relaciones Metricas',
    'semana_14_relaciones_m_tricas_entre_ngulos_y_pol_gonos': 'Relaciones Metricas',
    'semana_15_pol_gonos_regulares': 'Poligonos Regulares',
    'semana_16_reas_de_regiones_triangulares': 'Areas de Regiones Triangulares',
    'semana_17_relaciones_de_reas': 'Areas de Regiones',
    'semana_18_reas_de_regiones_cuadrangulares': 'Areas de Regiones Cuadrangulares',
    'semana_19_reas_de_regiones_circulares': 'Areas de Regiones Circulares',
}


def normalize_key(value: object) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    text = text.replace('�', '')
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r'\s+', ' ', text)
    return text.casefold()


def canonical_course(value: object) -> str | None:
    key = normalize_key(value)
    if 'lgebra' in key:
        return 'Algebra'
    return COURSE_MAP.get(key)


def canonical_topic(course: str, topic: object) -> str | None:
    key = normalize_key(topic)
    if not key or key == 'sin_tema' or key == 'none':
        return None
    if course == 'Geometria' and key in WEEK_GEOMETRY:
        return WEEK_GEOMETRY[key]
    if course == 'Geometria' and key == 'semana_2':
        return None
    return TOPICS.get(course, {}).get(key)


def ensure_catalog(cur):
    cur.execute('''
        CREATE TABLE IF NOT EXISTS temas (
            id SERIAL PRIMARY KEY,
            nombre TEXT NOT NULL,
            area TEXT NOT NULL DEFAULT '',
            UNIQUE (area, nombre)
        );
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS subtemas (
            id SERIAL PRIMARY KEY,
            tema_id INT NOT NULL REFERENCES temas(id) ON DELETE CASCADE,
            nombre TEXT NOT NULL,
            UNIQUE (tema_id, nombre)
        );
    ''')
    for course, topics in TOPICS.items():
        for topic in sorted(set(topics.values())):
            cur.execute(
                'INSERT INTO temas (nombre, area) VALUES (%s, %s) ON CONFLICT (area, nombre) DO NOTHING',
                (topic, course),
            )
    cur.execute('SELECT id, area, nombre FROM temas')
    return {(area, nombre): tid for tid, area, nombre in cur.fetchall()}


def main(apply: bool = True):
    db = DatabaseManager.from_profile('local_mirror')
    conn = db.get_connection(db.db_name)
    try:
        cur = conn.cursor()
        cur.execute('''
            SELECT id, curso, tema, subtema, tema_id, subtema_id
            FROM problemas
            ORDER BY id
        ''')
        rows = cur.fetchall()
        with BACKUP.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['id','curso','tema','subtema','tema_id','subtema_id'])
            w.writerows(rows)

        topic_ids = ensure_catalog(cur)
        report_rows = []
        unmapped_rows = []
        updates = []
        for pid, curso, tema, subtema, tema_id, subtema_id in rows:
            target_course = canonical_course(curso)
            if not target_course:
                unmapped_rows.append([pid, curso, tema, 'curso_no_objetivo_o_no_mapeado'])
                continue
            target_topic = canonical_topic(target_course, tema)
            if not target_topic:
                unmapped_rows.append([pid, curso, tema, 'tema_no_mapeado'])
                continue
            target_topic_id = topic_ids.get((target_course, target_topic))
            if not target_topic_id:
                unmapped_rows.append([pid, curso, tema, 'tema_sin_id_catalogo'])
                continue
            changed = (str(curso or '').strip() != target_course) or (str(tema or '').strip() != target_topic) or (tema_id != target_topic_id)
            report_rows.append([pid, curso, tema, target_course, target_topic, target_topic_id, changed])
            if changed:
                updates.append((target_course, target_topic, target_topic_id, pid))

        with REPORT.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['id','curso_actual','tema_actual','curso_canonico','tema_canonico','tema_id','cambia'])
            w.writerows(report_rows)
        with UNMAPPED.open('w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['id','curso_actual','tema_actual','motivo'])
            w.writerows(unmapped_rows)

        if apply:
            cur.executemany('''
                UPDATE problemas
                SET curso = %s,
                    tema = %s,
                    tema_id = %s
                WHERE id = %s
            ''', updates)
            conn.commit()
        else:
            conn.rollback()
        print('backup', BACKUP)
        print('report', REPORT)
        print('unmapped', UNMAPPED)
        print('rows', len(rows), 'mapped', len(report_rows), 'updates', len(updates), 'unmapped', len(unmapped_rows), 'apply', apply)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    main(apply=True)
