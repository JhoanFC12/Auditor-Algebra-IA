# Contract: Job Lifecycle

## Job States

```text
queued -> running -> done
queued -> running -> retrying -> running -> done
queued -> running -> failed
queued -> cancelled
running -> cancelled
```

`waiting` is allowed for jobs blocked by endpoint cold start, missing model readiness, or storage availability.

## Job Status Payload

```json
{
  "job_id": "job_abc123",
  "job_type": "ocr_queue",
  "status": "running",
  "scope": {
    "type": "instance",
    "id": 100
  },
  "progress": {
    "current": 12,
    "total": 40,
    "label": "12 of 40"
  },
  "message": "Running OCR",
  "started_by": 1,
  "created_at": "2026-07-06T00:00:00Z",
  "updated_at": "2026-07-06T00:01:00Z",
  "items": [
    {
      "item_id": "rec_12",
      "status": "done",
      "message": "OCR saved",
      "result_ref": "asset_or_record_ref"
    }
  ],
  "errors": [
    {
      "item_id": "rec_13",
      "stage": "ocr",
      "code": "SERVICE_UNAVAILABLE",
      "message": "OCR endpoint unavailable after retry window",
      "action": "Retry when endpoint is running"
    }
  ],
  "result": {
    "summary": "12 saved, 1 failed",
    "asset_refs": []
  }
}
```

## Requirements

- Job status must survive browser refresh.
- Job status must be queryable from another authenticated device.
- Partial item failures must not hide successful items.
- Failed items must include an action-oriented error category.
- OCR jobs must not scale down the endpoint until no active OCR jobs remain.
- Cancelled jobs must not delete completed item results unless the user explicitly requests cleanup.
- Jobs must never return raw tracebacks or private server paths to the public UI.

## Minimum Job Types

| Job Type | Recoverable | Partial Failure | Produces |
|----------|-------------|-----------------|----------|
| page_segmentation | yes | yes | page boxes, detector corrections |
| staging_materialization | yes | yes | crop assets, staging records |
| ocr_queue | yes | yes | raw OCR, OCR corrections |
| graph_segmentation | yes | yes | graph segment assets/corrections |
| promotion | yes | yes | official problem records |
| word_generation | yes | no for one document, yes for batch | Word assets |
| migration | yes | yes | migration manifest/report |
| sync | yes | yes | backup or mirror report |
