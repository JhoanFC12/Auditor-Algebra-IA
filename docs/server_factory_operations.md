# Server Factory Operations Guide

This guide describes the operational path for the server-side PDF Factory.

## Runtime Components

- Biblioteca/Fabrica web server: local/remote web UI and API layer.
- Server storage: filesystem root configured by `MCS_SERVER_STORAGE_ROOT`.
- Server jobs: persisted background work for segmentation and OCR queues.
- Local server models:
  - `PDF_PROBLEM_MODEL`;
  - `YOLO_FIGURE_SEGMENT_MODEL`;
  - derived `number_alt_detector` using the problem detector multiclass model.
- Hugging Face OCR endpoint:
  - `HF_TOKEN` or `HUGGINGFACEHUB_API_TOKEN`;
  - `HF_TRAINED_OCR_ENDPOINT_NAME`;
  - `HF_TRAINED_OCR_BASE_URL`.

## Minimum Environment

```powershell
$env:MCS_SERVER_STORAGE_ROOT = "E:\server-storage"
$env:PDF_PROBLEM_MODEL = "E:\server-storage\models\pdf_problem_detector\best.pt"
$env:YOLO_FIGURE_SEGMENT_MODEL = "E:\server-storage\models\figure_segmenter\best.pt"
$env:HF_MODEL = "Jhoan12/math-ocr-qwen2.5-vl-3b-geometry-agent"
$env:HF_TOKEN = "hf_..."
```

On Linux server, use server paths under the configured storage root, for example:

```bash
export MCS_SERVER_STORAGE_ROOT=/srv/mathcontentstudio
export PDF_PROBLEM_MODEL=/srv/mathcontentstudio/models/pdf_problem_detector/best.pt
export YOLO_FIGURE_SEGMENT_MODEL=/srv/mathcontentstudio/models/figure_segmenter/best.pt
export HF_MODEL=Jhoan12/math-ocr-qwen2.5-vl-3b-geometry-agent
export HF_TOKEN=hf_...
```

## Model Readiness

The web snapshot exposes `server_models`:

- `server_ready=true`: model can run from server configuration.
- `server_ready=false`: inspect `action`.
- common actions:
  - `copy_model_to_server_storage_and_repoint_env`;
  - `server_model_file_missing`;
  - `download_or_mount_model_on_server_then_set_env`;
  - `keep_hugging_face_endpoint`.

The UI shows the same status in model cards.

## Job Flow

1. Select PDF pages.
2. Start page/problem segmentation.
3. Poll job status until `done`.
4. Review boxes.
5. Materialize staging/crops.
6. Run OCR queue.
7. Review OCR and graph segments.
8. Save final review in staging.
9. Promote manually to database only after human review.

## OCR Endpoint Cost Control

- OCR jobs acquire an endpoint lease before starting.
- Cold-start errors (`502`, `503`, `504`, timeout/loading) are retried.
- Non-recoverable errors such as `403` fail immediately.
- `scale-to-zero` is called only when no active OCR jobs remain.

## Stale Artifact Rule

When a source page box changes:

- dependent crop path is cleared if the source box no longer matches;
- OCR, graph segmentation, normalization, review, artifacts, and sync state are
  cleared;
- record audit is marked `downstream_state.status=invalidated`;
- OCR/review edits are blocked until staging is regenerated.

## Training Data Rule

Human corrections are training data:

- page/box corrections go to `problem_detector_corrections`;
- raw OCR corrections go to `ocr_golden_live`;
- graph segment corrections go to `segment_training_live`;
- final normalization/review corrections go to `normalizer_training_bank`.

## Health Commands

```powershell
python -m unittest tests.test_instance_factory_model_inventory tests.test_server_factory_inventory
python -m unittest tests.test_instance_factory_server_jobs tests.test_instance_factory_server_segmentation
python -m unittest tests.test_instance_factory_training_corrections tests.test_instance_factory_stale_artifacts
python -m unittest tests.test_instance_factory_web_server
```

Syntax checks:

```powershell
python -m py_compile modulos\instance_factory\model_inventory.py modulos\instance_factory\server_jobs.py modulos\instance_factory\training_bank.py
& "C:\Users\Danny Fabián\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" --check modulos\instance_factory\web\app.js
```
