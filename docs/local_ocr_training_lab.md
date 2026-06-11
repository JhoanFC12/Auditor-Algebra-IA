# Laboratorio Local De OCR

Este laboratorio prueba modelos locales para reemplazar o reducir el uso del endpoint OCR. Por ahora el objetivo es solo:

```text
imagen crop -> OCR crudo corregido en nuestro formato
```

La normalizacion queda pendiente hasta definir y entrenar ese modelo aparte.

## 1. Preparar Dataset

Usar las golden bases revisadas:

```powershell
python tools/prepare_local_ocr_lab_dataset.py `
  --out-dir .cache/transcriptor_runs/datasets/local_ocr_lab_smoke `
  --golden-dir .cache/transcriptor_runs/datasets/ocr_geometry_golden_live `
  --max-samples 120
```

Para combinar geometria + OCR general:

```powershell
python tools/prepare_local_ocr_lab_dataset.py `
  --out-dir .cache/transcriptor_runs/datasets/local_ocr_lab_full
```

Para sumar OCR crudo editado desde staging:

```powershell
python tools/prepare_local_ocr_lab_dataset.py `
  --out-dir .cache/transcriptor_runs/datasets/local_ocr_lab_with_staging `
  --staging-root .cache/transcriptor_runs/staging
```

El exportador incluye solo staging con traza `human_raw_ocr_editor`, salvo que se use `--include-unreviewed-staging`.

## 2. Revisar Entorno Local

```powershell
python tools/train_local_ocr_lora.py `
  --dataset-dir .cache/transcriptor_runs/datasets/local_ocr_lab_smoke `
  --dry-run
```

Esto no descarga modelos. Solo revisa conteos, una imagen de ejemplo y si hay GPU CUDA.

## 3. Evaluar Candidatos Sin Entrenar

Evaluar la salida guardada en `raw_candidate` contra el texto corregido:

```powershell
python tools/evaluate_local_ocr_dataset.py `
  --dataset-dir .cache/transcriptor_runs/datasets/local_ocr_lab_smoke `
  --split test `
  --hide-results
```

Evaluar OCR local/Tesseract si está instalado:

```powershell
python tools/evaluate_local_ocr_dataset.py `
  --dataset-dir .cache/transcriptor_runs/datasets/local_ocr_lab_smoke `
  --split test `
  --provider local_ocr `
  --limit 10 `
  --out .cache/transcriptor_runs/evals/local_ocr_tesseract_test.json
```

También se puede evaluar un modelo externo guardando predicciones como:

```json
{
  "sample_id": "<01.> texto predicho..."
}
```

y usando `--predictions predicciones.json`.

## 4. Instalar Stack Opcional

Solo si vamos a entrenar:

```powershell
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
python -m pip install -r requirements-local-ocr.txt
```

## 5. Primer Entrenamiento Local Pequeño

```powershell
python tools/train_local_ocr_lora.py `
  --dataset-dir .cache/transcriptor_runs/datasets/local_ocr_lab_smoke `
  --output-dir models/local_ocr/qwen2_5_vl_3b_lora_smoke `
  --epochs 1 `
  --max-train-samples 80 `
  --max-eval-samples 20
```

Recomendacion inicial:

- GPU local de 24 GB: Qwen2.5-VL 3B LoRA es el primer candidato.
- GPU local de 16 GB: puede funcionar con imagenes reducidas, pero es estrecho.
- Solo CPU: no conviene entrenar VLM; mejor usar CPU para preparar dataset/evaluar y entrenar en una ventana corta de GPU.

## Candidatos

- `Qwen/Qwen2.5-VL-3B-Instruct` + LoRA: primer candidato porque ya estamos usando esta familia y nuestro dataset encaja con imagen+prompt+respuesta.
- PaddleOCR-VL: candidato ligero para evaluar como OCR/document parser local, pero requiere integración separada.
- pix2tex: auxiliar para formulas, no reemplaza todo el problema.

## Criterios De Decision

Mediremos:

- alucinaciones: texto agregado que no aparece;
- respeto del formato `<n.>` y `[CONT.]`;
- calidad de LaTeX;
- alternativas A-E preservadas;
- velocidad por imagen;
- memoria usada;
- estabilidad con crops con grafico.
