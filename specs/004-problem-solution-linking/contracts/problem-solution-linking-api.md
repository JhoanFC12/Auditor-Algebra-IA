# Contract: Problem-Solution Linking API

All operations are instance-scoped and operate on staging only.
Every mutating request MUST include the last observed `expected_revision`; a
missing value is rejected and a stale value returns a revision conflict.

## GET `/api/problem-solutions`

Returns persisted solution units, candidate links, bundles, counts and stale/conflict status.

## POST `/api/problem-solutions/solution-units`

Upserts reviewed solution units supplied by the segmentation workflow.

Required per unit:

- stable `unit_id`;
- scope identifiers;
- zero or one normalized problem number;
- one or more fragments with page, box and crop reference;
- source/review versions.
- `expected_revision` for the workspace being replaced.

Invalid units do not replace a previously valid unit.

## POST `/api/problem-solutions/generate`

Inputs:

```json
{
  "pattern": "separate_sections",
  "exercise_set_id": "practice_04",
  "source_mapping_confirmed": true,
  "expected_revision": 3
}
```

Returns deterministic candidate links. Generation is idempotent for unchanged unit digests.

## POST `/api/problem-solutions/review`

Inputs:

```json
{
  "candidate_link_id": "psl_...",
  "action": "confirm",
  "problem_unit_id": "problem_...",
  "reviewer": "human",
  "comment": "",
  "expected_revision": 4
}
```

Allowed actions: `confirm`, `reassign`, `reject`, `mark_orphan`.

Confirmation creates or updates the affected problem bundle and attaches it to the staging problem record. No canonical DB write occurs.
The response includes tokenized crop URLs so the human can compare the problem
and every solution fragment side by side without exposing arbitrary files.

For `solution_status = external_source`, confirmation also requires a
`document_relation` with `external: true`, `status: confirmed`, a stable PDF or
document reference, and the confirming reviewer.

## POST `/api/problem-solutions/problem-status`

Records the per-problem terminal decision `solutions_absent_confirmed`, or puts
the problem back into `pending_review`. An absence decision is rejected while a
non-terminal candidate still points at that problem. This is the only V1 path
that permits a problem in an opted-in solution workflow to promote without a
bundle.

## Atomic review rule

Candidate replacement, append-only review event, bundle reconciliation and
record attachment form one staging transaction. Any failure restores the prior
state before the API responds.

## Candidate thresholds

- High proposal: score at least 85, runner-up margin at least 20 and all gates pass.
- Review required: score 65-84 or margin 10-19.
- Weak: score 40-64.
- Orphan: score below 40 or no candidate.
- Conflict: margin below 10, duplicate identity, incompatible explicit numbers or invalid source mapping.

Every first-version candidate requires human confirmation regardless of score.
