# Contract: Studio Factory API

This contract defines public Studio-facing endpoints needed to replace the current Studio workflow with Biblioteca/Fabrica.

## Principles

- All endpoints require authenticated Studio access unless explicitly marked health/public.
- Public payloads must not expose tokens, local Windows paths, server private paths, or tracebacks.
- Long operations return a `job_id`; clients poll job status.
- Records with stale upstream state must return a blocking reason instead of accepting edits.

## GET /studio/factory/bootstrap

Returns initial workflow state.

Response:

```json
{
  "user": {
    "id": 1,
    "role": "admin"
  },
  "health": {
    "database": "ok",
    "storage": "ok",
    "ocr_endpoint": "running",
    "models": "ready",
    "active_jobs": 2
  },
  "navigation": {
    "default_view": "library",
    "legacy_studio_available": true
  },
  "cutover": {
    "mode": "validation",
    "server_is_source_of_truth": false,
    "local_pc_role": "backup_only",
    "local_writes_allowed": false,
    "backup_verified": false,
    "rollback_verified": false,
    "assets_migrated": false,
    "can_declare_official": false,
    "warnings": [
      "Servidor en validacion: aun no es la fuente oficial."
    ],
    "next_action": "Completar backup, rollback y migracion de assets antes del corte."
  }
}
```

## GET /studio/factory/books

Query parameters:

- `status`
- `course`
- `query`
- `recent`
- `limit`

Response:

```json
{
  "items": [
    {
      "id": 10,
      "code": "aseuni-semianual-geometria",
      "title": "ASEUNI SEMIANUAL - GEOMETRIA",
      "author": "Academia ASEUNI",
      "course": "Geometria",
      "cover_url": "/studio/factory/books/10/cover",
      "counts": {
        "instances": 19,
        "worked": 19,
        "in_database": 15,
        "missing": 4
      },
      "status": "in_progress",
      "last_activity_at": "2026-07-06T00:00:00Z"
    }
  ],
  "summary": {
    "books": 52,
    "in_progress": 274,
    "complete": 156,
    "empty": 88
  }
}
```

## GET /studio/factory/books/{book_id}/instances

Returns instances for a book with stage summary.

Response:

```json
{
  "book": {
    "id": 10,
    "title": "ASEUNI SEMIANUAL - GEOMETRIA"
  },
  "items": [
    {
      "id": 100,
      "code": "semana_01",
      "name": "Segmentos y angulos",
      "practice_title": "Segmentos y angulos",
      "stage": "database",
      "status": "complete",
      "counts": {
        "pages": 3,
        "boxes": 40,
        "staging": 40,
        "ocr": 40,
        "reviewed": 40,
        "in_database": 40,
        "word": 1,
        "errors": 0
      },
      "blocking_reason": ""
    }
  ]
}
```

## GET /studio/factory/instances/{instance_id}/snapshot

Returns the instance workflow state.

Response:

```json
{
  "instance": {
    "id": 100,
    "book_id": 10,
    "code": "semana_01",
    "name": "Segmentos y angulos",
    "stage": "ocr"
  },
  "server_models": {
    "stage_map": {
      "problem_detector": {"ready": true, "provider": "server_local"},
      "number_alt_detector": {"ready": true, "provider": "server_local"},
      "figure_segmenter": {"ready": true, "provider": "server_local"},
      "ocr_endpoint": {"ready": true, "provider": "hugging_face"}
    }
  },
  "records": [],
  "jobs": []
}
```

## POST /studio/factory/instances/{instance_id}/jobs

Starts a long operation.

Request:

```json
{
  "job_type": "ocr_queue",
  "selection": {
    "mode": "missing_or_errors",
    "record_ids": []
  },
  "options": {
    "concurrency": 4
  }
}
```

Response:

```json
{
  "job_id": "job_abc123",
  "status": "queued",
  "status_url": "/studio/factory/jobs/job_abc123"
}
```

Allowed `job_type` values:

- `page_segmentation`
- `staging_materialization`
- `ocr_queue`
- `graph_segmentation`
- `promotion`
- `word_generation`
- `migration`
- `sync`

## POST /studio/factory/records/{record_id}/review

Saves human review or final format.

Request:

```json
{
  "final_format": "\\item[\\textbf{1.}] ...",
  "review_status": "ready",
  "notes": ""
}
```

Response:

```json
{
  "record_id": "rec_1",
  "status": "saved",
  "training_correction_saved": true,
  "stale_state": "fresh"
}
```

If stale:

```json
{
  "record_id": "rec_1",
  "status": "blocked",
  "blocking_reason": "Regenerate staging before editing this record."
}
```

## POST /studio/factory/word/selection

Updates cross-filter Word selection.

Request:

```json
{
  "action": "add",
  "problem_ids": [1, 2, 3]
}
```

Response:

```json
{
  "selected_count": 3,
  "selection_id": "selection_user_1"
}
```

## POST /studio/factory/word/generate

Starts Word generation from instances or selected problems.

Request:

```json
{
  "mode": "filtered_selection",
  "selection_id": "selection_user_1",
  "title": "Practica de triangulos"
}
```

Response:

```json
{
  "job_id": "job_word_123",
  "status_url": "/studio/factory/jobs/job_word_123"
}
```
