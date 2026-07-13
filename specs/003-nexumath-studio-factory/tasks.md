# Tasks: NexumathJF Studio Factory

**Input**: Design documents from `/specs/003-nexumath-studio-factory/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Organization**: Tasks are grouped by user story to preserve independently testable increments.

## Phase 1: Setup And Inventory

**Purpose**: Establish the implementation map before changing the public Studio experience.

- [X] T001 Create the Nexumath Studio compatibility auditor in `tools/audit_nexumath_studio_factory.py`
- [X] T002 Add unit coverage for the compatibility auditor in `tests/test_nexumath_studio_factory_audit.py`
- [X] T003 Generate the first compatibility report in `docs/reporte_nexumath_studio_factory_compatibilidad.md`
- [X] T004 Add machine-readable audit output under `tmp/nexumath_studio_factory_audit/audit.json`
- [X] T005 Document the current public Studio route inventory in `docs/reporte_nexumath_studio_factory_compatibilidad.md`
- [X] T006 Document the current Biblioteca/Fabrica API inventory in `docs/reporte_nexumath_studio_factory_compatibilidad.md`

---

## Phase 2: Foundational Server Contracts

**Purpose**: Shared contracts and safety checks required before user-story work.

- [X] T007 Define shared factory settings for `scan-math-db` in `E:/Github/MathContentStudio/scan-math-db/app/core/config.py`
- [X] T008 Add server storage validation helpers for factory assets in `E:/Github/MathContentStudio/scan-math-db/app/factory_storage.py`
- [X] T009 Add public-safe error serialization for factory routes in `E:/Github/MathContentStudio/scan-math-db/app/factory_errors.py`
- [X] T010 Add route contract tests for safe factory errors in `E:/Github/MathContentStudio/scan-math-db/tests/test_factory_error_contract.py`
- [X] T011 Add model readiness DTOs for Studio Factory in `E:/Github/MathContentStudio/scan-math-db/app/schemas.py`
- [X] T012 Add job status DTOs for Studio Factory in `E:/Github/MathContentStudio/scan-math-db/app/schemas.py`
- [X] T013 Add migration/cutover DTOs for Studio Factory in `E:/Github/MathContentStudio/scan-math-db/app/schemas.py`
- [X] T014 Add server-path redaction tests in `E:/Github/MathContentStudio/scan-math-db/tests/test_factory_public_safety.py`

**Checkpoint**: Public contracts and safety primitives exist; no Studio replacement is active yet.

---

## Phase 3: User Story 1 - Enter Studio From Anywhere (Priority: P1) MVP

**Goal**: Make the authenticated Studio entry resolve to the Biblioteca/Fabrica shell.

**Independent Test**: Open `/studio`, authenticate, and reach the Biblioteca/Fabrica home surface with database, storage, model, OCR, and job health visible.

- [X] T015 [US1] Add factory bootstrap service in `E:/Github/MathContentStudio/scan-math-db/app/factory_bootstrap.py`
- [X] T016 [US1] Add `GET /studio/factory/bootstrap` in `E:/Github/MathContentStudio/scan-math-db/app/api/studio_factory.py`
- [X] T017 [US1] Include `studio_factory` router in `E:/Github/MathContentStudio/scan-math-db/app/main.py`
- [X] T018 [US1] Replace Studio dashboard entry with factory shell link in `E:/Github/MathContentStudio/scan-math-db/app/web/studio-dashboard.html`
- [X] T019 [US1] Create factory shell page in `E:/Github/MathContentStudio/scan-math-db/app/web/studio-factory.html`
- [X] T020 [US1] Create factory shell script in `E:/Github/MathContentStudio/scan-math-db/app/web/studio-factory.js`
- [X] T021 [US1] Add factory shell styles in `E:/Github/MathContentStudio/scan-math-db/app/web/styles.css`
- [X] T022 [US1] Add bootstrap route tests in `E:/Github/MathContentStudio/scan-math-db/tests/test_studio_factory_bootstrap.py`

**Checkpoint**: MVP Studio entry works without local desktop launcher.

---

## Phase 4: User Story 2 - Work Books And Instances Remotely (Priority: P1)

**Goal**: Browse and manage books/instances remotely with server-safe asset references.

**Independent Test**: Open a book, view instances, see stage counts, and verify assets are server-managed or flagged for migration.

- [X] T023 [US2] Add factory library service adapter in `E:/Github/MathContentStudio/scan-math-db/app/factory_library.py`
- [X] T024 [US2] Add `GET /studio/factory/books` in `E:/Github/MathContentStudio/scan-math-db/app/api/studio_factory.py`
- [X] T025 [US2] Add `GET /studio/factory/books/{book_id}/instances` in `E:/Github/MathContentStudio/scan-math-db/app/api/studio_factory.py`
- [X] T026 [US2] Add server-safe cover/PDF asset references in `E:/Github/MathContentStudio/scan-math-db/app/factory_library.py`
- [X] T027 [US2] Render remote book cards in `E:/Github/MathContentStudio/scan-math-db/app/web/studio-factory.js`
- [X] T028 [US2] Render remote instance list and stage pills in `E:/Github/MathContentStudio/scan-math-db/app/web/studio-factory.js`
- [X] T029 [US2] Add book/instance route tests in `E:/Github/MathContentStudio/scan-math-db/tests/test_studio_factory_library.py`
- [X] T030 [US2] Add migration warnings for local-only asset paths in `E:/Github/MathContentStudio/scan-math-db/app/factory_library.py`

**Checkpoint**: Books and instances are visible from the domain-ready Studio surface.

---

## Phase 5: User Story 3 - Run OCR And Model Jobs Without Losing Progress (Priority: P1)

**Goal**: Start and recover long-running server jobs for model and OCR work.

**Independent Test**: Start a job, refresh the browser, and recover progress, item status, and errors.

- [X] T031 [US3] Add factory job persistence model or adapter in `E:/Github/MathContentStudio/scan-math-db/app/factory_jobs.py`
- [X] T032 [US3] Add `POST /studio/factory/instances/{instance_id}/jobs` in `E:/Github/MathContentStudio/scan-math-db/app/api/studio_factory.py`
- [X] T033 [US3] Add `GET /studio/factory/jobs/{job_id}` in `E:/Github/MathContentStudio/scan-math-db/app/api/studio_factory.py`
- [X] T034 [US3] Integrate OCR endpoint status into job bootstrap in `E:/Github/MathContentStudio/scan-math-db/app/factory_bootstrap.py`
- [X] T035 [US3] Integrate server model readiness into job bootstrap in `E:/Github/MathContentStudio/scan-math-db/app/factory_bootstrap.py`
- [X] T036 [US3] Add refresh-safe job polling in `E:/Github/MathContentStudio/scan-math-db/app/web/studio-factory.js`
- [X] T037 [US3] Add job lifecycle tests in `E:/Github/MathContentStudio/scan-math-db/tests/test_studio_factory_jobs.py`
- [X] T038 [US3] Add public-safe itemized job error tests in `E:/Github/MathContentStudio/scan-math-db/tests/test_studio_factory_jobs.py`

**Checkpoint**: Model/OCR jobs are observable and restart-safe.

---

## Phase 6: User Story 4 - Promote Reviewed Problems And Generate Word From Server Data (Priority: P2)

**Goal**: Promote reviewed server data and generate Word outputs from official records.

**Independent Test**: Promote one reviewed instance and generate a downloadable Word document from that instance.

- [X] T039 [US4] Add review save endpoint `POST /studio/factory/records/{record_id}/review` in `E:/Github/MathContentStudio/scan-math-db/app/api/studio_factory.py`
- [X] T040 [US4] Add promotion preflight service in `E:/Github/MathContentStudio/scan-math-db/app/factory_promotion.py`
- [X] T041 [US4] Add promotion job type in `E:/Github/MathContentStudio/scan-math-db/app/factory_jobs.py`
- [X] T042 [US4] Add Word selection persistence service in `E:/Github/MathContentStudio/scan-math-db/app/factory_word.py`
- [X] T043 [US4] Add `POST /studio/factory/word/selection` in `E:/Github/MathContentStudio/scan-math-db/app/api/studio_factory.py`
- [X] T044 [US4] Add `POST /studio/factory/word/generate` in `E:/Github/MathContentStudio/scan-math-db/app/api/studio_factory.py`
- [X] T045 [US4] Add Word generation UI controls in `E:/Github/MathContentStudio/scan-math-db/app/web/studio-factory.js`
- [X] T046 [US4] Add promotion and Word generation tests in `E:/Github/MathContentStudio/scan-math-db/tests/test_studio_factory_word.py`

**Checkpoint**: Reviewed content can produce official database records and Word outputs from the remote workflow.

### Phase 6B: User Story 4 Closure - Real Word Artifacts

**Purpose**: Close the gap between a recoverable Word job record and a real downloadable Word artifact generated from server-side data.

- [X] T064 [US4] Add real Word job worker in `E:/Github/MathContentStudio/scan-math-db/app/factory_word.py` or a dedicated worker module
- [X] T065 [US4] Build `.tex` and `.docx` artifacts from official math-bank records, not from local generated files
- [X] T066 [US4] Store Word outputs under server-managed factory storage with public-safe artifact references
- [X] T067 [US4] Add `GET /studio/factory/word/jobs/{job_id}/download` for generated artifacts
- [X] T068 [US4] Show `Abrir/Descargar Word` only when the job has a completed artifact
- [X] T069 [US4] Add tests proving Word generation returns a downloadable `.docx` and no private Windows path leaks

**Checkpoint**: A promoted or selected set of problems produces a real downloadable Word file from the remote workflow.

---

## Phase 7: User Story 5 - Preserve Training Corrections For Future Models (Priority: P2)

**Goal**: Keep human corrections as model training data.

**Independent Test**: Save corrections for boxes, OCR, graph segments, and final format and verify training records include source and model version.

- [X] T047 [US5] Add training correction contract adapter in `E:/Github/MathContentStudio/scan-math-db/app/factory_training.py`
- [X] T048 [US5] Persist OCR correction metadata from review saves in `E:/Github/MathContentStudio/scan-math-db/app/factory_training.py`
- [X] T049 [US5] Persist detector correction metadata from box saves in `E:/Github/MathContentStudio/scan-math-db/app/factory_training.py`
- [X] T050 [US5] Persist graph segment correction metadata in `E:/Github/MathContentStudio/scan-math-db/app/factory_training.py`
- [X] T051 [US5] Show training counters in `E:/Github/MathContentStudio/scan-math-db/app/web/studio-factory.js`
- [X] T052 [US5] Add training correction tests in `E:/Github/MathContentStudio/scan-math-db/tests/test_studio_factory_training.py`

**Checkpoint**: Corrections are captured but models are not automatically promoted.

---

## Phase 8: User Story 6 - Keep Local PC As Backup Only (Priority: P3)

**Goal**: Preserve local fallback without uncontrolled double-writing.

**Independent Test**: Run local sync/backup mode and verify it is explicit, reportable, and separate from official server writes.

- [X] T053 [US6] Add local fallback mode documentation in `docs/plan_despliegue_remoto_dominio.md`
- [X] T054 [US6] Add cutover status indicator in `E:/Github/MathContentStudio/scan-math-db/app/factory_bootstrap.py`
- [X] T055 [US6] Add local-write warning in `E:/Github/MathContentStudio/scan-math-db/app/web/studio-factory.js`
- [X] T056 [US6] Add sync/rollback smoke checklist in `specs/003-nexumath-studio-factory/quickstart.md`
- [X] T057 [US6] Add cutover gate validation test in `E:/Github/MathContentStudio/scan-math-db/tests/test_studio_factory_cutover.py`

**Checkpoint**: Local tools are fallback/sync tools, not silent official writers.

---

## Final Phase: Polish And Validation

- [X] T058 Run `python -m unittest tests.test_nexumath_studio_factory_audit` from `E:/Github/Auditor-IA`
- [X] T059 Run `python -m unittest` for the new `scan-math-db` factory tests from `E:/Github/MathContentStudio/scan-math-db`
- [X] T060 Execute `specs/003-nexumath-studio-factory/quickstart.md` local smoke steps
- [X] T061 Update `docs/plan_despliegue_remoto_dominio.md` with implementation status and remaining risks
- [X] T062 Update Obsidian project note with implementation status and next action
- [X] T063 Verify no public API sample response exposes local Windows paths or secrets
- [X] T072 Add production compose template in `E:/Github/MathContentStudio/scan-math-db/docker-compose.production.yml`
- [X] T073 Add production environment template in `E:/Github/MathContentStudio/scan-math-db/.env.production.example`
- [X] T074 Add production deploy guardrail script in `E:/Github/MathContentStudio/scan-math-db/scripts/test_production_deploy_config.ps1`
- [X] T075 Document production deploy package in `E:/Github/MathContentStudio/scan-math-db/docs/nexumathjf-production-deploy.md`
- [X] T076 Add clean release bundle builder in `E:/Github/MathContentStudio/scan-math-db/scripts/build_nexumath_studio_release.ps1`
- [X] T077 Add cutover evidence packet builder in `E:/Github/MathContentStudio/scan-math-db/scripts/build_factory_cutover_packet.ps1`
- [X] T078 Harden strict cutover readiness so post-migration target counts, validation status, path rewrites and rollback evidence are required in `E:/Github/MathContentStudio/scan-math-db/scripts/test_factory_cutover_readiness.ps1`
- [X] T079 Add remote release deploy helper in `E:/Github/MathContentStudio/scan-math-db/scripts/deploy_nexumath_studio_release.ps1`
- [X] T080 Add end-to-end cutover runner in `E:/Github/MathContentStudio/scan-math-db/scripts/run_nexumath_studio_cutover.ps1`
- [X] T081 Add/update reverse proxy or Cloudflare tunnel template so `/studio`, `/studio/factory/bootstrap`, `studio.nexumathjf.com`, `api.nexumathjf.com`, and `aula.nexumathjf.com` route to the correct service
- [X] T082 Add a public domain routing verifier that fails on Studio 404 and writes a diagnostics report under `storage/diagnostics`
- [X] T089 Integrate the public domain routing gate into `scripts/run_nexumath_studio_cutover.ps1` before authenticated remote smoke
- [X] T090 Add SSH server deploy readiness verifier for release files, production env, Docker, storage, model root and local health
- [X] T091 Add reproducible server deploy handoff packet builder with checksums, required env checklist and ordered server steps
- [X] T092 Add idempotent Linux server preparation helper for `.env.production`, storage/model directories and compose validation
- [X] T093 Add math-bank bundle readiness verifier for migration manifest, counts, PDFs, covers and portable server paths
- [X] T094 Add server-side math-bank restore helper with pre-restore backup, Docker Compose restore and migration report
- [X] T095 Add reproducible SSH/SCP math-bank bundle upload helper before server restore
- [X] T096 Integrate bundle upload and server restore dry-run/restore flags into `E:/Github/MathContentStudio/scan-math-db/scripts/run_nexumath_studio_cutover.ps1`
- [X] T097 Add local deploy env example and prereq verifier for SSH/release/bundle readiness before server execution
- [X] T098 Let the math-bank bundle deploy helper reuse `.env.deploy.local` for SSH settings and update handoff docs
- [X] T099 Add SSH/Caddy routing apply helper and include it in the deploy handoff before public domain validation
- [X] T100 Integrate explicit `-ApplyDomainRouting` into the cutover runner so deploy, Caddy routing and public route validation can run in one ordered flow
- [X] T101 Harden deploy prereq verifier so it inspects release ZIP contents, required deploy/routing helpers and sensitive env exclusions before SSH
- [X] T102 Harden server readiness gate so it validates deployed Linux helper scripts with `bash -n` and warns when Caddy is unavailable before public routing
- [X] T103 Add a safe deploy environment initializer for `.env.deploy.local` so real SSH deployment starts from a validated local config
- [X] T104 Add a dedicated SSH key setup helper that registers `NEXUMATH_IDENTITY_FILE` without exposing private keys
- [X] T105 Separate public Cloudflare domains from the real SSH host using `NEXUMATH_SSH_HOST`/`-SshHost` in deploy helpers, docs and routing contracts
- [X] T106 Add a safe deploy target setter that writes the real SSH host/user into `.env.deploy.local`, preserves Studio smoke credentials and validates against Cloudflare-domain misuse
- [X] T107 Add an SSH access request artifact to the server deploy handoff with the public key, provider checklist and exact preflight command
- [X] T108 Add a portable deploy handoff archive builder that rejects `.env`, private keys and common token patterns before creating a provider-safe ZIP
- [X] T109 Add an independent handoff archive verifier that reopens the ZIP, checks required entries, validates the summary hash and scans for secret patterns
- [X] T110 Add a non-destructive SSH host candidate diagnostic proving public Nexumath domains are Cloudflare HTTP routes, not deploy SSH targets
- [X] T083 Deploy the clean `nexumath_studio_smoke.zip` release to the server with `.env.production` preserved outside git
- [X] T084 Configure production variables for database, storage, model root, OCR Hugging Face endpoint, CORS and Studio admin credentials
  - 2026-07-07 evidence: database, math-bank database, storage root, Factory storage/job/model roots, OCR Hugging Face endpoint, HF tokens and CORS are configured on `3.225.19.0`; authenticated smoke reports `database=ok`, `storage=ok`, `models=ready`, `ocr_endpoint=configured`. One active admin exists in DB, but bootstrap admin env vars remain intentionally empty pending credential policy.
  - 2026-07-07 evidence: credential policy finalized in `E:\Github\Auditor-IA\docs\studio_admin_credentials_policy.md`: permanent Studio admin credentials are DB-managed, not kept as persistent bootstrap env secrets. Remote DB verification found `admins=1`, `active=True`, `password_login=True`, `email_verified=True`; `SCAN_MATH_DB_CORS_ORIGINS_RAW`, `SCAN_MATH_DB_FACTORY_HF_OCR_ENDPOINT_NAME`, `SCAN_MATH_DB_FACTORY_HF_OCR_BASE_URL`, `HF_TOKEN`, server storage roots and official cutover flags are present without exposing secret values.
- [X] T085 Run the public domain routing gate and confirm `/studio` and `/studio/factory/bootstrap` are no longer 404
- [X] T086 Restore/migrate PostgreSQL math-bank data and server assets into `/srv/mathcontentstudio`
  - 2026-07-07 evidence: server `mathcontentstudio` DB has `70` books, `430` instances and `6415` problems; `/srv/mathcontentstudio/library` contains `70` PDFs and `41` covers. A PostgreSQL backup was created before metadata backfill at `/srv/mathcontentstudio/backups/mathcontentstudio_before_artifact_backfill_20260707_131946.dump`. Local Windows artifact references were preserved in `libro_artifacts_locales` and `instancia_artifacts_locales`; Factory payload validation returned `70` books and did not expose `E:\` or `D:\` paths.
- [X] T087 Run authenticated Studio Factory smoke: login, library, instance, job recovery, review/promotion and Word download
  - 2026-07-07 evidence: authenticated password smoke passed for bootstrap and library; Word generation smoke for instance `4912` completed with `job_status=done`, `6` problems, DOCX/TEX/manifest downloads verified, and smoke artifacts were cleaned.
  - 2026-07-07 evidence: authenticated smoke also saved a synthetic review with training correction, created a non-destructive `promotion` job, restarted `nexumathjf-aula.service`, recovered the queued job through the API after restart, and cleaned temporary user/job/review/training artifacts.
- [X] T088 Declare server source of truth only after strict readiness passes with backup, rollback, assets and counts verified
  - 2026-07-07 evidence: after strict readiness passed with `36` checks, `0` failures and `0` warnings, `SCAN_MATH_DB_FACTORY_OFFICIAL_SOURCE=true` was enabled on `3.225.19.0`; public authenticated bootstrap now reports `mode=official_server`, `server_is_source_of_truth=true`, `local_pc_role=backup_only`, `local_writes_allowed=false`, backup/rollback/assets verified and next action `Operar desde Studio remoto y usar la PC local solo como respaldo controlado.`
- [X] T070 Run `scripts/test_remote_studio_factory.ps1` after deploy against `nexumathjf.com`: login, library, instance, job recovery, review and Word download
  - 2026-07-07 evidence: `scripts/test_remote_studio_factory.ps1` passed against `https://nexumathjf.com` using a temporary admin. It verified health, login, bootstrap, library, instances, snapshot, recoverable job status, Word generation for `30` problems and DOCX download. Temporary user, auth tokens, `2` smoke jobs and `1` Word output directory were cleaned. Report: `E:\Github\Auditor-IA\storage\diagnostics\remote_studio_factory_nexumathjf_20260707_live.json`.
- [X] T071 Run `scripts/test_factory_cutover_readiness.ps1 -Strict` with backup, migration manifest and rollback plan before declaring server source of truth
  - 2026-07-07 evidence: strict readiness passed against `https://nexumathjf.com` with backup ref `/srv/mathcontentstudio/backups/mathcontentstudio_cutover_ready_20260707_133940.dump`, manifest `E:\Github\Auditor-IA\storage\cutover\migration_server_verified_20260707\migration-manifest.json`, rollback plan `E:\Github\Auditor-IA\storage\cutover\migration_server_verified_20260707\rollback-plan.md`, report `E:\Github\Auditor-IA\storage\diagnostics\factory_cutover_readiness_strict_20260707.json`, and temporary admin cleanup completed.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1** has no dependencies.
- **Phase 2** depends on Phase 1 inventory.
- **US1, US2, US3** depend on Phase 2 contracts.
- **US4** depends on US2 and US3.
- **US5** depends on the review/save paths from US4 and the correction sources from US2/US3.
- **US6** can begin after Phase 2, but final validation depends on US1-US4.
- **Final validation** depends on the desired story set.

### MVP Scope

MVP is Phase 1 + Phase 2 + US1:

1. Compatibility report exists.
2. Public-safe contracts exist.
3. Studio entry opens the Biblioteca/Fabrica shell with health/status.

### Parallel Opportunities

- T001 and T002 can be split after the auditor interface is sketched.
- T007-T014 can run in parallel by file area after Phase 1.
- US2 UI tasks can run in parallel with US2 service tests once endpoint payloads are stable.
- US3 job UI polling can run in parallel with backend job lifecycle tests.
- US4 Word selection UI can run in parallel with backend Word service work after contracts are fixed.

## Implementation Strategy

1. Complete Phase 1 and review the compatibility report.
2. Complete Phase 2 without replacing public routes yet.
3. Deliver US1 as MVP and validate from local Studio.
4. Add US2 and US3 before any real remote cutover.
5. Add US4 for promotion and Word generation.
6. Add US5 and US6 safety/training/fallback work.
7. Run quickstart and only then plan server deployment/cutover.
