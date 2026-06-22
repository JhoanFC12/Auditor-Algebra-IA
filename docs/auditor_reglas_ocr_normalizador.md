# Auditor De Reglas OCR Y Normalizador

Objetivo: medir cumplimiento de reglas antes de seleccionar muestras para reentrenar OCR Geometria o Normalizador. El auditor solo lee registros y escribe reportes; no modifica staging, problemas ni BD.

## Entradas

- `normalizer_input_staging_v1` exportado por `tools/prepare_normalizer_input_dataset.py`.
- `normalizer_training_sample_v1` desde `normalizer_training_bank/samples.jsonl`.
- Registros `records/*.json` de staging.
- JSONL de predicciones con `raw_ocr`, `final_latex`, `prediction`, `completion` o `messages`.

## Metricas

- `unit_spacing`: detecta unidades como `12,cm` o `$12cm$`; espera `12\,cm`.
- `angle_symbol`: detecta `\angle`, unicode de angulo o angulos confundidos con `<`; espera `\sphericalangle`.
- `degree_format`: detecta `40°`, `40º` o `40\circ`; espera `40^\circ`.
- `option_spacing`: detecta `A)$4$`; espera `A) $4$`.
- `continuation_marker`: acepta solo `[CONT.]`.
- `segment_vs_length`: senala confusiones obvias entre `\overline{AB}` y longitud `AB`.
- `arc_vs_measure`: senala `\wideparen` y medidas de arco sin `m\overparen{AB}`.
- `numeric_sets`: senala conjuntos numericos que no usan `\mathbb{N}`, `\mathbb{Z}`, `\mathbb{Q}`, `\mathbb{R}`, `\mathbb{C}`.
- `hallucination_risk`: riesgo de etiquetas de imagen sin evidencia de segmentacion o lenguaje de solucion.
- `final_format_valid`: valida el formato final del Normalizador.
- `alternatives_complete`: valida alternativas A-E y duplicados.

## Uso

Auditar staging:

```powershell
python tools/audit_ocr_normalizer_rules.py `
  --staging-root .cache/transcriptor_runs/staging `
  --mode both
```

Auditar un banco revisado del Normalizador:

```powershell
python tools/audit_ocr_normalizer_rules.py `
  --input .cache/transcriptor_runs/datasets/normalizer_training_bank/samples.jsonl `
  --mode both `
  --out-dir .cache/transcriptor_runs/audits/normalizer_training_bank_rules
```

Archivos de salida:

- `summary.json`: cumplimiento agregado por metrica y objetivo.
- `records.jsonl`: detalle por registro y regla.
- `eligible_samples.jsonl`: muestras que no tienen fallas bloqueantes; usar como filtro conservador para el siguiente entrenamiento.

Politica V1: una muestra es elegible solo si todas las reglas auditadas pasan o no aplican. `hallucination_risk` no es una prueba de verdad absoluta, pero bloquea la elegibilidad hasta revision humana.
