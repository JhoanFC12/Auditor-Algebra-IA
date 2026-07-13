# Quickstart: Server PDF Segmentation Smoke Test

This smoke test validates US1: page/problem segmentation runs as an observable
server job, writes page-box artifacts through server storage, and survives a
browser refresh.

## Prerequisites

- The Biblioteca/Fabrica web server is running.
- The target PDF is already registered in Biblioteca/Fabrica.
- Server storage is configured with `MCS_SERVER_STORAGE_ROOT`.
- The problem segmentation model is configured with `PDF_PROBLEM_MODEL`.

## Scope Guard

Autonomous agents are not required for this smoke test or for the current
server-side Factory implementation. The workflow must remain operable through
explicit user actions, server jobs, staging, and human review. Agent automation
is documented separately as future scope.

## Manual Smoke

1. Open Biblioteca/Fabrica and enter a book instance.
2. Go to stage 1, select one or more PDF pages, and click `Detectar con modelo`.
3. Confirm the UI shows a running page segmentation job with progress.
4. Refresh the browser tab while the job is running.
5. Confirm the UI reconnects to the same job instead of losing state.
6. Wait for completion and go to stage 2.
7. Confirm detected page boxes are visible and editable.
8. Restart the browser tab and confirm the boxes remain visible.

## API Smoke

Start a segmentation job:

```bash
curl -X POST "http://127.0.0.1:8765/api/pages/detect/jobs/start" \
  -H "Content-Type: application/json" \
  -d "{\"instance_id\":\"BOOK__INSTANCE\",\"pages\":\"1-2\",\"compact\":true}"
```

Poll status:

```bash
curl "http://127.0.0.1:8765/api/pages/detect/jobs/status?instance_id=BOOK__INSTANCE&job_id=JOB_ID"
```

Expected status response:

- `schema_version` is `pdf_factory_page_detect_job_v1`.
- `status` becomes `done`.
- `running` becomes `false`.
- `result.schema_version` is `pdf_factory_web_pages_detected_v1`.
- `result.pages` contains the detected page records.

## Artifact Verification

After the job finishes, inspect the staging root for the instance:

- `server_artifacts.json` contains `page_boxes`.
- `manifest.json` contains `server_storage.page_boxes`.
- Each artifact stores a server-safe `asset_key`, not a Windows local path.

## Regression Commands

```bash
python -m unittest tests.test_instance_factory_server_segmentation tests.test_instance_factory_web_server.InstanceFactoryWebServerTests.test_page_detection_job_exposes_compact_result_for_browser_reconnect
```

Optional syntax checks:

```bash
python -m py_compile modulos/instance_factory/staging.py modulos/instance_factory/server_jobs.py modulos/instance_factory/web_server.py modulos/instance_factory/library_web_server.py
node --check modulos/instance_factory/web/app.js
```
