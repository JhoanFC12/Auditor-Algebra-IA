# Tasks: Fabrica Server-Side With Local Models And Hugging Face OCR

**Input**: Design documents from `specs/002-server-factory-models/`

**Prerequisites**: `plan.md`, `spec.md`, and the completed base server design in
`specs/001-remote-platform/data-model.md`.

**Tests**: This feature requires focused tests for job state, model inventory,
server-safe staging paths, Hugging Face OCR retry behavior, and training
correction persistence.

## Phase 1: Setup

**Purpose**: Inventory current local factory/model behavior before moving it to
server-safe jobs.

- [X] T001 Create server factory inventory document in `docs/server_factory_inventory.md`.
- [X] T002 [P] Add local model path inventory helper in `tools/audit_factory_model_inventory.py`.
- [X] T003 [P] Add staging path inventory helper in `tools/audit_factory_staging_paths.py`.
- [X] T004 Add inventory test coverage in `tests/test_server_factory_inventory.py`.

---

## Phase 2: Foundational

**Purpose**: Shared infrastructure that blocks all server-side factory stories.

- [X] T005 Define server factory environment variables in `docs/server_factory_env.md`.
- [X] T006 Add server storage resolver module in `modulos/instance_factory/server_storage.py`.
- [X] T007 Add server job entity/service skeleton in `modulos/instance_factory/server_jobs.py`.
- [X] T008 Add job persistence tests in `tests/test_instance_factory_server_jobs.py`.
- [X] T009 Add server-safe staging artifact tests in `tests/test_instance_factory_server_storage.py`.
- [X] T010 Wire non-secret model configuration loading in `modulos/instance_factory/model_inventory.py`.

**Checkpoint**: Server storage resolution, job records, and model configuration
can be tested without running OCR or segmentation.

---

## Phase 3: User Story 1 - Process PDFs From The Remote Platform (Priority: P1)

**Goal**: Start problem segmentation from the remote app and persist server-side
staging without local Windows paths.

**Independent Test**: Select a PDF already in server storage, start problem
segmentation, refresh the browser, and verify boxes/staging remain visible.

### Tests for User Story 1

- [X] T011 [P] [US1] Add server segmentation job test in `tests/test_instance_factory_server_segmentation.py`.
- [X] T012 [P] [US1] Add browser-refresh job persistence test in `tests/test_instance_factory_server_jobs.py`.

### Implementation for User Story 1

- [X] T013 [US1] Add page/problem segmentation job runner in `modulos/instance_factory/server_jobs.py`.
- [X] T014 [US1] Store detected page boxes through server storage in `modulos/instance_factory/staging.py`.
- [X] T015 [US1] Add job status API endpoints in `modulos/instance_factory/library_web_server.py`.
- [X] T016 [US1] Update web UI job polling in `modulos/instance_factory/web/app.js`.
- [X] T017 [US1] Document PDF segmentation smoke test in `specs/002-server-factory-models/quickstart.md`.

**Checkpoint**: US1 is complete when problem segmentation works from a
server-side PDF and survives browser refresh.

---

## Phase 4: User Story 2 - Keep OCR On Hugging Face (Priority: P1)

**Goal**: Continue using the trained Hugging Face OCR endpoint from server jobs,
with observable retry/cold-start behavior.

**Independent Test**: Run OCR on one crop through the server job queue and verify
raw OCR is saved in server staging.

### Tests for User Story 2

- [X] T018 [P] [US2] Add Hugging Face OCR retry/backoff tests in `tests/test_hf_ocr_endpoint_manager.py`.
- [X] T019 [P] [US2] Add global active OCR job scale-down test in `tests/test_instance_factory_server_jobs.py`.

### Implementation for User Story 2

- [X] T020 [US2] Add server OCR job runner in `modulos/instance_factory/server_jobs.py`.
- [X] T021 [US2] Keep Hugging Face endpoint controls isolated in `modulos/instance_factory/hf_endpoint_manager.py`.
- [X] T022 [US2] Persist raw OCR outputs in `modulos/instance_factory/staging.py`.
- [X] T023 [US2] Add OCR job progress and error API output in `modulos/instance_factory/library_web_server.py`.
- [X] T024 [US2] Update OCR queue UI polling in `modulos/instance_factory/web/app.js`.

**Checkpoint**: US2 is complete when OCR jobs survive refresh, handle 503/cold
start visibly, and never scale down the endpoint while another OCR job is active.

---

## Phase 5: User Story 3 - Run Existing Local Models On The Server (Priority: P1)

**Goal**: Configure existing local segmentation models on the server and report
their availability clearly.

**Independent Test**: Configure model paths, run a sample instance, and verify
problem segmentation, number/alternative detection, and graph segmentation use
the configured server models.

### Tests for User Story 3

- [X] T025 [P] [US3] Add model inventory health tests in `tests/test_instance_factory_model_inventory.py`.
- [X] T026 [P] [US3] Add missing model error tests in `tests/test_instance_factory_model_inventory.py`.

### Implementation for User Story 3

- [X] T027 [US3] Add server model inventory output in `modulos/instance_factory/model_inventory.py`.
- [X] T028 [US3] Route problem detector model selection through inventory in `modulos/instance_factory/pipeline.py`.
- [X] T029 [US3] Route number/alternative detector model selection through inventory in `modulos/instance_factory/pipeline.py`.
- [X] T030 [US3] Route graph segmentation model selection through inventory in `modulos/instance_factory/pipeline.py`.
- [X] T031 [US3] Show configured/missing model status in `modulos/instance_factory/web/app.js`.

**Checkpoint**: US3 is complete when missing model configuration is actionable
and all configured models run from server paths.

---

## Phase 6: User Story 4 - Preserve Human Review And Training Data (Priority: P2)

**Goal**: Corrections made remotely must remain usable for future model training.

**Independent Test**: Correct one box, one raw OCR result, and one graph segment;
verify each correction lands in the correct training bank.

### Tests for User Story 4

- [X] T032 [P] [US4] Add training correction persistence tests in `tests/test_instance_factory_training_corrections.py`.
- [X] T033 [P] [US4] Add stale artifact invalidation tests in `tests/test_instance_factory_stale_artifacts.py`.

### Implementation for User Story 4

- [X] T034 [US4] Persist problem detector corrections in `modulos/instance_factory/training_bank.py`.
- [X] T035 [US4] Persist raw OCR corrections in `modulos/instance_factory/training_bank.py`.
- [X] T036 [US4] Persist graph segmentation corrections in `modulos/instance_factory/training_bank.py`.
- [X] T037 [US4] Mark downstream crops/OCR/segments stale after box changes in `modulos/instance_factory/pipeline.py`.
- [X] T038 [US4] Show stale artifact warnings in `modulos/instance_factory/web/app.js`.

**Checkpoint**: US4 is complete when human corrections are training data and
upstream changes cannot silently reuse stale downstream artifacts.

---

## Phase 7: User Story 5 - Defer Agent Automation (Priority: P3)

**Goal**: Keep autonomous agents out of this implementation while preserving the
future roadmap.

**Independent Test**: Confirm no required task or acceptance path depends on an
autonomous agent.

### Implementation for User Story 5

- [X] T039 [US5] Document deferred agents in `docs/server_factory_agents_future.md`.
- [X] T040 [US5] Add a guard note to `specs/002-server-factory-models/quickstart.md` stating agents are out of scope.

**Checkpoint**: US5 is complete when the future agent plan is documented without
blocking server-side factory work.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Validate the feature end to end and harden operations.

- [X] T041 [P] Add server factory operations guide in `docs/server_factory_operations.md`.
- [X] T042 [P] Add security checklist for tokens, model paths, and API outputs in `docs/server_factory_security.md`.
- [X] T043 Run focused test suite and record output in `docs/server_factory_validation.md`.
- [X] T044 Validate `specs/002-server-factory-models/quickstart.md` against a small sample instance.
- [X] T045 Update Obsidian project note `C:\Users\Danny Fabián\Documents\Obsidian Vault\02 Proyectos\Auditor-IA\2026-07-06 - Contexto remoto y Spec Kit.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup (Phase 1): no dependencies.
- Foundational (Phase 2): depends on Setup.
- User Stories (Phases 3-7): depend on Foundational.
- Polish (Phase 8): depends on the desired completed user stories.

### User Story Dependencies

- US1, US2, and US3 are all P1 and can be implemented after Foundational.
- US4 depends on the server staging/job behavior from US1-US3.
- US5 can be completed independently as documentation, but must not block US1.

### Parallel Opportunities

- T002 and T003 can run in parallel.
- T011 and T012 can run in parallel.
- T018 and T019 can run in parallel.
- T025 and T026 can run in parallel.
- T032 and T033 can run in parallel.
- Documentation tasks T039, T041, and T042 can run in parallel after the main
  implementation direction is stable.

## Implementation Strategy

### MVP First

1. Complete Phase 1 and Phase 2.
2. Complete US1, US2, and US3 as the minimal operational server factory.
3. Validate one small PDF instance from server storage through segmentation and
   OCR.
4. Only then implement US4 training correction persistence and stale artifact
   rules.

### Incremental Delivery

1. Inventory and storage resolver.
2. Job skeleton and persisted status.
3. Problem segmentation job.
4. Hugging Face OCR job.
5. Server model inventory.
6. Human correction/training bank persistence.

## Notes

- Agents are future scope and must not appear as required implementation tasks.
- OCR remains Hugging Face in this feature.
- Server-local models must not depend on Windows paths.
- Every job must remain observable after browser refresh.
