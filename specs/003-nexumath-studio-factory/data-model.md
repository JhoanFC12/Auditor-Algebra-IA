# Data Model: NexumathJF Studio Factory

## Studio User

Represents an authenticated operator.

Fields:

- `id`
- `role`: admin or collaborator for Studio operations
- `assigned_scope`: optional book, instance, or problem permissions
- `active`

Relationships:

- Can start jobs.
- Can review/correct staging records.
- Can promote only within allowed scope.

Validation:

- Must authenticate before accessing Studio workflow.
- Public responses must not include secrets or internal paths.

## Book

Represents an editorial source.

Fields:

- `id`
- `code`
- `title`
- `author`
- `editorial`
- `course`
- `base_topic`
- `cover_asset_id`
- `pdf_asset_id`
- `instance_count`
- `worked_instance_count`
- `database_instance_count`
- `missing_instance_count`
- `workflow_status`

Relationships:

- Has many `Instance`.
- Has server assets for PDF and cover.

Validation:

- Official PDF/cover references must point to server-managed assets before cutover.
- Local paths may exist only as fallback/local artifact metadata.

## Instance

Represents a processing unit derived from a book.

Fields:

- `id`
- `book_id`
- `code`
- `name`
- `practice_title`
- `workflow_stage`
- `status`
- `expected_problem_count`
- `problem_count`
- `staging_count`
- `error_count`
- `updated_at`

Relationships:

- Belongs to `Book`.
- Has many `PageSelection`, `BoxSet`, `StagingRecord`, `ProblemRecord`, `ServerJob`, and `GeneratedPractice`.

Validation:

- Stage changes must be monotonic unless a source edit invalidates downstream artifacts.
- Promotion requires reviewed final format.

## Server Asset

Represents a file managed by server storage.

Fields:

- `id`
- `asset_type`: pdf, cover, page_render, crop, merged_crop, graph_segment, generated_word, training_sample, export
- `server_path`
- `public_url_or_token`
- `checksum`
- `size_bytes`
- `mime_type`
- `created_at`
- `source_reference`

Relationships:

- Can belong to a Book, Instance, StagingRecord, GeneratedPractice, or TrainingCorrection.

Validation:

- `server_path` must stay under the configured server storage root.
- Public APIs expose safe URLs or tickets, not raw private paths when inappropriate.

## Workflow Stage

Represents the current operational state of an instance.

States:

1. `pages`
2. `boxes`
3. `staging`
4. `ocr`
5. `review`
6. `database`
7. `word`

Validation:

- Downstream stages become stale when upstream page/box data changes.
- UI must show stage, counts, and blocking reason.

## Server Job

Represents a long-running operation.

Fields:

- `id`
- `job_type`: page_segmentation, staging_materialization, ocr_queue, graph_segmentation, promotion, word_generation, migration, sync
- `scope_type`: book, instance, selected_records, migration_batch
- `scope_id`
- `status`: queued, running, waiting, retrying, done, failed, cancelled
- `progress_current`
- `progress_total`
- `message`
- `error_summary`
- `result_refs`
- `started_by`
- `created_at`
- `updated_at`

Relationships:

- Belongs to a user and a workflow scope.
- May produce ServerAssets, StagingRecords, ProblemRecords, or GeneratedPractices.

Validation:

- Status must be recoverable after refresh.
- Errors must be itemized when partial failures occur.

## Model Stage

Represents a configured model capability.

Fields:

- `stage_key`: problem_detector, number_alt_detector, figure_segmenter, ocr_endpoint, normalizer_future
- `display_name`
- `provider`: server_local or hugging_face
- `ready`
- `configured_reference`
- `action_required`
- `model_version`

Validation:

- Public UI may show readiness and action, but not secrets.
- Required stages block dependent jobs if unavailable.

## Staging Record

Represents a crop/problem candidate before final promotion.

Fields:

- `id`
- `instance_id`
- `page_number`
- `box_order`
- `crop_asset_id`
- `merged_from`
- `raw_ocr`
- `graph_segments`
- `final_format`
- `review_status`
- `stale_state`
- `errors`

Relationships:

- Belongs to Instance.
- May reference ServerAssets for crop and graph segments.
- Can become a ProblemRecord after review and promotion.

Validation:

- If `stale_state` is invalidated, OCR/review saves are blocked until staging is regenerated.
- Merged crops replace their source crops in OCR candidates.

## Training Correction

Represents a human correction saved for model improvement.

Fields:

- `id`
- `correction_type`: problem_detector, raw_ocr, figure_segmenter, normalizer_final
- `source_record_id`
- `input_asset_id`
- `model_version`
- `model_output`
- `corrected_output`
- `created_by`
- `created_at`

Validation:

- Corrections are training data, not automatic model promotion.
- Corrections must identify which model output was corrected.

## Problem Record

Represents reviewed content promoted to the official database.

Fields:

- `id`
- `book_id`
- `instance_id`
- `number`
- `course`
- `topic`
- `subtopic`
- `final_latex`
- `answer`
- `image_asset_refs`
- `origin`
- `review_state`

Validation:

- Must not be created from unreviewed stale staging.
- Must keep image references unique and server-resolvable.

## Generated Practice

Represents a Word output.

Fields:

- `id`
- `generation_mode`: instance, book, filtered_selection
- `title`
- `selected_problem_refs`
- `word_asset_id`
- `source_tex_asset_id`
- `status`
- `created_at`

Validation:

- Generated files must be stored in server storage.
- Filter selections must persist until user clears them or generates a document.

## Migration Batch

Represents a controlled data/storage migration.

Fields:

- `id`
- `source`
- `target`
- `status`
- `backup_ref`
- `manifest_ref`
- `counts_before`
- `counts_after`
- `path_rewrite_report`
- `rollback_plan`

Validation:

- Must have backup before official cutover.
- Must report unresolved local-only references.

## Public Route

Represents a domain route required for the remote workflow.

Fields:

- `host`
- `path`
- `route_type`: api_health, studio_entry, factory_bootstrap, aula_entry
- `expected_status`: ok, redirect, auth_required
- `current_status_code`
- `last_checked_at`
- `safe_response_sample`
- `blocking_reason`

Validation:

- 404 is a blocking failure for Studio Factory routes after deployment.
- 401/403 is acceptable only when the route exists and requires
  authentication.
- Response samples must pass public safety checks before they are stored in
  diagnostics.
