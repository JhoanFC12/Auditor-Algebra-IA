# Server Factory Model Inventory

Generated at: `2026-07-06T08:11:19.941992+00:00`

## Summary

- Stages checked: `4`
- Config candidates found: `7`
- Warnings: `0`

## Active Stages

| Stage | Provider | Server ready | Action | Exists locally | Source | Model |
|---|---|---:|---|---|---|---|
| `figure_segmenter` | `local` | no | `copy_model_to_server_storage_and_repoint_env` | yes | `env:YOLO_FIGURE_SEGMENT_MODEL` | `E:\Github\Auditor-IA\models\problem_segmentation_yolov8n_golden_v1\weights\best.pt` |
| `normalizer` | `huggingface` | no | `download_or_mount_model_on_server_then_set_env` | n/a | `env:HF_OCR_NORMALIZER_MODEL` | `Jhoan12/math-ocr-normalizer-qwen2.5-0.5b-nostradamus-wk01-17-merged-v3` |
| `ocr` | `huggingface` | yes | `keep_hugging_face_endpoint` | n/a | `env:HF_MODEL` | `Jhoan12/math-ocr-qwen2.5-vl-3b-geometry-agent-nostradamus-wk01-17-merged-v2` |
| `pdf_detector` | `local` | no | `copy_model_to_server_storage_and_repoint_env` | yes | `env:PDF_PROBLEM_MODEL` | `E:\Github\Auditor-IA\models\pdf_problem_detector_multiclass_v6_375\weights\best.pt` |

## Environment Sources Loaded

- `FIGURE_DETECTOR_MODEL`: `E:\Github\Auditor-IA\.env.local`
- `HF_BASE_URL`: `E:\Github\Auditor-IA\.env.local`
- `HF_ENDPOINT_COLD_START_RETRIES`: `E:\Github\Auditor-IA\.env.local`
- `HF_ENDPOINT_POLL_SECONDS`: `E:\Github\Auditor-IA\.env.local`
- `HF_ENDPOINT_START_TIMEOUT`: `E:\Github\Auditor-IA\.env.local`
- `HF_MODEL`: `E:\Github\Auditor-IA\.env.local`
- `HF_OCR_ENSEMBLE`: `E:\Github\Auditor-IA\.env.local`
- `HF_OCR_NORMALIZER_BASE_MODEL_LOCAL_DIR`: `E:\Github\Auditor-IA\.env.local`
- `HF_OCR_NORMALIZER_BASE_URL`: `E:\Github\Auditor-IA\.env.local`
- `HF_OCR_NORMALIZER_LOCAL_DIR`: `E:\Github\Auditor-IA\.env.local`
- `HF_OCR_NORMALIZER_MAX_TOKENS`: `E:\Github\Auditor-IA\.env.local`
- `HF_OCR_NORMALIZER_MODEL`: `E:\Github\Auditor-IA\.env.local`
- `HF_OCR_NORMALIZER_PREFER_LOCAL`: `E:\Github\Auditor-IA\.env.local`
- `HF_OCR_NORMALIZER_TEMPERATURE`: `E:\Github\Auditor-IA\.env.local`
- `HF_OCR_NORMALIZER_TIMEOUT`: `E:\Github\Auditor-IA\.env.local`
- `HF_TOKEN`: `<secret>`
- `HF_TRAINED_OCR_BASE_URL`: `E:\Github\Auditor-IA\.env.local`
- `HF_TRAINED_OCR_CLIENT_CONCURRENCY`: `E:\Github\Auditor-IA\.env.local`
- `HF_TRAINED_OCR_CONTEXT_FALLBACK_IMAGE_MAX_SIDE`: `E:\Github\Auditor-IA\.env.local`
- `HF_TRAINED_OCR_ENDPOINT_NAME`: `E:\Github\Auditor-IA\.env.local`
- `HF_TRAINED_OCR_IMAGE_MAX_SIDE`: `E:\Github\Auditor-IA\.env.local`
- `HF_TRAINED_OCR_MAX_TOKENS`: `E:\Github\Auditor-IA\.env.local`
- `HF_TRAINED_OCR_QUEUE_POLL_SECONDS`: `E:\Github\Auditor-IA\.env.local`
- `HF_TRAINED_OCR_QUEUE_WAIT_TIMEOUT_SECONDS`: `E:\Github\Auditor-IA\.env.local`
- `HUGGINGFACEHUB_API_TOKEN`: `<secret>`
- `OPENAI_API_KEY`: `<secret>`
- `PDF_PROBLEM_MODEL`: `E:\Github\Auditor-IA\.env.local`
- `SCAN_PROVIDER`: `E:\Github\Auditor-IA\.env.local`
- `YOLO_FIGURE_MODEL`: `E:\Github\Auditor-IA\.env.local`
- `YOLO_FIGURE_SEGMENT_MODEL`: `E:\Github\Auditor-IA\.env`
- `YOLO_SEGMENT_MODEL`: `E:\Github\Auditor-IA\.env.local`

## Required Server Actions

1. Copy detector and segmenter model files to server storage.
2. Point server environment variables to `/srv/mathcontentstudio/...` paths.
3. Keep OCR configured through the Hugging Face endpoint.
4. Do not expose model paths or tokens in public API responses.
