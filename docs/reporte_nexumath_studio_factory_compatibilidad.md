# Nexumath Studio Factory Compatibility Report

Generated: `2026-07-06T14:12:55.527429+00:00`

## Scope

- Auditor-IA root: `E:\Github\Auditor-IA`
- scan-math-db root: `E:\Github\MathContentStudio\scan-math-db`
- scan-math-db exists: `true`

## Summary

- scan_math_required_missing: `0`
- auditor_required_missing: `0`
- scan_math_route_count: `34`
- auditor_factory_route_count: `75`
- expected_factory_routes_present: `10`
- expected_factory_routes_missing: `0`
- studio_web_files: `8`
- studio_web_files_with_local_paths: `0`
- scan_math_db_exists: `True`

## Required scan-math-db Files

| File | Status |
|------|--------|
| `app/main.py` | ok |
| `app/api/studio.py` | ok |
| `app/api/studio_factory.py` | ok |
| `app/api/health.py` | ok |
| `app/math_bank.py` | ok |
| `app/core/config.py` | ok |
| `app/factory_errors.py` | ok |
| `app/factory_bootstrap.py` | ok |
| `app/factory_storage.py` | ok |
| `app/web/studio-login.html` | ok |
| `app/web/studio-factory.html` | ok |
| `app/web/studio-dashboard.html` | ok |
| `app/web/studio-instances.html` | ok |
| `app/web/studio-problems.html` | ok |
| `app/web/studio-pdf-open.html` | ok |
| `app/web/studio-pdf-viewer.html` | ok |

## Required Auditor-IA Factory Files

| File | Status |
|------|--------|
| `modulos/instance_factory/library_web_server.py` | ok |
| `modulos/instance_factory/web_server.py` | ok |
| `modulos/instance_factory/pipeline.py` | ok |
| `modulos/instance_factory/staging.py` | ok |
| `modulos/instance_factory/server_jobs.py` | ok |
| `modulos/instance_factory/server_storage.py` | ok |
| `modulos/instance_factory/model_inventory.py` | ok |
| `modulos/instance_factory/hf_endpoint_manager.py` | ok |
| `modulos/instance_factory/web/app.js` | ok |
| `modulos/instance_factory/web/styles.css` | ok |

## Current scan-math-db Routes

| Method | Path | File | Line |
|--------|------|------|------|
| `GET` | `/` | `app/main.py` | `100` |
| `GET` | `/studio` | `app/main.py` | `115` |
| `GET` | `/studio/` | `app/main.py` | `116` |
| `GET` | `/aula` | `app/main.py` | `121` |
| `GET` | `/aula/` | `app/main.py` | `122` |
| `GET` | `/studio/books` | `app/api/studio.py` | `183` |
| `GET` | `/studio/books/{book_id}/cover` | `app/api/studio.py` | `200` |
| `GET` | `/studio/books/{book_id}/pdf` | `app/api/studio.py` | `219` |
| `POST` | `/studio/books/{book_id}/pdf-link` | `app/api/studio.py` | `247` |
| `GET` | `/studio/books/{book_id}/pdf-native` | `app/api/studio.py` | `271` |
| `GET` | `/studio/books/{book_id}/instances` | `app/api/studio.py` | `296` |
| `PATCH` | `/studio/books/{book_id}` | `app/api/studio.py` | `318` |
| `GET` | `/studio/instances/{instance_id}/problems` | `app/api/studio.py` | `333` |
| `POST` | `/studio/instances/{instance_id}/problems` | `app/api/studio.py` | `355` |
| `GET` | `/studio/collaborators` | `app/api/studio.py` | `376` |
| `POST` | `/studio/collaborators` | `app/api/studio.py` | `384` |
| `GET` | `/studio/assignments` | `app/api/studio.py` | `418` |
| `POST` | `/studio/assignments` | `app/api/studio.py` | `426` |
| `DELETE` | `/studio/assignments/{assignment_id}` | `app/api/studio.py` | `452` |
| `PATCH` | `/studio/problems/{problem_id}` | `app/api/studio.py` | `465` |
| `GET` | `/studio/factory/bootstrap` | `app/api/studio_factory.py` | `53` |
| `GET` | `/studio/factory/books` | `app/api/studio_factory.py` | `71` |
| `GET` | `/studio/factory/books/{book_id}/instances` | `app/api/studio_factory.py` | `109` |
| `GET` | `/studio/factory/instances/{instance_id}/snapshot` | `app/api/studio_factory.py` | `151` |
| `POST` | `/studio/factory/instances/{instance_id}/jobs` | `app/api/studio_factory.py` | `186` |
| `GET` | `/studio/factory/jobs/{job_id}` | `app/api/studio_factory.py` | `225` |
| `POST` | `/studio/factory/records/{record_id}/review` | `app/api/studio_factory.py` | `253` |
| `POST` | `/studio/factory/word/selection` | `app/api/studio_factory.py` | `276` |
| `POST` | `/studio/factory/word/generate` | `app/api/studio_factory.py` | `297` |
| `GET` | `/studio/factory/word/jobs/{job_id}/download` | `app/api/studio_factory.py` | `318` |
| `GET` | `/health` | `app/api/health.py` | `316` |
| `GET` | `/runtime/status` | `app/api/health.py` | `674` |
| `GET` | `/runtime/diagnostics.txt` | `app/api/health.py` | `679` |
| `GET` | `/runtime/logs/recent.txt` | `app/api/health.py` | `684` |

## Current Biblioteca/Fabrica API Inventory

| Method | Path | File | Line |
|--------|------|------|------|
| `POST` | `/api/automation/cancel` | `modulos/instance_factory/library_web_server.py` | `1269` |
| `GET` | `/api/automation/instances` | `modulos/instance_factory/library_web_server.py` | `1265` |
| `POST` | `/api/automation/queue` | `modulos/instance_factory/library_web_server.py` | `1267` |
| `POST` | `/api/automation/retry-errors` | `modulos/instance_factory/library_web_server.py` | `1268` |
| `GET` | `/api/automation/schema` | `modulos/instance_factory/library_web_server.py` | `1266` |
| `GET` | `/api/automation/status` | `modulos/instance_factory/library_web_server.py` | `1264` |
| `POST` | `/api/endpoint/ocr/resume` | `modulos/instance_factory/library_web_server.py` | `1260` |
| `POST` | `/api/endpoint/ocr/scale-to-zero` | `modulos/instance_factory/library_web_server.py` | `1261` |
| `GET` | `/api/endpoint/ocr/status` | `modulos/instance_factory/library_web_server.py` | `1259` |
| `GET` | `/api/library/book` | `modulos/instance_factory/library_web_server.py` | `1251` |
| `POST` | `/api/library/book/create` | `modulos/instance_factory/library_web_server.py` | `1252` |
| `GET` | `/api/library/bootstrap` | `modulos/instance_factory/library_web_server.py` | `1250` |
| `POST` | `/api/library/cover/paste` | `modulos/instance_factory/library_web_server.py` | `1253` |
| `POST` | `/api/library/instance/create` | `modulos/instance_factory/library_web_server.py` | `1254` |
| `POST` | `/api/library/instance/factory` | `modulos/instance_factory/library_web_server.py` | `1255` |
| `POST` | `/api/ocr/jobs/start` | `modulos/instance_factory/library_web_server.py` | `1263` |
| `GET` | `/api/ocr/jobs/status` | `modulos/instance_factory/library_web_server.py` | `1262` |
| `POST` | `/api/pages/detect/jobs/start` | `modulos/instance_factory/library_web_server.py` | `1258` |
| `GET` | `/api/pages/detect/jobs/status` | `modulos/instance_factory/library_web_server.py` | `1257` |
| `GET` | `/api/training/status` | `modulos/instance_factory/library_web_server.py` | `1256` |
| `POST` | `/api/word/convert` | `modulos/instance_factory/library_web_server.py` | `1272` |
| `POST` | `/api/word/convert-instance` | `modulos/instance_factory/library_web_server.py` | `1274` |
| `POST` | `/api/word/convert-instances` | `modulos/instance_factory/library_web_server.py` | `1275` |
| `POST` | `/api/word/convert-problems` | `modulos/instance_factory/library_web_server.py` | `1276` |
| `POST` | `/api/word/open` | `modulos/instance_factory/library_web_server.py` | `1273` |
| `GET` | `/api/word/problems` | `modulos/instance_factory/library_web_server.py` | `1271` |
| `GET` | `/api/word/sessions` | `modulos/instance_factory/library_web_server.py` | `1270` |
| `POST` | `/api/app/reload-signal` | `modulos/instance_factory/web_server.py` | `3097` |
| `GET` | `/api/app/version` | `modulos/instance_factory/web_server.py` | `3096` |
| `POST` | `/api/automation/cancel` | `modulos/instance_factory/web_server.py` | `3125` |
| `POST` | `/api/automation/queue` | `modulos/instance_factory/web_server.py` | `3123` |
| `POST` | `/api/automation/retry-errors` | `modulos/instance_factory/web_server.py` | `3124` |
| `GET` | `/api/automation/schema` | `modulos/instance_factory/web_server.py` | `3107` |
| `GET` | `/api/automation/status` | `modulos/instance_factory/web_server.py` | `3106` |
| `GET` | `/api/bootstrap` | `modulos/instance_factory/web_server.py` | `3093` |
| `POST` | `/api/endpoint/ocr/resume` | `modulos/instance_factory/web_server.py` | `3118` |
| `POST` | `/api/endpoint/ocr/scale-to-zero` | `modulos/instance_factory/web_server.py` | `3119` |
| `GET` | `/api/endpoint/ocr/status` | `modulos/instance_factory/web_server.py` | `3102` |
| `POST` | `/api/normalize` | `modulos/instance_factory/web_server.py` | `3137` |
| `POST` | `/api/normalize/ai` | `modulos/instance_factory/web_server.py` | `3138` |
| `POST` | `/api/normalize/ai/jobs/start` | `modulos/instance_factory/web_server.py` | `3121` |
| `GET` | `/api/normalize/ai/jobs/status` | `modulos/instance_factory/web_server.py` | `3104` |
| `POST` | `/api/ocr/jobs/start` | `modulos/instance_factory/web_server.py` | `3120` |
| `GET` | `/api/ocr/jobs/status` | `modulos/instance_factory/web_server.py` | `3103` |
| `POST` | `/api/ocr/raw` | `modulos/instance_factory/web_server.py` | `3134` |
| `POST` | `/api/ocr/run` | `modulos/instance_factory/web_server.py` | `3133` |
| `POST` | `/api/ocr/segments/boxes` | `modulos/instance_factory/web_server.py` | `3135` |
| `POST` | `/api/pages/boxes` | `modulos/instance_factory/web_server.py` | `3127` |
| `POST` | `/api/pages/delete` | `modulos/instance_factory/web_server.py` | `3129` |
| `POST` | `/api/pages/detect` | `modulos/instance_factory/web_server.py` | `3126` |
| `POST` | `/api/pages/detect/jobs/start` | `modulos/instance_factory/web_server.py` | `3122` |
| `GET` | `/api/pages/detect/jobs/status` | `modulos/instance_factory/web_server.py` | `3105` |
| `POST` | `/api/pages/training-capture` | `modulos/instance_factory/web_server.py` | `3128` |
| `GET` | `/api/pdf/page` | `modulos/instance_factory/web_server.py` | `3098` |
| `GET` | `/api/promotion` | `modulos/instance_factory/web_server.py` | `3100` |
| `POST` | `/api/promotion/upload` | `modulos/instance_factory/web_server.py` | `3101` |
| `GET` | `/api/record` | `modulos/instance_factory/web_server.py` | `3099` |
| `GET` | `/api/records` | `modulos/instance_factory/web_server.py` | `3095` |
| `POST` | `/api/review/save` | `modulos/instance_factory/web_server.py` | `3139` |
| `POST` | `/api/segments/boxes` | `modulos/instance_factory/web_server.py` | `3136` |
| `GET` | `/api/staging/continuations/candidates` | `modulos/instance_factory/web_server.py` | `3131` |
| `POST` | `/api/staging/continuations/candidates` | `modulos/instance_factory/web_server.py` | `3131` |
| `POST` | `/api/staging/continuations/merge` | `modulos/instance_factory/web_server.py` | `3132` |
| `POST` | `/api/staging/materialize` | `modulos/instance_factory/web_server.py` | `3130` |
| `GET` | `/api/summary` | `modulos/instance_factory/web_server.py` | `3094` |
| `POST` | `/api/training/cycle/reset` | `modulos/instance_factory/web_server.py` | `3117` |
| `GET` | `/api/training/normalizer/status` | `modulos/instance_factory/web_server.py` | `3116` |
| `GET` | `/api/training/status` | `modulos/instance_factory/web_server.py` | `3115` |
| `POST` | `/api/word/convert` | `modulos/instance_factory/web_server.py` | `3110` |
| `POST` | `/api/word/convert-instance` | `modulos/instance_factory/web_server.py` | `3112` |
| `POST` | `/api/word/convert-instances` | `modulos/instance_factory/web_server.py` | `3113` |
| `POST` | `/api/word/convert-problems` | `modulos/instance_factory/web_server.py` | `3114` |
| `POST` | `/api/word/open` | `modulos/instance_factory/web_server.py` | `3111` |
| `GET` | `/api/word/problems` | `modulos/instance_factory/web_server.py` | `3109` |
| `GET` | `/api/word/sessions` | `modulos/instance_factory/web_server.py` | `3108` |

## Expected /studio/factory Contract Routes

| Method | Path | Purpose | Status |
|--------|------|---------|--------|
| `GET` | `/studio/factory/bootstrap` | US1 bootstrap | present |
| `GET` | `/studio/factory/books` | US2 library | present |
| `GET` | `/studio/factory/books/{book_id}/instances` | US2 instances | present |
| `GET` | `/studio/factory/instances/{instance_id}/snapshot` | US2 instance snapshot | present |
| `POST` | `/studio/factory/instances/{instance_id}/jobs` | US3 start job | present |
| `GET` | `/studio/factory/jobs/{job_id}` | US3 job status | present |
| `POST` | `/studio/factory/records/{record_id}/review` | US4 review save | present |
| `POST` | `/studio/factory/word/selection` | US4 word selection | present |
| `POST` | `/studio/factory/word/generate` | US4 word generation | present |
| `GET` | `/studio/factory/word/jobs/{job_id}/download` | US4 word artifact download | present |

## Studio Web Files

| File | References | Local Path Refs |
|------|------------|-----------------|
| `app/web/studio-assignments.html` | `7` | `0` |
| `app/web/studio-dashboard.html` | `8` | `0` |
| `app/web/studio-factory.html` | `11` | `0` |
| `app/web/studio-instances.html` | `6` | `0` |
| `app/web/studio-login.html` | `5` | `0` |
| `app/web/studio-pdf-open.html` | `5` | `0` |
| `app/web/studio-pdf-viewer.html` | `6` | `0` |
| `app/web/studio-problems.html` | `6` | `0` |

## Next Actions

- Proceed to Studio shell replacement and contract tests.
