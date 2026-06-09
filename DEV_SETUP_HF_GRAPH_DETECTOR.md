# Entrenamiento en nube del detector de gráficos

## Objetivo

Entrenar en GPU un detector `grafico_problema` usando el dataset YOLO exportado desde las sesiones del Transcriptor IA.

## Archivos preparados

- `config/hf_graph_detector_job.example.json`
- `tools/upload_graph_detector_dataset_to_hf.py`
- `submitted_jobs/train_graph_detector_hf.py`
- `tools/submit_graph_detector_hf_job.py`

## Configuración mínima necesaria

1. Tener sesión iniciada en Hugging Face:

```powershell
hf auth login
```

2. Crear una copia local del archivo de configuración:

```powershell
copy E:\Github\Auditor-IA\config\hf_graph_detector_job.example.json E:\Github\Auditor-IA\config\hf_graph_detector_job.json
```

3. Editar estos valores:

- `dataset_local_path`
- `dataset_repo_id`
- `model_repo_id`
- `flavor`
- `epochs`
- `batch`
- `imgsz`

## Paso 1: subir dataset a la nube

```powershell
python E:\Github\Auditor-IA\tools\upload_graph_detector_dataset_to_hf.py ^
  --dataset-path E:\Github\Auditor-IA\.cache\transcriptor_runs\datasets\graph_detector_20260504_132827 ^
  --repo-id Jhoan12/graph-detector-geom-v1 ^
  --private
```

## Paso 2: lanzar job GPU

```powershell
python E:\Github\Auditor-IA\tools\submit_graph_detector_hf_job.py ^
  --config E:\Github\Auditor-IA\config\hf_graph_detector_job.json
```

## Recomendación inicial de hardware

- `t4-small`: prueba barata
- `l4x1`: mejor punto de partida para entrenamiento serio
- `a10g-large`: si luego aumentamos imagen o dataset

## Recomendación inicial de parámetros

- `base_model = yolov8n.pt`
- `epochs = 50`
- `batch = 16` en `l4x1`
- `imgsz = 1024`

## Resultado esperado

El job descarga el dataset desde el repo privado, entrena YOLO en la nube y sube toda la corrida al repo del modelo en Hugging Face.
