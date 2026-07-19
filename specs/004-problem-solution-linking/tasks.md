# Tasks: Enlace Problema-Solucion

**Input**: Design documents from `/specs/004-problem-solution-linking/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Focused tests are required because the feature changes persisted instance configuration and canonical database promotion.

## Phase 1: Setup And Baseline

**Purpose**: Confirm the existing behavior that must remain compatible.

- [X] T001 Run and record the baseline for `tests/test_library_web_api.py`, `tests/test_instance_factory_db_promotion.py`, `tests/test_instance_factory_staging.py`, and `tests/test_instance_factory_web_server.py`
- [X] T002 Verify ignore and repository safety constraints for new staging sidecars in `.gitignore`

---

## Phase 2: Foundational Domain Contracts

**Purpose**: Create shared deterministic contracts before UI or database integration.

- [X] T003 [P] Add failing unit tests for number normalization, separated/interleaved matching, conflicts, continuations, fingerprints and bundle validation in `tests/test_problem_solution_linking.py`
- [X] T004 Implement pure problem/solution units, candidate scoring, bundle fingerprints and validation in `modulos/instance_factory/problem_solution_linking.py`
- [X] T005 [P] Add failing persistence tests for solution units, candidates, reviews, optimistic revisions and bundles in `tests/test_problem_solution_staging.py`
- [X] T006 Add atomic problem-solution sidecar persistence and lookup methods in `modulos/instance_factory/staging.py`

**Checkpoint**: The linker and its staging workspace are deterministic and independently testable.

---

## Phase 3: User Story 1 - Mapear enunciados y soluciones por instancia (Priority: P1)

**Goal**: Persist and restore independent, overlapping problem and solution page selections.

**Independent Test**: Save separate and overlapping ranges, reopen the instance and verify both roles plus structure state survive; a legacy request must preserve V2 fields.

- [X] T007 [P] [US1] Add failing API tests for overlapping page roles, enums, idempotence and legacy preservation in `tests/test_library_web_api.py`
- [X] T008 [P] [US1] Add failing context round-trip and legacy-default tests in `tests/test_instance_factory_staging.py`
- [X] T009 [US1] Extend instance page-selection validation and `config_snapshot` persistence in `modulos/instance_factory/library_api.py`
- [X] T010 [US1] Extend `InstancePipelineContext` with solution page ranges and structure metadata in `modulos/instance_factory/models.py`
- [X] T011 [P] [US1] Add failing static/contract coverage for solution-page UI persistence in `tests/test_instance_factory_web_server.py`
- [X] T012 [US1] Add problem/solution role selection, overlap display and structure controls in `modulos/instance_factory/web/app.js`
- [X] T013 [US1] Add role-specific visual states for problem, solution and overlapping page chips in `modulos/instance_factory/web/styles.css`

**Checkpoint**: A saved instance page map is recoverable and backward-compatible.

---

## Phase 4: User Story 2 - Revisar enlaces problema-solucion (Priority: P1)

**Goal**: Generate, persist and review auditable link proposals without canonical writes.

**Independent Test**: Upsert solution units, generate separated/interleaved candidates, confirm/reassign/reject/orphan links and recover all decisions after reload.

- [X] T014 [P] [US2] Add failing web API contract tests for solution-unit upsert, generation, review and reload in `tests/test_instance_factory_web_server.py`
- [X] T015 [US2] Integrate problem-unit projection, candidate generation, review events and bundle attachment in `modulos/instance_factory/problem_solution_linking.py`
- [X] T016 [US2] Expose staging-only problem-solution query, unit, generation and review routes in `modulos/instance_factory/web_server.py`
- [X] T017 [US2] Add side-by-side candidate review, evidence and conflict/orphan actions to `modulos/instance_factory/web/app.js`
- [X] T018 [US2] Add link-review layout and state styles in `modulos/instance_factory/web/styles.css`

**Checkpoint**: Confirmed links create ready bundles in staging; no DB write has occurred.

---

## Phase 5: User Story 3 - Promover paquetes completos (Priority: P1)

**Goal**: Store a reviewed problem and all confirmed visual solutions atomically and idempotently.

**Independent Test**: Promote one bundle, replay it without duplicates, simulate a mid-transaction failure and verify rollback only affects that package.

- [X] T019 [P] [US3] Add failing tests for confirmed bundle payloads, missing assets, rollback, idempotence, preservation of legacy solutions and independent continuation in `tests/test_instance_factory_db_promotion.py`
- [X] T020 [US3] Add visual-solution preflight, rich solution payload construction and non-destructive update semantics in `modulos/instance_factory/db_promotion.py`
- [X] T021 [US3] Include bundle IDs, solution counts and blocking reasons in promotion responses from `modulos/instance_factory/web_server.py`
- [X] T022 [US3] Show bundle readiness, solution totals and promotion errors in `modulos/instance_factory/web/app.js`

**Checkpoint**: Each confirmed bundle commits problem, solutions and origin together; existing problem-only promotion remains valid.

---

## Phase 6: User Story 4 - Conservar procedencia y decisiones (Priority: P2)

**Goal**: Preserve reconstructable sources and separate link-review history from detector corrections.

**Independent Test**: Reload a promoted/reviewed bundle and reconstruct source pages, boxes, fragments, hashes, versions and every human decision.

- [X] T023 [P] [US4] Add failing stale-source and append-only review-history tests in `tests/test_problem_solution_linking.py`
- [X] T024 [US4] Persist source fingerprints, provenance versions and append-only review events in `modulos/instance_factory/staging.py`
- [X] T025 [US4] Invalidate derived links and bundles when referenced page/box fingerprints change in `modulos/instance_factory/problem_solution_linking.py`
- [X] T026 [US4] Expose provenance and stale reasons in problem-solution web payloads from `modulos/instance_factory/web_server.py`

**Checkpoint**: Every promoted visual solution is traceable and stale derivatives cannot be promoted.

---

## Final Phase: Polish And Validation

- [X] T027 Run the focused suite from `specs/004-problem-solution-linking/quickstart.md` and fix regressions in affected files
- [X] T028 Run `python -m unittest tests.test_library_web_api tests.test_problem_solution_linking tests.test_problem_solution_staging tests.test_instance_factory_staging tests.test_instance_factory_db_promotion tests.test_instance_factory_web_server` and record the result in `specs/004-problem-solution-linking/quickstart.md`
- [X] T029 Run `git diff --check` and verify no model training, `.env` mutation, production deployment or unrelated file overwrite occurred
- [X] T030 Update implementation status, known limits and next pilot in `specs/004-problem-solution-linking/plan.md`

## Phase 7: Audit hardening

- [X] T031 Enforce complete scope, reviewed solution-unit provenance, configured page-role filtering and semantic context invalidation
- [X] T032 Require optimistic revisions and expose tokenized problem/solution crops with scoring evidence in the human review UI
- [X] T033 Revalidate bundles at the DB write boundary, materialize solution assets durably and replace retired managed groups without deleting legacy groups
- [X] T034 Block unconfirmed external documents, incomplete multipage solutions and problem-only promotion without an explicit absence decision
- [X] T035 Apply candidate decision, append-only event and bundle reconciliation as one rollback-safe staging transaction
- [X] T036 Re-run the complete focused/regression suite after audit hardening and record final evidence
- [X] T037 Close final audit findings for evidence invalidation, true reassignment, external sources, concurrent record writers and multipage roles
- [X] T038 Re-run the 267-test focused/regression suite and record the independent no-P0/P1 audit result

## Phase 8: Agent Contract Synchronization

- [X] T039 Publish the portable Euler-Gottfried-Ingrid problem-solution contract and synchronize the three operational profiles
- [X] T040 Add a canonical Obsidian adenda and superseding notes to historical Biblioteca contracts without erasing prior decisions
- [X] T041 Validate capability IDs, assignment schemas, state vocabularies, human gates and Markdown integrity across the synchronized contracts

## Phase 9: Read-only Biblioteca Audit Console

- [X] T042 [P] Add failing adapter, legacy-normalization, media-boundary and static UI tests for the Biblioteca audit console in `tests/test_problem_detector_lab_server.py`
- [X] T043 Add a read-only Gottfried/Ingrid artifact adapter and tokenized audit-media routes in `modulos/problem_detector_lab/server.py`
- [X] T044 Add the separate Biblioteca audit tab and page/instance navigation in `modulos/problem_detector_lab/web/index.html` and `modulos/problem_detector_lab/web/app.js`
- [X] T045 Add coarse Gottfried regions, precise Ingrid boxes, eligibility, statistics and traceability presentation in `modulos/problem_detector_lab/web/styles.css`
- [X] T046 Run focused/regression checks and verify the console performs no canonical, PDF, training or promotion write
- [X] T047 Compare the implementation with the approved visual at the same viewport and record a passing project-root `design-qa.md`

---

## Phase 10: Precision Annotation Foundation

**Purpose**: Materialize the V2 precision schema and deterministic quality gates before accepting any new Ingrid result as ground truth.

- [X] T048 [P] Add failing schema, answer-block, exclusion, continuity and H-PS2 gate tests in `tests/test_precision_annotation_contract.py`
- [X] T049 Implement immutable region, unit, relation and geometry-quality contracts plus payload validation in `modulos/instance_factory/annotation_contracts.py`
- [X] T050 Implement class-specific inclusion/exclusion checks, alternative coverage, warning generation, reciprocal continuity validation and H-PS2 readiness in `modulos/instance_factory/annotation_quality.py`

**Checkpoint**: Precise annotations can be validated without app, database, PDF or training writes.

---

## Phase 11: User Story 2 - Aplicar el gate preciso a la revision (Priority: P1)

**Goal**: Prevent an Ingrid V2 unit with omitted alternatives, foreign content, unsupported continuation or unresolved geometry from reaching H-PS2 readiness.

**Independent Test**: Submit valid and invalid V2 problem/solution annotations to the staging linker and read-only audit endpoint; only the fully passing unit is reported as H-PS2 ready, while legacy V1 artifacts remain readable and non-approved.

- [X] T051 [P] [US2] Add failing precision-gate integration and legacy-compatibility tests in `tests/test_problem_solution_linking.py`
- [X] T052 [US2] Integrate opt-in V2 annotation validation and blocking reasons into `modulos/instance_factory/problem_solution_linking.py`
- [X] T053 [P] [US2] Add failing read-only precision validation and static UI contract tests in `tests/test_problem_detector_lab_server.py`
- [X] T054 [US2] Expose token-safe, non-persistent precision validation responses in `modulos/problem_detector_lab/server.py`
- [X] T055 [US2] Present answer-block completeness, exclusion checks, continuity evidence and H-PS2 readiness in `modulos/problem_detector_lab/web/index.html`, `modulos/problem_detector_lab/web/app.js` and `modulos/problem_detector_lab/web/styles.css`

**Checkpoint**: The audit console explains every precision blocker and performs no canonical or training write.

---

## Phase 12: User Story 5 - Construir ground truth relacional (Priority: P2)

**Goal**: Export only reviewed annotations into an auditable relational release, assign complete documents to exclusive splits and evaluate specialized capability candidates without promoting them.

**Independent Test**: Export a mixed fixture containing approved, rejected, pending and abstained annotations; verify only approved items are training-eligible, all relations remain reconstructible, document derivatives stay in one split, leakage is detected and a model candidate remains blocked when any threshold, critical-error, abstention, approval or rollback gate fails.

- [X] T056 [P] [US5] Add failing relational export and review-eligibility tests in `tests/test_supervised_annotation_export.py`
- [X] T057 [US5] Implement deterministic relational annotation export, manifests, counts and frozen-release fingerprints in `modulos/instance_factory/supervised_annotations.py`
- [X] T058 [P] [US5] Add failing document deduplication, inherited-split and leakage-audit tests in `tests/test_document_split_leakage.py`
- [X] T059 [US5] Implement document-level split manifests and zero-leakage auditing in `modulos/instance_factory/document_splits.py`
- [X] T060 [P] [US5] Add failing IND-MA-01 threshold, family-error, abstention, human-approval and rollback tests in `tests/test_specialized_model_evaluation.py`
- [X] T061 [US5] Implement architecture-agnostic capability evaluation and progressive rollout gates in `modulos/instance_factory/specialized_model_evaluation.py`
- [X] T062 [US5] Add a staging-only validation CLI for annotation releases, split manifests and model-evaluation reports in `scripts/validate_specialized_model_artifacts.py`

**Checkpoint**: Dataset and model reports are reproducible and auditable, but no training, model promotion or canonical data application occurs.

---

## Final Phase: V2 Polish And Validation

- [X] T063 Run the new precision, relational-export, split-leakage and specialized-model test modules and fix regressions in affected files
- [X] T064 Run the complete focused/regression suite from `specs/004-problem-solution-linking/quickstart.md` and verify JavaScript/Python syntax plus `git diff --check`
- [X] T065 Validate a synthetic 20-page manifest shape without dispatching agents or writing canonical data and record the command/result in `specs/004-problem-solution-linking/quickstart.md`
- [X] T066 Update delivered status, remaining real-pilot limits and rollback notes in `specs/004-problem-solution-linking/plan.md`

---

## Phase 13: Auditoria visual pre-H-PS1 en Problem Detector Lab (Priority: P1)

**Goal**: Representar y verificar una revision exacta de mapa V2 antes de H-PS1 sin reutilizar una activacion de Ingrid ni escribir estado canonico.

**Independent Test**: Materializar una sesion valida y otra con hash/revision divergente; la primera muestra paginas, regiones coarse, P/S/R, evidencia y huellas, mientras la segunda queda `visual_audit_blocked`. Ninguna ruta mutante es invocada.

- [X] T067 [P] [US2] Add failing pre-H-PS1 session, hash/revision mismatch, opaque-media and static UI contract tests in `tests/test_problem_detector_lab_server.py`
- [X] T068 [US2] Implement read-only visual-session discovery, exact map validation and token-safe payloads in `modulos/problem_detector_lab/server.py`
- [X] T069 [US2] Add pre-H-PS1 session selection, relation side-by-side review, provisional overlays and session-only marks in `modulos/problem_detector_lab/web/index.html`, `modulos/problem_detector_lab/web/app.js` and `modulos/problem_detector_lab/web/styles.css`
- [X] T070 [P] [US2] Synchronize the visual-audit rule and authority limits in the Euler/Gottfried operating contracts without changing H-PS1 ownership
- [X] T071 [US2] Materialize and validate the four Gottfried visual sessions for `euler-precision-pilot-20p-20260717-r1-phase-b-r1` without modifying the four r0 maps
- [X] T072 [US2] Audit all four sessions in Problem Detector Lab, record exact counts/blockers and leave H-PS1 pending for a later human order

---

## Phase 14: Responsive controls polish (Priority: P1)

**Goal**: Keep the pre-H-PS1 audit controls readable and non-overlapping at desktop, intermediate and narrow viewport widths.

**Independent Test**: Resize the live Biblioteca audit view across the desktop-to-mobile breakpoints; header tabs/metadata, inspector tabs, page controls and the four decision buttons must remain inside their containers with no text overflow, sibling overlap or page-level horizontal overflow.

- [X] T073 [P] [US2] Add a failing responsive-controls contract test in `tests/test_problem_detector_lab_server.py`
- [X] T074 [US2] Implement non-overlapping header metadata, wrapping inspector/page controls and adaptive decision-button grids in `modulos/problem_detector_lab/web/styles.css`
- [X] T075 [US2] Validate the live Biblioteca audit view at desktop, intermediate and narrow widths and record the result in `specs/004-problem-solution-linking/quickstart.md`
- [X] T076 [P] [US2] Add a failing sidebar contract test that prevents the page list from collapsing beneath the relation section at any supported viewport height in `tests/test_problem_detector_lab_server.py`
- [X] T077 [US2] Use one outer sidebar scroll at every height so page and relation rows keep their natural height in `modulos/problem_detector_lab/web/styles.css`
- [X] T078 [US2] Reproduce and validate the reported `1343x874` geometry plus the `2039x1074` desktop control and record the correction in `specs/004-problem-solution-linking/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 has no dependencies.
- Phase 2 depends on the baseline and blocks every user story.
- US1 and US2 can proceed after Phase 2, but US2 consumes the instance structure from US1 for full integration.
- US3 depends on confirmed bundles from US2.
- US4 depends on the persisted linker and bundle paths from US2/US3.
- Final validation depends on all desired stories.
- Phase 8 depends on the implemented contracts from US1-US4 and does not authorize new runtime or canonical writes.
- Phase 10 depends on the synchronized V2 contracts and blocks precision acceptance plus US5 dataset work.
- The Phase 11 US2 increment depends on Phase 10 and remains staging/read-only.
- The Phase 12 US5 increment depends on Phase 10; its split and evaluation tasks can proceed independently after the relational schema is stable.
- V2 final validation depends on Phases 10-12 and does not authorize a real Ingrid lot, training or model promotion.

### Parallel Opportunities

- T003 and T005 touch separate test areas and can run in parallel.
- T007, T008 and T011 can be prepared in parallel before their implementations.
- T014 can be written while US1 UI integration is finalized.
- T019 can be prepared independently once the bundle contract from T004 is stable.
- T023 can be written while promotion integration is under test.
- T048, T051 and T053 touch separate test surfaces and can be prepared in parallel.
- T056, T058 and T060 are independent TDD surfaces once the Phase 10 schema is fixed.

## Implementation Strategy

1. Lock the deterministic domain and staging contracts first.
2. Deliver US1 as the first visible increment without changing detector execution.
3. Deliver US2 as a staging-only review workflow.
4. Enable US3 only after confirmed-bundle validation passes.
5. Close provenance/invalidation in US4 before declaring the feature ready for a real-book pilot.
6. Synchronize Euler, Gottfried and Ingrid only after their handoffs and human gates match the implemented contracts.
7. Materialize the precision contract and H-PS2 blocker before reprocessing any Ingrid scope.
8. Build relational export, document split and evaluation gates before selecting or training a specialized model.

## MVP Scope

The smallest useful scope is Phase 1 + Phase 2 + US1 + US2: page roles and reviewed bundles in staging. The user-requested direct database outcome additionally requires US3, so this implementation run targets US1-US4.
