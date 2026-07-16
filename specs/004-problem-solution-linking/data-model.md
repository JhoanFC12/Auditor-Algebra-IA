# Data Model: Enlace Problema-Solucion

## 1. Instance Structure Map

Represents the editorial organization of one existing book instance.

Fields:

- `schema_version`
- `structure_mode`: `separate_sections`, `interleaved`, `hybrid`, `no_solutions`, `unknown`
- `solution_status`: `identified`, `confirmed_absent`, `external_source`, `uncertain`, `pending_review`
- `exercise_set_id`
- `problem_page_selection`
- `solution_page_selection`
- `solution_source`
- `review_status`
- `updated_at`

Rules:

- Problem and solution page sets may overlap.
- A legacy instance has empty solution pages, `unknown` mode and `pending_review` status.
- `external_source` requires a document reference confirmed by a human before link generation.

## 2. Problem Unit

Projection of one reviewed staging problem used by the linker.

Fields:

- `unit_id`
- `record_id`
- `book_id`, `instance_id`, `exercise_set_id`
- `number_raw`, `number_normalized`
- `page_span`
- `box_ids`
- `crop_paths`
- `reading_order`
- `column_index`
- `source_digest`

Rules:

- The `record_id` must identify one non-continuation staging problem.
- Page and box changes alter `source_digest` and invalidate derived links.

## 3. Solution Fragment

One continuous visual region of a solution on one page.

Fields:

- `fragment_id`
- `page_number`
- `bbox_xyxy`
- `crop_path`
- `crop_sha256`
- `fragment_role`: `single`, `begin`, `middle`, `end`
- `reading_order`
- `column_index`

Rules:

- Coordinates must be ordered and non-negative.
- Existing crop assets must match their registered digest when a digest is present.

## 4. Solution Unit

One identifiable solution composed of one or more ordered fragments.

Fields:

- `unit_id`
- `book_id`, `instance_id`, `exercise_set_id`
- `number_raw`, `number_normalized`
- `solution_kind`: `worked`, `short_answer`, `hint`, `unknown`
- `fragments`
- `page_span`
- `variant_index`
- `source_mapping_status`
- `source_digest`
- `provenance.source_version`, `provenance.review_version`
- `continuation_complete`

Rules:

- Fragments must have unique IDs and a valid single/begin-middle-end sequence.
- A solution with incomplete continuity cannot enter a confirmed bundle.
- Multiple solution units may target one problem.

## 5. Candidate Link

Auditable proposal relating one problem unit and one solution unit.

Fields:

- `candidate_link_id`
- `pattern`
- `relation_kind`: `one_to_one`, `alternative_solution`, `shared_solution`
- `problem_ref`, `solution_ref`
- `signals`
- `score`, `runner_up_score`, `score_margin`
- `gates`
- `decision`: `link_proposed_high`, `review_required`, `weak_candidate`, `conflict`, `orphan`, `rejected`
- `ambiguity_reasons`
- `human_review`
- `provenance`
- `candidate_evidence_fingerprint`, `review_fingerprint`

State transitions:

```text
generated -> link_proposed_high|review_required|weak_candidate|conflict|orphan
link_proposed_high|review_required -> human_confirmed|rejected
human_confirmed -> bundled
any derived state -> stale when source_digest changes
```

Rules:

- Both units must share book, instance and exercise set.
- A conflicting explicit number caps the candidate below reviewable automatic proposal.
- Human confirmation records reviewer, timestamp and optional comment.

## 6. Problem-Solution Bundle

Canonical promotion input for one problem and all confirmed visual solutions.

Fields:

- `schema_version`
- `bundle_id`
- `idempotency_key`
- `problem_record_id`
- `scope`
- `problem`
- `solutions`
- `confirmed_link_ids`
- `human_review`
- `provenance`
- `dependency_snapshot`: semantic page map plus unit, candidate, review and event fingerprints
- `status`: persisted review state `human_confirmed`;
- `promotion_status`: derived state `ready_for_db`, `promoted`, `blocked` or `stale`
- `created_at`, `updated_at`

Rules:

- Every solution must come from a human-confirmed link targeting the same problem.
- All source assets and digests must validate before promotion.
- `idempotency_key` is stable for the problem identity and confirmed solution digests.
- A bundle becomes stale when any referenced source digest changes.
- A semantic page-map change archives active candidates and invalidates affected bundles.

## 7. Canonical Visual Solution Group

Stored with the official problem for immediate use before semantic normalization.

Fields:

- `solution_group_id`
- `variant_index`
- `images`
- `fragments` with page, box, role and digest
- `source`
- `link` with method, score, confirmation and evidence
- `bundle_id`

Relationship:

```text
Official Problem 1 --- N Canonical Visual Solution Group
```

Later OCR/LaTeX solution rows may reference the visual group without replacing its provenance.

## 8. Review Event

Append-only audit entry for a candidate or bundle decision.

Fields:

- `event_id`
- `target_type`, `target_id`
- `action`: `confirm`, `reassign`, `reject`, `mark_orphan`, `invalidate`
- `before`, `after`
- `reviewer`, `comment`, `created_at`

Rules:

- Link review events never mutate detector-correction history.
- Reassignment creates a new candidate/bundle version and retains the previous event.

## 9. Per-Problem Solution Status

Append-only human decision used only when an opted-in solution workflow has no
bundle for a specific problem.

Fields:

- `record_id`
- `status`: `pending_review` or `solutions_absent_confirmed`
- `reviewer`, `comment`, `reviewed_at`

Rules:

- `solutions_absent_confirmed` is forbidden while an unresolved candidate still
  points to the problem.
- A configured solution workflow blocks problem-only promotion until it has a
  confirmed bundle, global `confirmed_absent`, or this per-record terminal state.
