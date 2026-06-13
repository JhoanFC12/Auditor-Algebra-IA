from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Iterable

from .tokens import SEP_LINE


SYSTEM_PROMPT_EXTRACT = (
    "Eres un extractor estricto de problemas matematicos escaneados. "
    "Devuelves SOLO JSON valido. "
    "No inventas texto. "
    "El JSON debe ser legible para humanos. "
    "Usas palabras normales en espanol para texto comun. "
    "No usas macros de presentacion como \\text, \\textit, \\textbf o \\emph para texto normal. "
    "Conservas solo matematica basica cuando realmente sea una expresion matematica. "
    "No usas etiquetas, letras ni texto interno de diagramas o figuras como parte del enunciado u opciones. "
    "No mezclas alternativas dentro del enunciado; statement y options deben estar separados. "
    "Nunca agregas digitos fantasma a la numeracion visible. "
    "Nunca conviertes una continuidad en un item nuevo sin encabezado real. "
    "No cortas statement por una B) aislada dentro de parentesis algebraicos como (Ax + B). "
    "Conservas correctamente tildes y caracteres espanoles; si usas LaTeX, las secuencias deben ser validas y consistentes. "
    "Usas \\sphericalangle solo para notacion geometrica simbolica; no reemplazas la palabra espanola angulo en texto normal."
)


SYSTEM_PROMPT_RAW_OCR = (
    "Eres un transcriptor fiel de imagenes escaneadas de matematicas. "
    "Devuelves SOLO texto plano fiel. "
    "No resumes, no estructuras en JSON y no decides que contenido sirve o no sirve. "
    "No inventas ni completas por inferencia. "
    "No agregas etiquetas artificiales, marcadores de imagen, claves, respuestas, nombres de variables ni datos que no esten impresos como texto visible. "
    "Conservas el orden de lectura de arriba hacia abajo y, dentro de cada bloque, de izquierda a derecha. "
    "Mantienes numeracion, alternativas, formulas y texto inicial aunque parezca continuidad del problema anterior. "
    "Conservas el texto comun en espanol normal y usas LaTeX solo para expresiones matematicas visibles. "
    "No conviertes todo el enunciado a LaTeX ni reescribes el estilo matematico de la imagen. "
    "Si el numero de problema aparece dentro de un circulo, sello o adorno, lo normalizas exactamente como '<numero.>' al inicio de una linea. "
    "Si hay varias preguntas en una misma imagen, cada una debe iniciar en una linea nueva y separarse con una linea en blanco. "
    "Cada alternativa debe quedar en su propia linea como A), B), C), D), E). "
    "No escribes etiquetas artificiales como 'N.11' ni comentarios sobre el formato. "
    "Nunca agregas prefijos fantasmas a la numeracion visible: si el numero visible es 3, escribes <3.> y nunca <93.> o <103.>. "
    "Si una linea solo continua una formula o enunciado del mismo problema, no inventas un nuevo encabezado. "
    "Formato general cuando hay numero visible: <numero.> enunciado visible, luego alternativas visibles A), B), C), D), E), y solo si esta impresa una respuesta o clave, una linea Clave: <valor visible>. "
    "Cuando no hay numeracion visible, inicia el bloque con [CONT.] y transcribe el contenido como continuacion; no inventes numero. "
    "No describas figuras, diagramas ni graficos; no escribas frases como 'hay un triangulo', 'se observa' ni enumeres elementos del dibujo. "
    "No extraigas letras, medidas, relaciones ni etiquetas internas del dibujo como parte del enunciado u opciones; otro modelo se encarga de detectar graficos. "
    "Transcribe solo texto externo visible al dibujo. Si el crop contiene solo grafico sin texto externo legible, escribe [CONT.] [sin texto OCR visible]. "
    "Si una palabra, numero, signo o formula no se lee con seguridad, escribe [ilegible] o conserva el fragmento dudoso sin completarlo."
)

SYSTEM_PROMPT_GRAPHIC_CONTINUATION = (
    "Eres un detector visual estricto de continuaciones graficas en ejercicios matematicos. "
    "Devuelves SOLO JSON valido. "
    "No inventas enunciados ni opciones. "
    "No haces OCR completo; solo clasificas la imagen y reportas etiquetas visibles de opciones cuando existan. "
    "Si no estas seguro, responde conservadoramente."
)


RAW_OCR_REGRESSION_GUARDS = (
    "ERRORES QUE NO DEBES COMETER:\n"
    "- No inventes prefijos de numeracion. Si el visible es 3, escribe <3.>; nunca <93.>, <103.> o <108.>.\n"
    "- No abras un problema nuevo por una linea que solo continua una formula, fraccion o enunciado del problema anterior.\n"
    "- Si una continuacion empieza con x, \\frac, \\sqrt, 'entre', parentesis o un signo matematico, mantenla dentro del mismo problema hasta ver un encabezado nuevo y claro.\n"
    "- Si aparece algo como '<7.> ...' seguido por un bloque '<2.> x^3 + ... A)...', tratalo como continuidad del problema 7 salvo que el nuevo encabezado sea realmente visible y confiable.\n"
    "- Si la imagen empieza directamente con alternativas A), B), C), D) o E), transcribe TODAS esas alternativas visibles antes de cualquier problema nuevo; no las omitas aunque no aparezca el enunciado.\n"
    "- Si las alternativas aparecen en una sola linea, separalas igualmente como lineas A), B), C), D), E); nunca reemplaces sus valores por '...'.\n"
    "- No conviertas una lista inicial de alternativas en un problema nuevo; es continuacion del problema anterior hasta que aparezca un encabezado real como <n.> o PROBLEMA n.\n"
)


STRUCTURED_REGRESSION_GUARDS = (
    "REGLAS DE NUMERACION Y CONTINUIDAD:\n"
    "9) Nunca agregues digitos fantasma al numero visible. Si el visible es 3, usa n=3; nunca 93, 103 o 108.\n"
    "10) Si start_n es pequeno y aparece un numero improbable con prefijo espurio (por ejemplo 93 o 108) pero el sufijo coincide con la secuencia esperada, usa el numero pequeno plausible y marca needs_review=true.\n"
    "10.1) Si el OCR bruto trae encabezados visibles como <9.>, <10.>, <11.>, esos numeros visibles mandan sobre start_n; no renumeres a 50, 51, etc.\n"
    "10.2) Si el OCR bruto trae encabezados visibles grandes como <151.>, <181.> o <211.>, conserva esos numeros exactos; no los renumeres a 1, 2, 3 salvo que el usuario lo pida explicitamente.\n"
    "10.3) start_n es SOLO respaldo cuando el numero no es detectable; nunca debe reemplazar un numero visible confiable del OCR bruto.\n"
    "10.4) No repitas el mismo n en dos items del mismo JSON, salvo que el OCR muestre exactamente el mismo encabezado repetido; si ocurre por duda, conserva el visible y marca needs_review=true.\n"
    "11) No crees un item nuevo para un fragmento que solo continua el statement u opciones del item anterior.\n"
    "12) Si despues de un item aparece un bloque que empieza con 'entre', 'x', '\\frac', '\\sqrt', un parentesis o un signo matematico y completa la idea anterior, fusionalo con el item previo.\n"
    "13) Solo inicia item nuevo cuando veas un encabezado real y confiable del problema.\n"
    "13.1) Si el OCR bruto empieza con alternativas A), B), C), D) o E) antes del primer encabezado real, colocalas obligatoriamente en leading_options; no las pongas en items y no las cambies por '...'.\n"
    "13.2) Si el OCR bruto contiene solo opciones iniciales y ningun enunciado nuevo, items debe ser [] y leading_options debe conservar las opciones visibles.\n"
    "13.3) Si solo se ven algunas opciones iniciales, conserva esas letras en leading_options y deja ausentes las no visibles; no inventes opciones faltantes.\n"
    "13.4) Si el OCR bruto contiene '[CONT.] A)... B)...' antes de un nuevo encabezado <n.>, esas opciones pertenecen al problema anterior y deben ir SOLO en leading_options; el nuevo item <n.> empieza despues.\n"
    "13.5) Si un problema empieza en esta imagen pero sus alternativas no aparecen porque continuan en la siguiente, usa options con '...' y needs_review=true; no inventes valores.\n"
    "13.6) Si las alternativas reales aparecen en el OCR bruto, no las reemplaces por '...'. Copia sus valores visibles en options o leading_options segun corresponda.\n"
    "REGLAS PARA SEPARAR OPCIONES:\n"
    "14) No cortes statement por una B) aislada dentro de parentesis o algebra, por ejemplo '(Ax + B)'.\n"
    "15) Solo separa options cuando exista un bloque real de alternativas A), B), C) y normalmente D), E).\n"
    "16) Si el statement contiene '(Ax + B), calcule AB A) ...', conserva '(Ax + B), calcule AB' dentro de statement y empieza options recien en la A) real.\n"
    "17) Las letras a), b), c), d), e) dentro de expresiones, listas de variables, funciones o parentesis NO son opciones: por ejemplo (a+b), P(a,b,c,d,e), f(a,b), (x+B), C_{n}^{k}.\n"
    "18) Antes de separar alternativas, verifica que la primera opcion real sea A) y que despues aparezcan B), C), D) y E) como bloque de respuestas. Nunca empieces options en b), c), d) o e) si no hubo A) real antes.\n"
    "19) Todo lo que aparece antes de la A) real pertenece al statement, incluyendo formulas con comas, letras a,b,c,d,e, potencias, radicales, parentesis y dos puntos.\n"
    "EJEMPLOS DE REGRESION OBLIGATORIOS:\n"
    "- Si OCR bruto trae '<7.> ...\\n\\n<2.> x^3 + ... A)...', devuelve un solo item n=7; el bloque '<2.>' se trata como continuidad del 7, no como item 2.\n"
    "- Si OCR bruto trae '<1.> ...\\n\\n<93.> ...' y start_n=2, corrige el segundo item al numero pequeno plausible; no dejes 93.\n"
    "- Si OCR bruto trae '<9.> En la expansion de P(a,b,c,d,e)=(a+b+c+d+e)^5 ... A)106 B)107 ...', statement termina justo antes de A)106; no cortes en e).\n"
    "REGLAS DE NOTACION MATEMATICA QUE NO SE DEBE MUTAR:\n"
    "20) Preserva \\sqrt{...}; nunca lo conviertas en \\root{...}.\n"
    "21) Preserva grados como ^\\circ o como el texto visible equivalente; nunca conviertas grados en ^\\theta, ^\\bullet ni caracteres nulos.\n"
    "22) \\theta solo se usa cuando la variable griega theta aparece como variable del problema, no como reemplazo de grados.\n"
    "23) En geometria, para arcos usa siempre \\overset{\\frown}{AB}; nunca uses \\widehat, \\bar ni \\overline para representar arcos.\n"
    "24) Si OCR bruto contiene m\\overset{\\frown}{AB}, conserva exactamente m\\overset{\\frown}{AB}; no lo transformes a \\Delta, \\bar ni \\overline.\n"
    "25) Usa \\overline{AB} solo para segmento como objeto cuando asi aparezca o corresponda claramente; no lo uses para arcos de circunferencia.\n"
    "26) Usa \\sphericalangle ABC solo para angulos; no uses \\Delta para representar arcos ni medidas de arco.\n"
    "27) Si no estas seguro entre arco, segmento o angulo, conserva la notacion del OCR bruto y marca needs_review=true.\n"
)


FINAL_LATEX_FORMAT_SPEC = (
    "FORMATO FINAL CANONICO PARA golden base:\n"
    "- Cada problema debe iniciar exactamente con \\item[\\textbf{n.}].\n"
    "- Por defecto NO agregues etiquetas [[curso=...]], [[tema=...]], [[Estado=...]] ni [[Clave=...]].\n"
    "- Si el problema tiene figura real, coloca [[Imagen=img-n]] al final del enunciado, justo antes de las alternativas.\n"
    "- Si no tiene figura real, no coloques ninguna etiqueta de imagen.\n"
    "- La estructura de alternativas debe iniciar con £ y cerrar con £.\n"
    "- Usa el patron: £A)$...$æB)$...$æC)$...$£D)$...$ææE)$...$£.\n"
    "- No escribas la palabra Opciones.\n"
    "- Mantén el orden original de A), B), C), D), E).\n"
    "- Toda matematica debe ir dentro de $...$.\n"
    "- Las expresiones centrales deben ir como £$...$£.\n"
    "- Usa \\dfrac, no \\frac.\n"
    "- Usa \\operatorname{sen}, no \\sin.\n"
    "- No uses \\displaystyle.\n"
    "- Las unidades van dentro del modo matematico con espacio fino, por ejemplo $30\\,cm$.\n"
    "- En geometria usa $\\overline{AB}$ para el segmento como objeto y $AB$ para la medida.\n"
    "- En geometria usa $\\sphericalangle ABC$ para angulos, $m\\sphericalangle ABC$ para medidas de angulo y $\\Delta ABC$ para triangulos.\n"
    "- En geometria usa siempre $\\overset{\\frown}{AB}$ para arcos de circunferencia.\n"
    "- Si hay proposiciones I, II, III, IV, separalas con £.\n"
    "- No resuelvas, no resumas, no agregues datos y no cambies el orden de las alternativas.\n"
)


def _normalize_prompt_text(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def infer_prompt_profile(*, curso: str = "", tema: str = "", book_code: str = "", instance_type: str = "") -> str:
    haystack = " ".join(
        _normalize_prompt_text(v)
        for v in (curso, tema, book_code, instance_type)
        if str(v or "").strip()
    )
    if not haystack:
        return "general"
    if any(token in haystack for token in ("geometria", "circunferencia", "triangulo", "cuadrilatero", "poligono")):
        return "geometria"
    if any(token in haystack for token in ("trigonometria", "trigonometrica", "seno", "coseno", "tangente")):
        return "trigonometria"
    if any(token in haystack for token in ("algebra", "polinomio", "binomio", "factorizacion", "ecuacion")):
        return "algebra"
    if any(token in haystack for token in ("aritmetica", "divisibilidad", "mcd", "mcm", "porcentaje")):
        return "aritmetica"
    return "general"


def build_prompt_profile_instructions(
    *,
    curso: str = "",
    tema: str = "",
    book_code: str = "",
    instance_type: str = "",
    stage: str = "structured",
) -> str:
    profile = infer_prompt_profile(curso=curso, tema=tema, book_code=book_code, instance_type=instance_type)
    if str(stage or "").strip().lower() in {"ocr", "raw_ocr", "faithful_ocr"}:
        base = (
            "PERFIL AUTOMATICO ACTIVADO: OCR LITERAL.\n"
            "Reglas para esta etapa:\n"
            "- Transcribe solo texto visible. No normalices a formato final.\n"
            "- No agregues marcadores artificiales de imagen, figure_tag, claves, respuestas ni comentarios.\n"
            "- No uses reglas del curso para completar datos ausentes.\n"
            "- Si una fraccion visible aparece como fraccion vertical o apilada, transcribela con $\\dfrac{numerador}{denominador}$; si aparece lineal, conserva la forma lineal visible.\n"
            "- No describas figuras ni extraigas letras, medidas o relaciones internas del dibujo; transcribe solo texto externo visible.\n"
            "- Si algo no se lee con seguridad, marca [ilegible] en vez de inferir.\n"
        )
        if profile == "geometria":
            return (
                base
                + "Reglas de geometria SOLO para notacion visible:\n"
                "- Si el texto visible muestra angulo ABC o el simbolo de angulo con ABC, puedes transcribirlo como $\\sphericalangle ABC$.\n"
                "- Si el texto visible muestra medida de angulo ABC, puedes transcribirlo como $m\\sphericalangle ABC$.\n"
                "- Si el texto visible muestra triangulo ABC, puedes transcribirlo como $\\Delta ABC$.\n"
                "- Si el texto visible muestra segmento AB como objeto, puedes transcribirlo como $\\overline{AB}$; si muestra longitud AB, conserva $AB$.\n"
                "- Si el texto visible muestra arco AB, puedes transcribirlo como $\\overset{\\frown}{AB}$.\n"
                "- Si el texto visible muestra grados, usa $^\\circ$.\n"
                "- Estas reglas no autorizan completar datos: no agregues relaciones, medidas, letras o nombres que no esten impresos como texto visible.\n"
                "- No describas el dibujo ni uses informacion interna del dibujo para completar o ampliar el enunciado; si solo hay dibujo sin texto externo, usa [CONT.] [sin texto OCR visible].\n"
            )
        return base
    if profile == "geometria":
        return (
            "PERFIL AUTOMATICO ACTIVADO: GEOMETRIA.\n"
            "Reglas especiales para problemas de geometria:\n"
            "- Si el texto dice 'en el grafico', 'en la figura', 'segun la figura', 'del grafico' o 'de la figura', considera que el problema tiene figura asociada.\n"
            "- En JSON, si hay figura asociada usa has_figure=true y figure_tag='img-n'; si no hay figura real, no coloques etiqueta de imagen.\n"
            "- Si el enunciado dice 'En el grafico', 'Segun el grafico', 'Del grafico', 'A partir del grafico' o equivalente, has_figure debe ser true aunque las opciones no sean visibles en esa imagen.\n"
            "- No copies letras internas del dibujo como A, B, C, O, P, Q, R dentro del enunciado salvo que tambien aparezcan claramente en el texto externo.\n"
            "- Coloca toda expresion matematica entre $...$, incluyendo igualdades, operaciones, variables, grados y relaciones geometricas.\n"
            "- Coloca cada elemento geometrico mencionado en el texto entre $...$: puntos como $A$, medidas como $AB$, segmentos como $\\overline{AB}$ y triangulos como $\\Delta ABC$.\n"
            "- Distingue objeto geometrico y medida: usa $\\overline{AB}$ para segmento como objeto y $AB$ para longitud.\n"
            "- Para angulos usa siempre $\\sphericalangle ABC$; no uses \\angle.\n"
            "- Para medidas de angulo usa siempre $m\\sphericalangle ABC=40^\\circ$; no uses m\\angle ni \\angle.\n"
            "- Para triangulos usa siempre $\\Delta ABC$; no uses \\triangle.\n"
            "- Para arcos de circunferencia usa siempre $\\overset{\\frown}{AB}$; no uses \\widehat y no lo confundas con segmento $\\overline{AB}$.\n"
            "- Si el OCR bruto contiene $m\\overset{\\frown}{AB}$, $m\\overset{\\frown}{ABC}$ o similar, conserva exactamente \\overset{\\frown}{...}; prohibido cambiarlo por \\Delta, \\bar u \\overline.\n"
            "- Nunca uses \\Delta para representar medida de arco. \\Delta solo representa triangulo.\n"
            "- Nunca uses ^\\theta ni ^\\bullet para grados; usa siempre ^\\circ. La letra theta se conserva solo si aparece como variable $\\theta$.\n"
            "- Conserva \\sqrt{...}; no uses \\root{...}.\n"
            "- Usa siempre \\dfrac para fracciones; no uses \\frac ni \\displaystyle.\n"
            "- Conserva las unidades dentro del mismo modo matematico con espacio fino, por ejemplo $AB=10\\,cm$, $A=25\\,m^2$ y $x=30^\\circ$.\n"
            "- Conserva relaciones completas dentro de un solo $...$: $AB\\parallel CD$, $AB\\perp CD$, $AB=BC$, $AB\\cong CD$, $\\Delta ABC\\cong \\Delta DEF$ y $\\Delta ABC\\sim \\Delta DEF$.\n"
            "- Conserva condiciones ligadas dentro de un solo $...$, por ejemplo $x>0;\\ x\\in\\mathbb{R}$.\n"
            "- Si una expresion matematica central ocupa su propio bloque, usa el formato £$...$£.\n"
            "- Usa \\operatorname{sen}, no \\sin. Para inversas usa \\operatorname{arcsen}, \\operatorname{arcsec}, \\operatorname{arccsc} y \\operatorname{arccot}.\n"
            "- En circunferencia, conserva radio, diametro, cuerda, secante, tangente, arco, angulo inscrito, angulo central y semicircunferencia.\n"
            "- Si una alternativa es solo un numero con grados, escribela como $40^\\circ$; si es longitud, conserva la unidad si aparece.\n"
            "- No uses informacion interna de la figura para completar datos faltantes; marca needs_review=true si el enunciado depende de un grafico ambiguo.\n"
        )
    if profile == "trigonometria":
        return (
            "PERFIL AUTOMATICO ACTIVADO: TRIGONOMETRIA.\n"
            "Reglas especiales: usa \\operatorname{sen} para seno, conserva \\cos, \\tan, \\cot, \\sec y \\csc, y no confundas grados con radianes.\n"
        )
    if profile == "algebra":
        return (
            "PERFIL AUTOMATICO ACTIVADO: ALGEBRA.\n"
            "Reglas especiales: no confundas letras dentro de polinomios o expresiones como P(a,b,c,d,e), (Ax+B) o C_n^k con alternativas.\n"
        )
    if profile == "aritmetica":
        return (
            "PERFIL AUTOMATICO ACTIVADO: ARITMETICA.\n"
            "Reglas especiales: conserva razones, porcentajes, divisibilidad, MCD/MCM y unidades exactamente como aparezcan.\n"
        )
    return "PERFIL AUTOMATICO ACTIVADO: GENERAL.\n"


def build_faithful_ocr_prompt(
    *,
    curso: str = "",
    tema: str = "",
    book_code: str = "",
    instance_type: str = "",
) -> str:
    return (
        "Transcribe TODO el texto visible de la imagen en orden de lectura.\n"
        "Devuelve SOLO texto plano fiel; no JSON, no markdown, no explicaciones y no eco del prompt.\n"
        "No agregues nada que no este escrito en la imagen como texto visible.\n"
        "No agregues marcadores artificiales de imagen, figure_tag, 'con grafico', claves, respuestas ni comentarios.\n"
        "No omitas el texto del inicio aunque parezca continuacion del problema anterior.\n"
        "No estructures todavia en items, ni enunciado, ni opciones.\n"
        "No decidas que bloques sobran.\n"
        "Si un problema tiene numero visible, aunque este dentro de un circulo, sello o adorno, escribelo exactamente como '<numero.>' al inicio de una linea nueva.\n"
        "Formato general por problema: <numero.> enunciado visible; luego alternativas visibles A), B), C), D), E); y solo si esta impresa una respuesta o clave, una linea Clave: <valor visible>.\n"
        "Si no hay numero de problema visible, inicia el bloque con [CONT.] y transcribe lo visible como continuacion; no inventes numeracion.\n"
        "Si aparece encabezado tipo 'PROBLEMA N° 12', 'PROBLEMA Nº 12' o 'PREGUNTA N° 12', normalizalo como '<12.>' al inicio de linea.\n"
        "Si la imagen contiene varios problemas, cada problema debe empezar en su propia linea con su numero y debes dejar una linea en blanco entre problemas.\n"
        "Escribe cada alternativa en su propia linea y normalizala como A), B), C), D), E), aunque en la imagen aparezca a), b), c), d), e).\n"
        "Si la imagen empieza con alternativas sueltas A), B), C), D) o E), transcribelas primero y completas; son continuidad del problema anterior.\n"
        "Si una clave o respuesta no esta impresa, no escribas Clave ni respuesta.\n"
        "Si varias alternativas estan en una misma linea, separalas en lineas individuales sin perder sus valores.\n"
        "Conserva el texto comun en espanol normal.\n"
        "Usa LaTeX SOLO para expresiones matematicas visibles: potencias, subindices, radicales, fracciones, parentesis matematicos, letras griegas y simbolos cuando realmente aparezcan.\n"
        "No conviertas todo el enunciado a LaTeX.\n"
        "No uses macros de presentacion como \\text, \\textit, \\textbf, \\left o \\right salvo que sean indispensables para una expresion visible.\n"
        "Si una fraccion visible aparece como fraccion vertical o apilada, usa \\dfrac{numerador}{denominador}; si se ve lineal, conserva la forma lineal visible y no fuerces fraccion.\n"
        "Conserva formulas, signos, parentesis, exponentes, radicales, llaves y fracciones tal como se ven. No resuelvas, no simplifiques y no corrijas matematicamente.\n"
        "No describas ni reconstruyas figuras; el modelo de segmentacion de graficos las procesa aparte.\n"
        "No copies letras, medidas ni relaciones internas de una figura dentro del enunciado; transcribe solo texto externo al dibujo.\n"
        "Si el crop contiene solo un grafico/diagrama sin texto externo legible, escribe [CONT.] [sin texto OCR visible].\n"
        "Si una palabra, numero, signo o formula no se lee con seguridad, escribe [ilegible] o conserva el fragmento dudoso sin completarlo.\n"
        "No escribas etiquetas artificiales como 'N.11', 'Problema 11' ni comentarios sobre el formato.\n"
        f"{RAW_OCR_REGRESSION_GUARDS}"
        f"{build_prompt_profile_instructions(curso=curso, tema=tema, book_code=book_code, instance_type=instance_type, stage='ocr')}"
        "Salida final: SOLO la transcripcion fiel completa."
    )


def build_faithful_ocr_prompt_compact(
    *,
    curso: str = "",
    tema: str = "",
    book_code: str = "",
    instance_type: str = "",
) -> str:
    return (
        "Transcribe todo el texto visible en orden de lectura.\n"
        "Devuelve solo texto plano fiel.\n"
        "No agregues nada que no este escrito en la imagen como texto visible.\n"
        "No agregues marcadores artificiales de imagen, figure_tag, claves, respuestas ni comentarios.\n"
        "Conserva el texto comun en espanol normal y usa LaTeX solo para expresiones matematicas visibles.\n"
        "No conviertas todo el enunciado a LaTeX ni fuerces \\frac, \\left o \\right si no hacen falta.\n"
        "Si aparece un numero de problema visible, escribelo exactamente como '<numero.>' al inicio de una linea.\n"
        "Formato general: <numero.> enunciado visible; alternativas visibles A)-E); y Clave: <valor visible> solo si la clave esta impresa.\n"
        "Si no hay numero visible, inicia con [CONT.] y no inventes numeracion.\n"
        "Si hay varios problemas, separalos con una linea en blanco.\n"
        "Escribe cada alternativa en su propia linea como A), B), C), D), E).\n"
        "Si la imagen empieza con alternativas sueltas, transcribelas completas antes de cualquier encabezado nuevo.\n"
        "No inventes encabezados nuevos ni prefijos 93/108 cuando el visible es un numero pequeno.\n"
        "Si una linea solo continua el problema actual, no la conviertas en item nuevo.\n"
        "No describas figuras ni copies letras, medidas o relaciones internas del dibujo; transcribe solo texto externo visible.\n"
        "Si el crop contiene solo grafico sin texto externo legible, escribe [CONT.] [sin texto OCR visible].\n"
        "Si algo no se lee con seguridad, marca [ilegible] en vez de inferir.\n"
        "No JSON, no markdown, no explicaciones, no eco del prompt.\n"
        "No inventes, no resuelvas, no simplifiques.\n"
        "Salida final: solo la transcripcion completa."
    )


def build_extract_prompt(
    *,
    curso: str,
    tema: str,
    start_n: int,
    book_code: str = "",
    instance_type: str = "",
) -> str:
    return (
        "Extrae EXACTAMENTE los problemas matematicos de la imagen y devuelve SOLO JSON valido.\n"
        "Prohibido responder markdown o texto fuera del JSON.\n"
        "No inventes, no completes por inferencia y no reconstruyas texto faltante con contenido del diagrama.\n"
        "No uses Unicode matematico crudo (ej: Â°, âˆ , â‰¤, Î¸); usa notacion matematica basica en LaTeX solo cuando haga falta.\n"
        "Esquema obligatorio:\n"
        "{\n"
        '  "leading_continuation": "<texto inicial que pertenece al problema anterior o \'\' >",\n'
        '  "leading_options": {"A":"<texto opcional>","B":"<texto opcional>","C":"<texto opcional>","D":"<texto opcional>","E":"<texto opcional>"} o {},\n'
        '  "items": [\n'
        "    {\n"
        '      "schema": "ScanItemJSON-v1",\n'
        '      "n": <int>,\n'
        '      "curso": "<texto>",\n'
        '      "tema": "<texto>",\n'
        '      "has_figure": <true|false>,\n'
        '      "figure_tag": "img-n" o "",\n'
        f'      "statement": "una sola linea; usa {SEP_LINE} para saltos internos; conserva texto humano legible y coloca cada expresion matematica visible entre $...$ cuando corresponda",\n'
        '      "options": {"A":"<texto con matematica entre $...$ cuando corresponda>","B":"<texto>","C":"<texto>","D":"<texto>","E":"<texto>"},\n'
        '      "answer_key": "A|B|C|D|E" o "",\n'
        '      "final_latex_candidate": "\\\\item[\\\\textbf{n.}] ...",\n'
        '      "needs_review": <true|false>\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Reglas:\n"
        "1) NO inventes texto.\n"
        "1.1) Si la imagen contiene 2, 3 o mas problemas, devuelve cada problema en items como objetos separados.\n"
        "1.2) Cada item debe conservar su numero visible original.\n"
        "2) options siempre tiene A-E; si falta una opcion usa '...'.\n"
        "2.1) Si aparece una clave/respuesta correcta visible para el problema, colocala en answer_key. Si no aparece, usa ''.\n"
        "2.2) No inventes answer_key; solo usa A, B, C, D o E cuando sea visible en OCR o solucionario.\n"
        "3) has_figure=true solo si hay figura/diagrama asociado al enunciado.\n"
        "4) Si NO hay figura, has_figure=false y figure_tag=''.\n"
        "5) Si hay figura, figure_tag='img-n'.\n"
        "5.0) El campo final_latex_candidate debe ser el problema completo en el formato final canonico, usando figure_tag solo cuando has_figure=true.\n"
        f"{FINAL_LATEX_FORMAT_SPEC}"
        "5.1) Si la imagen empieza con texto u opciones que pertenecen al problema anterior, NO crees un item nuevo para esa continuidad.\n"
        "5.2) Ese contenido inicial debe ir en leading_continuation y/o leading_options.\n"
        "5.3) items debe incluir SOLO los problemas que realmente empiezan en esta imagen.\n"
        "5.4) Si no hay continuidad inicial, usa leading_continuation='' y leading_options={}.\n"
        "6) Si hay duda, ilegible, o conflicto entre texto del problema y texto del diagrama, excluye lo dudoso, no inventes y usa '' o '...' segun corresponda; marca needs_review=true.\n"
        "6.1) Usa \\sphericalangle SOLO para notacion geometrica simbolica (por ejemplo \\sphericalangle ABC o m\\sphericalangle ABC); para la palabra natural conserva 'angulo'. Usa ^\\circ para grados (no caracteres Unicode).\n"
        "FUENTE DE TEXTO VALIDA:\n"
        "6.2) Usa SOLO texto impreso que pertenezca claramente al enunciado o a las alternativas.\n"
        "6.3) Prioriza bloques lineales de texto fuera de la figura o diagrama.\n"
        "6.4) PROHIBIDO usar letras, numeros, angulos, medidas o ecuaciones dibujadas dentro de la figura para completar statement u options.\n"
        "6.5) Las etiquetas internas del diagrama (vertices, angulos, marcas geometricas) NO forman parte del texto del problema.\n"
        "6.6) No uses medidas o ecuaciones visibles dentro del grafico salvo que esten claramente impresas como texto externo del enunciado u opciones.\n"
        "REGLA DE CONFLICTO:\n"
        "6.7) Si dudas si un fragmento pertenece al diagrama o al enunciado, EXCLUYELO.\n"
        "6.8) Si excluyes texto por duda visual, conserva solo lo seguro y marca needs_review=true.\n"
        "POLITICA DE FIGURA:\n"
        "6.9) has_figure=true solo describe que existe una figura asociada; NO autoriza a usar el texto interno del dibujo para completar statement u options.\n"
        "6.10) Nunca reconstruyas texto faltante usando anotaciones internas del grafico.\n"
        "SEPARACION ENTRE ENUNCIADO Y ALTERNATIVAS:\n"
        "6.11) statement debe contener SOLO el enunciado, sin incluir A), B), C), D) o E).\n"
        "6.12) PROHIBIDO copiar, repetir o mezclar alternativas dentro de statement.\n"
        "6.13) Las alternativas deben ir SOLO dentro de options.\n"
        "6.14) Si detectas opciones A-E en el texto del problema, separalas y colocalas unicamente en options.\n"
        "6.15) Nunca repitas las opciones dentro de statement.\n"
        "6.16) Si no puedes separar con certeza, limpia lo evidente, conserva options como fuente principal y marca needs_review=true.\n"
        f"{STRUCTURED_REGRESSION_GUARDS}"
        f"{build_prompt_profile_instructions(curso=curso, tema=tema, book_code=book_code, instance_type=instance_type, stage='structured')}"
        "FORMATO DEL JSON VISIBLE:\n"
        "6.17) statement y options deben ser legibles para humanos.\n"
        "6.18) Conserva expresiones matematicas claras en notacion matematica basica y deja cada expresion matematica visible entre delimitadores $...$ dentro de statement u options.\n"
        "6.19) NO uses macros de presentacion como \\text{...}, \\textit{...}, \\textbf{...} o \\emph{...} para palabras comunes.\n"
        "6.20) Si una unidad acompana una expresion matematica (por ejemplo rad, cm o m^2), conservala dentro del mismo $...$ con espacio fino cuando corresponda; no uses \\text{...}.\n"
        "TEXTO EN ESPANOL:\n"
        "6.21) Conserva correctamente tildes y caracteres espanoles.\n"
        "6.22) Si usas secuencias LaTeX para acentos, deben ser validas, consistentes y no doble-escapadas. No latexices innecesariamente texto normal.\n"
        f"7) Usa curso='{curso}' y tema='{tema}' por defecto si no aparecen en imagen.\n"
        f"8) Si n no es detectable, usa secuencia desde {max(1, int(start_n))}.\n"
        "Salida final: SOLO JSON valido."
    )


def build_structure_prompt(
    *,
    raw_ocr_text: str,
    curso: str,
    tema: str,
    start_n: int,
    book_code: str = "",
    instance_type: str = "",
) -> str:
    raw_block = str(raw_ocr_text or "").strip()
    raw_section = ""
    if raw_block:
        raw_section = (
            "OCR bruto fiel previo de la misma imagen:\n"
            "<<<OCR_BRUTO_INICIO>>>\n"
            f"{raw_block[:12000]}\n"
            "<<<OCR_BRUTO_FIN>>>\n"
        )
    return (
        "Usa la imagen como fuente principal y el OCR bruto fiel previo como inventario obligatorio del contenido visible.\n"
        "No omitas el bloque superior aunque parezca continuidad del problema anterior.\n"
        "Ese bloque inicial debe resolverse en leading_continuation y/o leading_options cuando corresponda.\n"
        "Si el bloque superior empieza con A), B), C), D) o E), esas alternativas deben ir en leading_options con sus valores reales visibles, no como '...'.\n"
        "Si el bloque superior trae solo alternativas y despues aparece un problema nuevo, leading_options conserva las alternativas y items contiene solo el problema nuevo.\n"
        "Si OCR bruto y numeracion esperada entran en conflicto, privilegia la secuencia visible y evita saltos absurdos como 93 o 108 cuando el bloque realmente corresponde a 3 u 8.\n"
        "Si el OCR bruto muestra encabezados visibles como <9.> o <10.>, conserva esos numeros en n aunque start_n sea distinto.\n"
        "Al separar statement/options, usa el OCR bruto como frontera: todo antes de la primera A) real del bloque de respuestas queda en statement.\n"
        "No interpretes a), b), c), d), e) minusculas dentro de formulas, parentesis o listas de variables como alternativas.\n"
        f"{raw_section}"
        f"{build_extract_prompt(curso=curso, tema=tema, start_n=start_n, book_code=book_code, instance_type=instance_type)}"
    )


def build_graphic_continuation_prompt() -> str:
    return (
        "Analiza la imagen solo para detectar continuidad grafica de problemas matematicos.\n"
        "Devuelve SOLO JSON valido con este esquema exacto:\n"
        "{\n"
        '  "has_figure": true,\n'
        '  "starts_new_numbered_item": false,\n'
        '  "numbered_item_labels": [],\n'
        '  "contains_option_graphs": false,\n'
        '  "option_labels_visible": [],\n'
        '  "figure_scope": "none",\n'
        '  "usable_text_outside_graph": "",\n'
        '  "notes": ""\n'
        "}\n"
        "Reglas:\n"
        "1) No inventes enunciados ni opciones.\n"
        "2) Si la imagen contiene graficas que corresponden a alternativas A-E, usa contains_option_graphs=true.\n"
        "3) option_labels_visible debe listar solo letras A-E visibles asociadas a esas opciones, sin parentesis.\n"
        "4) starts_new_numbered_item=true solo si ves explicitamente un numero de problema.\n"
        "5) numbered_item_labels debe listar esos numeros visibles tal como aparecen.\n"
        "6) usable_text_outside_graph debe contener solo texto impreso util fuera del grafico; si no hay, dejalo vacio.\n"
        "7) figure_scope debe ser uno de: statement, options_only, statement_and_options, none.\n"
        "8) Si la imagen es solo continuidad grafica de opciones, usa starts_new_numbered_item=false, contains_option_graphs=true y figure_scope=options_only.\n"
        "Salida final: SOLO JSON valido."
    )


def _safe_json(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def build_correction_prompt(
    *,
    bad_item: Any,
    errors: Iterable[str],
    curso: str,
    tema: str,
) -> str:
    joined = "\n".join(f"- {e}" for e in errors) or "- formato_invalido"
    return (
        "Corrige el siguiente item para que cumpla ScanItemJSON-v1.\n"
        "Devuelve SOLO JSON del item (no arreglo, no texto extra).\n"
        "Manten el resultado legible para humanos; usa solo matematica basica en LaTeX cuando haga falta.\n"
        "No generes numeracion fantasma ni cortes falsos de opciones.\n"
        "Incluye o corrige final_latex_candidate usando el formato final canonico.\n"
        f"{FINAL_LATEX_FORMAT_SPEC}"
        f"{STRUCTURED_REGRESSION_GUARDS}"
        f"{build_prompt_profile_instructions(curso=curso, tema=tema, stage='structured')}"
        f"Curso por defecto: {curso}\n"
        f"Tema por defecto: {tema}\n"
        "Errores detectados:\n"
        f"{joined}\n"
        "Item actual:\n"
        f"{_safe_json(bad_item)}"
    )


def build_parse_retry_prompt(
    *,
    raw_output: str,
    errors: Iterable[str],
    curso: str,
    tema: str,
    start_n: int,
) -> str:
    joined = "\n".join(f"- {e}" for e in errors) or "- salida_no_json"
    return (
        "Tu salida estructurada previa no se pudo parsear como JSON valido.\n"
        "Corrige especificamente errores de numeracion fantasma, continuidades partidas y cortes falsos de opciones.\n"
        f"{build_structure_prompt(raw_ocr_text=raw_output, curso=curso, tema=tema, start_n=start_n)}\n"
        "Errores detectados:\n"
        f"{joined}\n"
    )
