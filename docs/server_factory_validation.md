# Server Factory Validation

Date: 2026-07-06

Feature: `specs/002-server-factory-models`

## Scope Validated

- server-safe model inventory;
- server storage path classification;
- page/problem segmentation job persistence;
- Hugging Face OCR retry/cold-start behavior;
- OCR endpoint scale-down guard for parallel jobs;
- raw OCR persistence in server staging;
- training correction persistence;
- stale downstream artifact invalidation;
- web snapshot and UI JavaScript syntax.

## Commands Executed

Final aggregate focused suite:

```powershell
python -m unittest tests.test_instance_factory_model_inventory tests.test_server_factory_inventory tests.test_instance_factory_server_storage tests.test_instance_factory_server_jobs tests.test_instance_factory_server_segmentation tests.test_hf_ocr_endpoint_manager tests.test_instance_factory_training_corrections tests.test_instance_factory_stale_artifacts tests.test_instance_factory_staging tests.test_instance_factory_web_server
```

Result: 172 tests OK. The printed traceback is expected from the test that
verifies internal errors are hidden from the API client.

```powershell
python -m unittest tests.test_instance_factory_model_inventory tests.test_server_factory_inventory
```

Result: 13 tests OK.

```powershell
python -m unittest tests.test_instance_factory_server_segmentation tests.test_instance_factory_server_jobs
```

Result: 9 tests OK.

```powershell
python -m unittest tests.test_instance_factory_training_corrections tests.test_instance_factory_stale_artifacts tests.test_instance_factory_staging
```

Result: 93 tests OK.

```powershell
python -m unittest tests.test_instance_factory_web_server
```

Result: 51 tests OK. The printed traceback is expected from the test that
verifies internal errors are hidden from the API client.

```powershell
python -m py_compile modulos\instance_factory\model_inventory.py modulos\instance_factory\pipeline.py modulos\instance_factory\web_server.py modulos\instance_factory\server_storage.py modulos\instance_factory\training_bank.py modulos\instance_factory\server_jobs.py modulos\instance_factory\staging.py
```

Result: OK.

```powershell
& "C:\Users\Danny Fabián\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" --check modulos\instance_factory\web\app.js
```

Result: OK.

## Quickstart Validation

The quickstart flow was validated through focused automated coverage:

- segmentation job can start, persist, and expose compact status for browser
  reconnect;
- OCR job can run as a reconnectable background job;
- endpoint scale-down waits until parallel OCR jobs finish;
- stale records block downstream edits until staging is regenerated.

Manual server smoke still required after deployment:

1. configure `MCS_SERVER_STORAGE_ROOT`;
2. copy local detector models into server storage;
3. set `PDF_PROBLEM_MODEL` and `YOLO_FIGURE_SEGMENT_MODEL`;
4. configure Hugging Face OCR endpoint variables;
5. process one small PDF instance through pages, boxes, crops, OCR, review, and
   manual promotion.

## Remaining Operational Risk

- Production server model files still need to be copied/mounted and referenced
  by server paths.
- Hugging Face endpoint startup time remains external to the app; the app now
  retries bounded cold-start errors.
- Autonomous agents are not part of this feature and require a separate spec.
