# Plan Golden Base Para Segmentacion De Graficos

Fecha: 2026-06-13

## Objetivo

Preparar una golden base acumulativa para mejorar el modelo que detecta graficos dentro de cada crop de problema.

Esta golden base sirve para entrenar o reentrenar el modelo de segmentacion grafica, especialmente en Geometria Plana, donde el grafico suele ser parte esencial del problema.

## Principio

Solo se considera dato de entrenamiento cuando hay correccion humana real.

La meta inicial es:

```text
200 imagenes corregidas
```

`reviewed` significa que el usuario confirmo la prediccion sin cambios. Sirve como auditoria, pero no entra al dataset de entrenamiento por defecto.

`corrected` significa que el usuario movio, agrego o elimino boxes. Esos registros si entran al dataset y cuentan hacia las 200 imagenes.

El flujo correcto es:

```text
Crop del problema
-> modelo detecta grafico
-> usuario corrige boxes del grafico si hace falta
-> se guarda manifest + imagen fuente + boxes finales
-> se exporta dataset YOLO solo con correcciones
-> se reentrena/evalua el modelo
```

No buscamos describir la topologia del grafico en esta fase. Aqui solo queremos mejorar el detector de cajas de graficos.

## Ruta Viva De Golden Base

Ruta por defecto:

```text
.cache/transcriptor_runs/datasets/segment_training_live
```

Variable opcional:

```text
SEGMENT_LIVE_GOLDEN_BASE
```

Variables de control:

```text
SEGMENT_LIVE_GOLDEN_DISABLE=1
SEGMENT_LIVE_GOLDEN_DEFER_INDEX=1
```

`SEGMENT_LIVE_GOLDEN_DISABLE=1` desactiva el mirror automatico.

`SEGMENT_LIVE_GOLDEN_DEFER_INDEX=1` permite guardar muchos registros y reconstruir indices despues, util para lotes grandes.

## Estructura Esperada

```text
segment_training_live/
  manifest.json
  source_records_all.jsonl
  source_records_positive.jsonl
  source_records_corrected.jsonl
  records_all.jsonl
  records/
    <record_id>.json
  source_images/
    <record_id>_<stem>.png
  segments/
    <record_id>/
      <record_id>_seg_01.png
```

Cada `records/<record_id>.json` usa:

```json
{
  "schema_version": "segment_training_live_source_v1",
  "record_id": "string",
  "source_path": "crop original",
  "source_image_rel": "source_images/...",
  "boxes_total": 1,
  "boxes_px": [[10, 20, 100, 120]],
  "segments": [],
  "detector_review": {
    "review_status": "corrected",
    "diagram_presence_label": "yes",
    "detector_source": "human_reviewed_segments",
    "predicted_boxes": [],
    "final_boxes": [
      {"bbox_px": [10, 20, 100, 120], "source": "human_review"}
    ]
  }
}
```

`manifest.json` mantiene contadores de avance:

```json
{
  "corrected_images": 37,
  "target_corrected_images": 200,
  "remaining_corrected_images": 163
}
```

## Cuando Se Guarda

La Fabrica web guarda segmentos revisados cuando el usuario usa el editor de segmentos graficos y pulsa guardar.

Ruta tecnica:

```text
POST /api/ocr/segments/boxes
-> InstancePdfPipelineService.update_figure_segments
-> SegmentadorProblemasV2.save_reviewed_segments
-> segments_manifest.json
-> segment_training_live/records/*.json
```

Casos que cuentan como `corrected`:

- el modelo detecto un grafico pero el box estaba mal;
- el modelo detecto ruido y el usuario elimina el box;
- el modelo no detecto grafico y el usuario agrega un box;

Casos que quedan como `reviewed`:

- el modelo detecto bien y el usuario solo confirma;
- el crop no tiene grafico, el modelo tampoco detecto nada y el usuario solo confirma.

## Exportar Dataset YOLO

Comando:

```powershell
python tools/build_graph_detector_feedback_dataset.py `
  --segments-root .cache/transcriptor_runs/datasets/segment_training_live `
  --out-dir .cache/transcriptor_runs/datasets/graph_detector_feedback_v1
```

Por defecto este exportador incluye solo registros `corrected`.

Para auditoria o diagnostico, se pueden incluir tambien revisiones sin cambios:

```powershell
python tools/build_graph_detector_feedback_dataset.py `
  --segments-root .cache/transcriptor_runs/datasets/segment_training_live `
  --out-dir .cache/transcriptor_runs/datasets/graph_detector_feedback_audit_v1 `
  --include-reviewed
```

Salida:

```text
graph_detector_feedback_v1/
  images/
  labels/
  dataset.yaml
  classes.txt
  records.jsonl
  manifest.json
```

Clase unica:

```text
0 grafico_problema
```

Los registros negativos se guardan con label vacio. Eso ayuda a reducir falsos positivos.

## Entrenamiento Futuro

Ejemplo:

```powershell
python tools/train_graph_detector_yolo.py `
  --data .cache/transcriptor_runs/datasets/graph_detector_feedback_v1/dataset.yaml `
  --model yolov8n.pt `
  --project runs/graph_detector
```

Antes de entrenar:

- revisar `manifest.json`;
- confirmar que `corrected_samples` este cerca o por encima de 200;
- confirmar que hay positivos y negativos;
- revisar visualmente una muestra de labels;
- separar train/val/test si el lote crece.

## Politica De Calidad

Incluir:

- boxes corregidos por humano;
- negativos corregidos, por ejemplo falsos positivos eliminados;
- crops de Geometria Plana con variedad de estilos;
- errores reales del modelo.

Evitar:

- guardar automaticamente todos los aciertos sin revision;
- duplicar muchas imagenes identicas;
- mezclar solucion/problema como objetivo de esta fase;
- usar descripciones de topologia como label YOLO.

## Relacion Con La Capa Semantica

Este dataset mejora solo la deteccion del grafico.

Despues, otro modelo o capa podra usar el crop del grafico para generar:

```text
geometry_figure_description_v1
```

Pero ese descriptor semantico no reemplaza esta golden base. Son dos tareas distintas:

| Tarea | Salida |
| --- | --- |
| Segmentacion grafica | box del grafico dentro del problema |
| Descriptor geometrico | puntos, segmentos, relaciones y condiciones visibles |

## Metricas Iniciales

- positivos totales;
- negativos totales;
- correcciones humanas;
- avance contra la meta de 200 correcciones;
- falsos positivos reducidos;
- falsos negativos reducidos;
- IoU contra boxes revisados;
- precision/recall en validacion manual.

## Pendientes

- Mostrar contador de golden base en la UI.
- Boton para confirmar `sin grafico` como negativo.
- Exportador con splits train/val/test cuando haya suficientes datos.
- Reporte visual HTML para revisar labels YOLO exportados.
