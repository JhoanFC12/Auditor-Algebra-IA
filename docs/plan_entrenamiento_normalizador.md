# Plan De Entrenamiento Del Modelo Normalizador

Fecha: 2026-06-10

## Objetivo

Entrenar un modelo normalizador para problemas normales de libros e instancias, sin mezclar todavia el caso de examenes de admision.

El normalizador debe transformar:

```text
OCR crudo revisado + contexto de staging + segmentos graficos opcionales
```

en:

```text
JSON normalizado revisable en staging + item LaTeX final en el formato de la aplicacion
```

Nada de este flujo escribe directamente en `problemas`. Todo queda en staging y cada correccion humana se guarda como ejemplo de entrenamiento.

## Alcance V1

Incluido:

- Captura de nuevos escaneos desde Biblioteca/Fabrica antes de entrenar.
- Seleccion de libros e instancias concretas para construir ejemplos.
- Segmentacion de problemas desde paginas PDF.
- Revision de boxes y materializacion de crops.
- OCR crudo y segmentacion de graficos por crop.
- JSON base previo a normalizacion.
- Problemas normales de libros.
- OCR crudo en formato `<01.> ... A) ... B) ...`.
- Alternativas A-E.
- Clave cuando existe.
- Curso, tema y subtema si se conoce.
- Deteccion de si requiere grafico mediante staging/segmentacion.
- Etiqueta `[[Imagen=img-n]]` solo cuando corresponde.
- Continuaciones `[CONT.]` en orden de staging.
- Salida JSON estable.
- Render LaTeX para revision humana.

Fuera de V1:

- Examenes mixtos de admision.
- Insercion directa en base de datos final.
- Creacion automatica de temas definitivos en catalogos.
- Normalizacion sin revision humana.
- Vectorizacion, similitud y recomendacion personalizada.
- Estimacion automatica definitiva de dificultad.

## Fase Previa: Sacar Problemas De Nuevos Escaneos

Antes de entrenar el modelo normalizador, primero construiremos un conjunto limpio de problemas desde nuevos escaneos.

Flujo operativo:

```text
Biblioteca
-> elegir libro
-> elegir instancia
-> seleccionar paginas del PDF
-> detectar boxes de problemas
-> revisar/ajustar boxes
-> materializar crops en staging
-> aplicar OCR crudo
-> aplicar segmentacion de graficos
-> guardar JSON base para normalizacion
```

Esta fase produce los insumos del normalizador. Todavia no entrena el modelo y todavia no inserta en `problemas`.

Cada crop debe quedar con:

- imagen del problema;
- pagina y orden correcto;
- box fuente revisado;
- OCR crudo editable;
- OCR estructurado si existe;
- segmentos graficos revisables;
- estado de stale/invalidez si el box fuente cambia;
- metadata de libro e instancia.

El resultado de esta fase sera un JSON intermedio:

```json
{
  "schema_version": "normalizer_input_staging_v1",
  "record_id": "string",
  "crop_id": "string",
  "crop_path": "string",
  "raw_ocr": "<01.> texto OCR crudo...",
  "structured_ocr": {},
  "figure_segmentation": {
    "has_figure": true,
    "segments": []
  },
  "source": {
    "book_code": "string",
    "instance_type": "string",
    "page_number": 1,
    "problem_number": 1,
    "box_index": 1,
    "crop_name": "imagen.png"
  }
}
```

Ese JSON es la entrada real para la normalizacion. Despues, cuando el humano corrija el resultado normalizado, se formara el par de entrenamiento:

```text
normalizer_input_staging_v1 -> normalized_problem_staging_v1 revisado
```

Exportar el JSON base desde staging:

```powershell
python tools/prepare_normalizer_input_dataset.py `
  --staging-root .cache/transcriptor_runs/staging `
  --out-dir .cache/transcriptor_runs/datasets/normalizer_inputs_smoke
```

Archivos generados:

- `inputs.jsonl`
- `skipped.jsonl`
- `manifest.json`

## Base Acumulativa De Muestras Revisadas

Cada vez que se guarda una revision lista con `latex_rendered_item`, la Fabrica agrega o actualiza una muestra en la base acumulativa:

```text
.cache/transcriptor_runs/datasets/normalizer_training_bank
```

Estructura:

- `samples/<sample_id>.json`: muestra individual con OCR, metadata, segmentos, formato final y trazabilidad.
- `images/<sample_id>__main.png`: copia del crop principal.
- `images/<sample_id>__continuation_01.png`: copia de continuaciones asociadas cuando existan.
- `samples.jsonl`: indice listo para preparar dataset de entrenamiento.
- `manifest.json`: conteo, umbral y estado.

Reglas:

- Solo cuentan registros principales, no registros `[CONT.]` independientes.
- La muestra debe tener `normalized.latex_rendered_item`.
- Las continuaciones se guardan como parte del problema padre, no como muestras separadas.
- El umbral inicial es `200` muestras.
- Cuando `samples_total >= 200`, la UI muestra aviso para entrenar una primera version.

La ruta puede cambiarse con:

```text
NORMALIZER_TRAINING_BANK_ROOT
```

## Contrato De Entrada

Cada ejemplo de entrenamiento debe tener como minimo:

```json
{
  "schema_version": "normalizer_training_sample_v1",
  "record_id": "string",
  "crop_id": "string",
  "raw_ocr": "<01.> texto OCR crudo...",
  "structured_ocr": {},
  "source": {
    "book_code": "string",
    "instance_type": "string",
    "page_number": 1,
    "problem_number": 1,
    "crop_name": "imagen.png"
  },
  "figure_segmentation": {
    "has_figure": true,
    "segments": []
  },
  "target": {}
}
```

`raw_ocr` es la fuente principal y ahora es requisito para entrar al normalizador. `structured_ocr` queda como campo historico/opcional y se exporta vacio en el contrato nuevo para no depender del parser anterior. La otra evidencia obligatoria para problemas con graficos es `figure_segmentation`, que indica si se debe usar `[[Imagen=img-n]]` sin describir el grafico.

## Contrato De Salida

Staging guarda JSON valido para revision y trazabilidad:

```json
{
  "schema_version": "normalized_problem_staging_v1",
  "numero": "271",
  "curso": "Geometria",
  "tema": "Relaciones Metricas en el Triangulo Rectangulo",
  "subtema": "",
  "estado": "sin_revisar",
  "respuesta_correcta": "E",
  "enunciado_latex": "En el grafico, $AB=8$. Calcule $\\left(CD\\right)\\left(AH\\right)$.",
  "tiene_grafico": true,
  "figure_tag": "img-271",
  "alternativas": {
    "A": "$24$",
    "B": "$8$",
    "C": "$16$",
    "D": "$32$",
    "E": "$64$"
  },
  "continuacion": {
    "es_continuacion": false,
    "fusionar_con_anterior": false
  },
  "classification": {
    "curso_confidence": 0.0,
    "tema_confidence": 0.0,
    "requires_human_review": true,
    "candidate_new_topic": ""
  },
  "latex_rendered_item": "\\item[\\textbf{271.}] [[curso=Geometria]] [[tema=Relaciones Metricas en el Triangulo Rectangulo]] [[Estado=sin_revisar]] [[Clave=E]] En el grafico, $AB=8$. Calcule $\\left(CD\\right)\\left(AH\\right)$. [[Imagen=img-271]] £A)$24$æB)$8$æC)$16$£D)$32$ææE)$64$£"
}
```

Para el entrenamiento del normalizador por lote, el prompt canonico versionado esta en `docs/prompt_normalizador_formato_final_lote_v1.md`.

La salida supervisada principal del modo por lote no es JSON: es el bloque LaTeX final que luego se guarda dentro de `latex_rendered_item`.

Reglas:

- No inventar datos.
- No inventar clave.
- No inventar imagen.
- No describir graficos.
- Si falta un dato, dejarlo vacio o como `SIN_CURSO` / `SIN_TEMA` segun corresponda.
- Mantener `estado = sin_revisar` por defecto.
- Todo dato matematico debe ir entre `$...$`.
- Usar `\\sphericalangle` para angulos.
- Usar `\\wideparen{}` para arcos.
- Usar `\\dfrac` para fracciones.
- Usar `^\\circ` para grados.
- Mantener separadores de alternativas `£`, `æ`.
- Usar exactamente el patron `£A)...æB)...æC)...£D)...ææE)...£`.

## Manejo De `[CONT.]`

V1 debe reconocer continuaciones y la fusion final se hace siguiendo el orden visual del staging.

Si un OCR comienza con `[CONT.]`, la salida debe marcar:

```json
{
  "continuacion": {
    "es_continuacion": true,
    "fusionar_con_anterior": true
  }
}
```

El texto de la continuacion no debe convertirse en un problema nuevo. Al final debe quedar fusionado dentro del problema anterior, porque ese problema completo es el que se preparara para futura base de datos.

El registro padre conserva el item LaTeX final completo y puede guardar trazabilidad en `continuaciones_fusionadas`. El registro `[CONT.]` queda marcado como continuacion y no debe promocionarse como problema independiente.

## Preparacion Para La Capa Semantica Futura

El normalizador V1 no debe intentar resolver similitud, dificultad ni recomendacion. Su responsabilidad es dejar el problema limpio, trazable y revisable.

El contrato detallado de esa fase esta en `docs/plan_descriptor_semantico_recomendacion.md`.

Sin embargo, cada muestra revisada debe conservar suficiente evidencia para que despues podamos crear un descriptor semantico multimodal:

```text
normalizer_input_staging_v1
-> normalized_problem_staging_v1 revisado
-> problem_semantic_profile_v1 futuro
-> embedding / similitud / dificultad / recomendacion
```

Campos que deben conservarse para esa fase:

- `raw_ocr` revisado;
- `latex_rendered_item` final;
- curso, tema y subtema revisados;
- crop principal y continuaciones fusionadas;
- `figure_segmentation` y nombres canonicos de imagen;
- origen: libro, instancia, pagina, box y crop;
- errores/correcciones humanas relevantes.

Cuando exista el descriptor de graficos geometricos, se agregara como evidencia adicional, no como reemplazo del OCR:

```json
{
  "schema_version": "geometry_figure_description_v1",
  "source_record_id": "string",
  "figure_tag": "img-15",
  "construction_cdl": [],
  "condition_cdl": [],
  "evidence": []
}
```

La salida semantica posterior debera describir:

- conceptos matematicos;
- habilidades necesarias;
- objetos del problema;
- condiciones dadas;
- incognitas;
- senales de dificultad;
- texto limpio para embedding.

Esto permite que la BD local deje de ser solo un repositorio y se convierta en base para busqueda por proximidad, deteccion de problemas similares y practicas especializadas para el alumno.

## Dataset

Fuentes:

- Nuevos escaneos procesados por Biblioteca/Fabrica.
- Registros `normalizer_input_staging_v1` con OCR y segmentos revisables.
- `staging/review_outputs/*/training_examples.json`
- `staging/golden_contracts/*.json`
- Golden base `ocr_normalization_golden_live`
- Revisiones humanas hechas en el editor de normalizacion

Reglas de inclusion:

- Incluir solo ejemplos con `human_normalized`.
- Excluir registros con `source_stale = true`.
- Excluir registros con errores de caja no regenerada.
- Mantener ejemplos con graficos, pero guardar el grafico como metadata, no como descripcion textual.
- Separar train/dev/test por libro o instancia para evitar fuga de datos.

Tamanos guia:

- Smoke: 50 a 150 ejemplos.
- V0 util: 500 a 1000 ejemplos.
- V1 estable: 2000 a 5000 ejemplos revisados.
- V2 fuerte: 10000+ ejemplos con variedad de cursos.

## Modelo Candidato

Primer candidato text-only:

```text
Qwen/Qwen2.5-0.5B-Instruct + LoRA
```

Motivo:

- La tarea es texto a JSON/LaTeX, no vision.
- El OCR ya lo hace otro modelo.
- Es mucho mas barato que usar un VLM.
- Puede correr localmente con menos recursos que un modelo visual.
- Ya existe configuracion base en `config/hf_ocr_normalizer_job.json`.

Alternativas futuras:

- Modelo 1.5B/3B si el 0.5B falla en reglas LaTeX.
- Entrenamiento local QLoRA si la GPU lo permite.
- Inferencia local cuantizada para evitar endpoint.
- Servicio remoto corto solo para entrenamiento o lotes grandes.

## Fases

### Fase 0: Baseline Sin Modelo

Crear un normalizador determinista minimo que convierta OCR estructurado a JSON, aunque sea imperfecto.

Objetivo:

- Tener una referencia medible.
- Saber cuanto mejora realmente el modelo.

### Fase 1: Exportador De Dataset

Crear script:

```powershell
python tools/prepare_normalizer_dataset.py `
  --staging-root .cache/transcriptor_runs/staging `
  --out-dir .cache/transcriptor_runs/datasets/normalizer_v1_smoke `
  --max-samples 150
```

Salida esperada:

- `train.jsonl`
- `dev.jsonl`
- `test.jsonl`
- `manifest.json`

### Fase 2: Evaluador

Crear script:

```powershell
python tools/evaluate_normalizer_dataset.py `
  --dataset-dir .cache/transcriptor_runs/datasets/normalizer_v1_smoke `
  --predictions predictions.jsonl
```

Metricas:

- JSON valido.
- Numero correcto.
- Alternativas completas.
- Clave no inventada.
- Imagen no inventada.
- Curso/tema correctos.
- Reglas LaTeX.
- Distancia de edicion contra revision humana.
- Tasa de alucinacion.

### Fase 3: Smoke Training

Entrenar pequeno para validar contrato:

```powershell
python tools/train_normalizer_lora.py `
  --dataset-dir .cache/transcriptor_runs/datasets/normalizer_v1_smoke `
  --output-dir models/normalizer/qwen2_5_0_5b_lora_smoke `
  --epochs 1 `
  --max-train-samples 120 `
  --max-eval-samples 30
```

Criterio para continuar:

- JSON valido >= 95%.
- No inventa claves en test manual.
- Respeta alternativas A-E en la mayoria de casos.

### Fase 4: Entrenamiento V1

Con dataset mayor:

```powershell
python tools/prepare_normalizer_dataset.py `
  --staging-root .cache/transcriptor_runs/staging `
  --out-dir .cache/transcriptor_runs/datasets/normalizer_v1_full
```

```powershell
python tools/train_normalizer_lora.py `
  --dataset-dir .cache/transcriptor_runs/datasets/normalizer_v1_full `
  --output-dir models/normalizer/qwen2_5_0_5b_lora_v1 `
  --epochs 3
```

Criterio de aceptacion:

- JSON valido >= 99%.
- Numero correcto >= 98%.
- Alternativas A-E completas >= 95%.
- Clave inventada <= 1%.
- Imagen inventada <= 1%.
- Casos con `[CONT.]` marcados correctamente >= 90%.
- Revision humana promedio menor que el baseline.

### Fase 5: Integracion En Fabrica

Activar el normalizador como opcion:

```text
normalizador = local_passthrough | local_lora | hf_endpoint
```

La Fabrica debe:

- Preparar borrador normalizado.
- Mostrar render LaTeX.
- Permitir edicion humana.
- Guardar correccion como nuevo ejemplo.
- Nunca insertar directo en `problemas`.

## Politica De Seguridad De Datos

- El modelo solo propone.
- El humano confirma.
- El staging es la fuente de verdad antes de BD.
- Toda promocion futura requiere paso explicito.
- Los ejemplos de entrenamiento deben conservar origen, record_id y modelo usado.

## Ruta Recomendada

1. Congelar este contrato.
2. Elegir algunos libros e instancias normales para nuevos escaneos.
3. Segmentar paginas y revisar boxes.
4. Materializar crops en staging.
5. Ejecutar OCR crudo y segmentacion de graficos.
6. Guardar JSON base `normalizer_input_staging_v1`.
7. Corregir normalizacion humana para crear pares de entrenamiento.
8. Implementar exportador de dataset normalizador.
9. Implementar evaluador.
10. Ejecutar smoke con ejemplos ya revisados.
11. Corregir UI/guardado para que cada revision alimente el dataset.
12. Entrenar LoRA pequeno.
13. Comparar contra baseline.
14. Integrar solo si reduce trabajo humano real.

## Pendiente Para Mas Adelante

Cuando el flujo de problemas normales este estable, agregar:

- Examenes de admision mixtos.
- `exam_context`.
- Curso/tema por problema con candidatos nuevos.
- Origen estructurado en `origenes` / `problema_origen`.
- Etiqueta de origen al generar practicas.
