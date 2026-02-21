PROMPT_ESCANEO_LITERAL = r"""Extrae TODOS los problemas visibles en la imagen actual.

SALIDA: texto plano estructurado.

Si la imagen inicia con continuacion del problema anterior (sin encabezado nuevo), usa este bloque opcional:
LEADING_CONTINUATION: <una sola linea o VACIO>
LEADING_OPTIONS:
A) <texto o ...>
B) <texto o ...>
C) <texto o ...>
D) <texto o ...>
E) <texto o ...>
ENDLEADING

Despues, para cada problema detectado en ESTA imagen, usa EXACTAMENTE:
ITEM: <n>
ENUNCIADO: <una sola linea>
OPCIONES:
A) <texto o ...>
B) <texto o ...>
C) <texto o ...>
D) <texto o ...>
E) <texto o ...>
ENDITEM

REGLAS CRITICAS:
1) ITEM:<n> SOLO se obtiene de encabezado impreso tipo "PROBLEMA ... <n>" (variantes: Problema, N, No, N°).
   PROHIBIDO inventar ITEM: 1, ITEM: 2, etc.
2) Si una imagen comienza con texto/opciones sin encabezado, eso es continuacion del problema anterior:
   - Enunciado faltante -> LEADING_CONTINUATION
   - Opciones A-E faltantes -> LEADING_OPTIONS
   - NO crear ITEM nuevo para ese prefijo.
3) Si aparece A)-E) antes del siguiente encabezado "PROBLEMA ...", esas opciones van en LEADING_OPTIONS.
4) No mezcles problemas: cada ITEM termina con ENDITEM.

REGLAS GENERALES:
- NO inventes contenido.
- PROHIBIDO comentarios (Nota, explicaciones, traducciones).
- Siempre deben existir 5 opciones A-E por ITEM (aunque sean "...").
- ENUNCIADO en una sola linea (une partes con espacios).
- Si hay texto superpuesto dentro del dibujo/figura, prioriza transcribir el enunciado y las alternativas impresas fuera del grafico.
- NO uses texto interno del diagrama para reemplazar o completar enunciado/opciones si no esta claramente impreso en esas secciones.
- Si el grafico esta pegado al enunciado, prioriza conservar todo el texto del enunciado aunque haya ruido visual.

ECUACIONES / NOTACION:
- Toda expresion con simbolos (=, >, <, +, -, /, \angle, \in, \parallel, raices, fracciones, potencias, grados)
  debe ir en un unico bloque $...$.
- No partir una misma expresion en varios $...$ ni intercalar $...$ dentro de palabras.
- Dentro de $...$ normaliza: ∠ -> \angle, θ -> \theta, ° -> ^\circ.
"""


PROMPT_VISION_DIRECT_SCAN_V1 = r"""Transcribe TODOS los problemas visibles de la imagen y devuelve SOLO items en formato scan final.

SALIDA:
- SOLO lineas \item, sin comentarios, sin markdown, sin JSON.
- Si hay varios problemas, devuelve un \item por problema (una linea por \item).

FORMATO OBLIGATORIO POR ITEM:
\item[\textbf{n.}] <enunciado> [[Imagen=img-n]] £A) $...$æB) $...$æC) $...$£D) $...$ææE) $...$£
o (si NO hay imagen asociada):
\item[\textbf{n.}] <enunciado> £A) $...$æB) $...$æC) $...$£D) $...$ææE) $...$£

REGLAS ESTRICTAS:
1) No inventes contenido.
2) Conserva el numero original n cuando sea visible.
3) No uses ITEM:/ENDITEM ni FIGURA: SI/NO.
4) Opciones A-E siempre presentes; si falta alguna, usa exactamente $...$.
5) Cada opcion completa en un unico bloque $...$.
6) No devuelvas texto fuera de los \item.
7) Detecta si el problema tiene grafico/figura asociada en la pagina.
8) Si hay figura asociada al problema, inserta EXACTAMENTE [[Imagen=img-n]] antes de £A).
9) Si no hay figura asociada, NO insertes marcador de imagen.

MATEMATICA:
- Expresiones y ecuaciones en un unico bloque $...$.
- No partir expresiones: evita m$\angle$A o 2$(m\angle C).
- Dentro de $...$ normaliza:
  - simbolo de angulo -> \angle
  - theta -> \theta
  - grado -> ^\circ
  - \frac y \tfrac -> \dfrac

NOTA SOBRE IMAGEN:
- Prioriza evidencias visuales del problema (diagrama, grafico, figura geometrica, esquema).
- Si la figura corresponde claramente a otro problema cercano, no marques este item.
"""
