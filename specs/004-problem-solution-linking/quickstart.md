# Quickstart: Validate Problem-Solution Linking

## Prerequisites

- Run from `E:\Github\Auditor-IA`.
- Use a test or local-mirror database profile only.
- Do not train or promote models during this validation.

## 1. Focused tests

```powershell
python -m unittest tests.test_library_web_api tests.test_problem_solution_linking tests.test_problem_solution_staging tests.test_instance_factory_staging tests.test_instance_factory_db_promotion tests.test_instance_factory_web_server
```

Expected: all tests pass, including legacy selection and problem-only promotion.

## 2. Separated-sections scenario

1. Create/open a test instance.
2. Select problem pages `2-4`.
3. Select solution pages `20-22`.
4. Set structure to `separate_sections`, solution status to `identified` and set `practice_01`.
5. Reopen the instance and verify both selections persist.
6. Upsert three reviewed solution units numbered 1-3.
7. Generate links and verify exact scoped matches are proposed.
8. Confirm one link and verify a `ready_for_db` bundle appears in staging.

## 3. Interleaved scenario

1. Mark pages `5-7` in both selections.
2. Set structure to `interleaved`.
3. Supply one unnumbered solution after a reviewed problem in the same column.
4. Generate links and verify proximity evidence appears.
5. Confirm, reject and orphan different candidates; reload and verify decisions persist.

## 4. Promotion dry run

Run the existing promotion preview for a staging problem with a confirmed bundle.

Expected:

- report shows the bundle and solution-group count;
- pending/conflicting bundles are blocked;
- missing assets are reported before a DB write.

## 5. Transaction rollback test

Use the focused test double to fail origin or solution persistence after the problem statement is prepared.

Expected:

- rollback is called for the affected bundle;
- no partial official state remains;
- the next independent record is still processed.

## 6. Regression checks

- Legacy request with only `selected_pages` preserves solution metadata.
- Problem-only staging records still promote as before.
- Replaying the same confirmed bundle creates no duplicate problem or solution group.
- Changing a source digest marks the candidate/bundle stale.
- Changing the semantic page map archives active candidates and blocks stale bundles.
- A configured solution workflow blocks problem-only promotion until a bundle or explicit absence decision exists.
- External solution documents require a stable reference and human confirmation.
- Incomplete multipage solutions cannot enter a bundle.
- No detector training, `.env` mutation or production deployment occurs.

## Validation record

Validated on 2026-07-15 from `E:\Github\Auditor-IA`:

- Focused and regression suite: **267 tests passed** in 84.771 seconds.
- Python compilation: all six affected backend modules passed `py_compile`.
- JavaScript syntax: `web/app.js` passed `node --check` with the bundled workspace runtime.
- Atomic review rollback was exercised at six checkpoints; every injected failure restored state, bundles, record attachments and manifest bytes.
- Concurrent record writers, true cross-problem reassignment, evidence invalidation, external-source enforcement and mandatory multipage roles have focused regression coverage.
- A final independent audit ran 213 focused tests and found no remaining P0/P1 issue in those five hardening areas.
- The tracebacks printed by two tests are intentional assertions that internal paths are hidden from HTTP clients; the suite result is `OK`.

Agent-contract synchronization validated on 2026-07-16:

- the same 267-test focused/regression command passed in 74.420 seconds;
- repeated Gottfried/Ingrid YAML contracts match the portable contract byte for byte;
- capability IDs, state vocabularies, gate references, Markdown fences and relative links passed consistency checks;
- an independent read-only contract audit returned `PASS`;
- no model training, database write, `.env` change or deployment was performed.
