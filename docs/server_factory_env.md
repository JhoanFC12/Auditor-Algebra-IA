# Server Factory Environment

This document defines the environment variables required to run the PDF Factory
pipeline from the server while keeping OCR on Hugging Face.

## Storage

| Variable | Required | Example | Purpose |
|---|---:|---|---|
| `MCS_SERVER_STORAGE_ROOT` | yes | `/srv/mathcontentstudio` | Root for PDFs, page renders, boxes, crops, OCR outputs, graph segments, reviews, jobs, and training corrections. |
| `MCS_FACTORY_ASSET_NAMESPACE` | no | `factory` | Namespace under the storage root used by the Factory. |
| `MCS_PUBLIC_ASSET_BASE_URL` | no | `https://api.nexumathjf.com/assets` | Optional URL prefix for public asset serving. Do not expose private paths directly. |

Server artifacts must be stored as relative `asset_key` values derived from
`MCS_SERVER_STORAGE_ROOT`. Staging records must not persist `D:\...`, `E:\...`,
or any other local Windows path once running in production.

## Server-Local Models

| Variable | Required | Example | Purpose |
|---|---:|---|---|
| `PDF_PROBLEM_MODEL` | yes | `/srv/mathcontentstudio/models/pdf_problem_detector_multiclass_v6_375/weights/best.pt` | Problem detector used for page/problem boxes, numbering, and alternatives when the active detector supports multiple classes. |
| `YOLO_PROBLEM_MODEL` | no | `/srv/mathcontentstudio/models/pdf_problem_detector_multiclass_v6_375/weights/best.pt` | Compatibility alias for problem detection. |
| `YOLO_FIGURE_SEGMENT_MODEL` | yes | `/srv/mathcontentstudio/models/problem_segmentation_yolov8n_golden_v1/weights/best.pt` | Graph/figure segmentation model. |
| `YOLO_FIGURE_MODEL` | no | `/srv/mathcontentstudio/models/problem_segmentation_yolov8n_golden_v1/weights/best.pt` | Compatibility alias for graph/figure segmentation. |

These variables must point to files that exist on the server. If they still
point to Windows paths, the server model inventory must report them as not ready.

## Hugging Face OCR

| Variable | Required | Example | Purpose |
|---|---:|---|---|
| `HF_MODEL` | yes | `Jhoan12/math-ocr-qwen2.5-vl-3b-geometry-agent-nostradamus-wk01-17-merged-v2` | Trained OCR model identifier. |
| `HF_TRAINED_OCR_BASE_URL` | yes for dedicated endpoint | `https://xxxx.endpoints.huggingface.cloud/v1` | OpenAI-compatible base URL for the dedicated OCR endpoint. |
| `HF_TRAINED_OCR_ENDPOINT_NAME` | recommended | `math-ocr-geometry-agent-angle-policy-v1` | Endpoint name used by lifecycle controls. |
| `HF_TOKEN` or `HUGGINGFACEHUB_API_TOKEN` | yes | server secret | Token for inference and endpoint lifecycle calls. |
| `HF_TRAINED_OCR_CLIENT_CONCURRENCY` | no | `4` | Maximum OCR calls in parallel from this server. |
| `HF_ENDPOINT_START_TIMEOUT` | no | `420` | Cold-start wait timeout in seconds. |
| `HF_ENDPOINT_COLD_START_RETRIES` | no | `3` | Retry count for 502/503/cold start failures. |

Tokens must be configured as server secrets and must never be returned by public
API responses.

## Database

| Variable | Required | Example | Purpose |
|---|---:|---|---|
| `DB_PROFILE_DEFAULT` | yes | `cloud` | Default database profile for the remote app. |
| `DB_CLOUD_HOST` | yes | `127.0.0.1` or private host | PostgreSQL host reachable only from the server/private network. |
| `DB_CLOUD_NAME` | yes | `mathcontentstudio_prod` | Production database. |
| `DB_CLOUD_USER` | yes | `mcs_app` | Runtime application role. |
| `DB_CLOUD_PASSWORD` | yes | server secret | Runtime database password. |
| `DB_CLOUD_SSLMODE` | yes | `require` | SSL mode for server database connections. |

PostgreSQL must not be exposed directly to the public Internet.

## Job Runtime

| Variable | Required | Example | Purpose |
|---|---:|---|---|
| `MCS_FACTORY_JOB_ROOT` | no | `/srv/mathcontentstudio/factory/jobs` | Optional explicit job state directory. Defaults to `MCS_SERVER_STORAGE_ROOT/factory/jobs`. |
| `MCS_FACTORY_JOB_RETENTION_DAYS` | no | `30` | Retention policy for finished job metadata. |

Jobs must persist status, counters, logs, errors, and artifact keys so browser
refreshes or reconnects never cancel segmentation/OCR work.
