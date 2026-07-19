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

## 7. Precision-annotation pilot (infrastructure implemented; real pilot pending)

Use 20 non-canonical pages from the first lot after Gottfried has produced V2
roles and provisional units and H-PS1 has frozen the exact scopes.

The sample must include:

- one and two columns;
- one and multiple visual answer blocks;
- graphical alternatives and open questions;
- full-page and partial-page solutions;
- true multipage continuations and repeated headers that are not continuations;
- pages mixing problems, solutions and editorial furniture.

For every page verify against a human golden:

- the parent `problem` includes all alternatives;
- every visible alternative belongs to exactly one `answer_block` of the same
  problem, or the question is explicitly `not_applicable`;
- no neighboring alternative, header, footer, page number or advertising enters
  the box;
- solution fragments contain only their semantic unit;
- visible identifiers are captured;
- multipage relations have reciprocal positive evidence;
- all required `ingrid_geometry_quality_v1` controls pass.

Expected: zero omitted or neighboring alternatives, zero recurring headers
accepted as solution/continuation and no H-PS2 approval while a required check
is `fail` or unresolved `uncertain`.

## 8. Relational dataset validation (implemented; real release pending)

Export only human-approved pilot annotations to a draft
`supervised_relational_annotation_v1` dataset. Do not train a model.

Verify:

1. document, page, region, unit and relation identifiers are reconstructible;
2. normalized and pixel coordinates are valid;
3. required relations are present: `contains`, `belongs_to`, `continues_on`,
   `continues_from`, `solves`, `has_answer_block`, `precedes`, `same_entity`;
4. rejected, abstained and pending annotations are not training-eligible;
5. every derivative inherits the split of its complete source document;
6. no source digest or derivative appears in more than one split;
7. contract, schema, annotator and human-review versions are preserved.

Expected: the release remains `draft` until the manifest, leakage audit and
human review pass.

## 9. IND-MA-01 model gate (implemented; real candidate pending)

Evaluate candidate capabilities only on unseen `test` and `difficult_ood`
documents. The gate requires the thresholds in
[`specialized-model-independence-v1.md`](./contracts/specialized-model-independence-v1.md),
an error audit by document family, abstention, human approval and a rollback
target. A passing average never overrides a systematic critical error.

Implementation checks:

```powershell
python -m unittest tests.test_precision_annotation_contract tests.test_supervised_annotation_export tests.test_document_split_leakage tests.test_specialized_model_evaluation
python scripts\validate_specialized_model_artifacts.py --help
```

The validator is read-only by default. An optional `--output` writes only a
non-canonical report selected by the operator; it never trains or promotes a
model.

## 10. Pre-H-PS1 visual audit adapter (implemented and audited)

Problem Detector Lab now discovers explicit
`problem_detector_visual_audit_session_v1` manifests and revalidates each one
against its live `gottfried_problem_solution_map_v2`, ledger, structural rows,
page media, hashes and revision before rendering it.

Open **Auditoría Biblioteca** and keep **Pre-H-PS1 · mapa de Gottfried** selected.
The batch `euler-precision-pilot-20p-20260717-r1-phase-b-r1` must show:

- `4/4 ready_for_visual_audit`, `0` blocked;
- `14` exact page cards and `25` exact P/S relation cards;
- problem and solution pages side by side, including multipage units;
- only provisional `coarse` overlays from Gottfried and `0` final boxes/crops;
- map/PDF/session fingerprints, artifact and structural-ledger hashes, and `r0`;
- declared relation uncertainties where sections are shared;
- `H-PS1 pendiente`, `Ingrid no activada` and no persistent decision route.

Validation commands:

```powershell
python -m unittest tests.test_problem_detector_lab_server
python -m unittest tests.test_library_web_api tests.test_problem_solution_linking tests.test_problem_solution_staging tests.test_instance_factory_staging tests.test_instance_factory_db_promotion tests.test_instance_factory_web_server tests.test_precision_annotation_contract tests.test_supervised_annotation_export tests.test_document_split_leakage tests.test_specialized_model_evaluation tests.test_problem_detector_lab_server
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" --check modulos\problem_detector_lab\web\app.js
```

Visual evidence: [pre-H-PS1 side-by-side audit](../../output/product-audit/problem-detector-lab-pre-hps1-b162.png).

## 11. Responsive audit controls (implemented and validated)

The Biblioteca audit controls now adapt without sibling overlap or text
overflow:

- header context shrinks inside its own track and never invades the product tabs;
- inspector tabs use three columns at intermediate widths and two on narrow screens;
- page controls wrap instead of escaping the center toolbar;
- decision buttons use an adaptive desktop grid, two columns below `1080px` and
  one column below `640px`;
- the single-column workspace breakpoint starts at `1080px`, eliminating the
  previous horizontal-overflow band immediately above `900px`.
- the complete audit sidebar now owns one vertical scroll; page and relation
  lists retain their natural row heights instead of competing for flex space.

Validation command:

```powershell
$env:PYTHONPATH='E:\Github\Auditor-IA'
python -m unittest discover -s tests -p 'test_problem_detector_lab_server.py' -v
```

Visual evidence: [responsive Biblioteca controls](../../output/product-audit/problem-detector-lab-responsive-buttons.png) and [non-overlapping page/relation lists](../../output/product-audit/problem-detector-lab-sidebar-overlap-fixed.png).

## Validation record

Responsive controls validated on 2026-07-18:

- a follow-up screenshot exposed a vertical case missed by the first button-only
  measurement: at CSS viewport `1343x874`, `auditPageList` had `10px` of client
  height for `203px` of rows and its first page row intersected the relation
  section; the same collapse also occurred at `2039x1074`;
- after moving page and relation rows into one outer sidebar scroll, the page
  list measures `203px`, the relation list `448px`, and all four page/relation
  intersection checks are false at `1343x874`, `2039x1074` and `597x874`;
- the previous header overlap at CSS width `1146px`, inspector-label overflow at
  `1146px`/`955px` and page-level horizontal overflow at `955px` were reproduced
  before the change;
- live measurements at CSS widths `2039`, `1791`, `1343`, `1146`, `955`, `716`
  and `597px` found zero sibling overlaps, zero button text overflows and zero
  page-level horizontal overflow after the change;
- header metadata was checked separately at `2039`, `1146`, `955` and `597px`
  with zero overlap between product tabs, title and both H-PS badges;
- the focused Problem Detector Lab module passed **16 tests**;
- no H-PS1 decision, Ingrid activation, box/crop creation, app/API/DB write,
  map/PDF mutation, training or promotion occurred.

Pre-H-PS1 adapter and real-session audit validated on 2026-07-17:

- Gottfried materialized exactly four session manifests without changing the four r0 maps, `artifact_hashes.json`, `bundle_manifest.json`, structural ledgers or PDFs;
- the adapter reported `4 ready`, `0 blocked`, `14 pages`, `P=25`, `S=25` and `R=25`;
- all four integrity panels showed `passed`, ten hash/revision rows, zero blockers, `H-PS1 pending` and `activate_ingrid=false`;
- all 25 relation cards were present; first/last relations plus the supported multipage cases `161 p11→12`, `162 p93→94` and `196 p177→178` loaded successfully with provisional coarse overlays;
- the focused Problem Detector Lab module passed **14 tests**, and the combined focused/regression suite passed **304 tests** in 73.273 seconds;
- interactive browser checks found zero console errors and corrected the workflow rail so a pre-H-PS1 session can no longer display `H-PS1 aprobado`;
- no H-PS1 decision, Ingrid activation, final box/crop, app/API/DB/dataset write, map/PDF mutation, training or promotion occurred.

V2 precision and IND-MA-01 infrastructure validated on 2026-07-17:

- the combined focused/regression command passed **300 tests** in 60.142 seconds;
- the precision, relational export, split and specialized-model surfaces plus their integrations passed **50 focused tests** in 0.445 seconds;
- `python -m unittest tests.test_supervised_annotation_export.SupervisedAnnotationExportTests.test_synthetic_twenty_page_pilot_shape_is_safe_and_complete` passed in 0.002 seconds and verified 20 unique non-canonical pages, all required case tags, `agents_dispatched: false`, `canonical_writes: disabled`, `training: not_started` and `promotion: not_started`;
- all affected Python modules passed `py_compile`, the bundled Node runtime accepted `web/app.js`, and `git diff --check` passed;
- no Gottfried/Ingrid assignment, real-book pilot, database/PDF write, training, model promotion, `.env` mutation or deployment occurred.

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

Problem Detector Lab Biblioteca audit console validated on 2026-07-16:

- eight focused adapter/UI contracts passed, covering V1 role normalization, V2 coarse/statistics fields, provisional traceability, opaque media tokens and path boundaries;
- the combined focused/regression command passed **275 tests** in 79.759 seconds;
- the live campaign exposed 82 exact assignments, 10 Ingrid outputs and 10 pending H-PS2 scopes without emitting private filesystem paths;
- `server.py` passed `py_compile` and `web/app.js` passed `node --check` with the bundled Node runtime;
- interactive browser checks covered tab switching, statistics, page filters, live solution boxes and session-only H-PS2 marks with zero console errors;
- visual comparison and final status are recorded in the project-root `design-qa.md`;
- no canonical app/DB write, PDF mutation, training, promotion or autonomous `/api/pages/boxes` call occurred.
