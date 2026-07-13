# Tasks: Remote Platform With Central Database

**Input**: `specs/001-remote-platform/spec.md` and `specs/001-remote-platform/plan.md`

## Phase 0 - Pre-Migration Audit

- [X] T001 Create a read-only migration readiness audit tool for the local math bank.
- [X] T002 Count critical database tables: problems, books, instances, files, origins, and link tables when present.
- [X] T003 Detect Windows-only paths stored in database columns.
- [X] T004 Check referenced PDF, cover, and generated asset file existence.
- [X] T005 Detect server-unsafe paths that must be rewritten before migration.
- [X] T006 Generate JSON and Markdown reports without modifying the database.
- [X] T007 Add focused tests for path classification, report rendering, and missing-file summaries.
- [X] T008 Document how to run the audit before any server migration.

## Phase 1 - Server Database And Storage Design

- [X] T009 Define production PostgreSQL roles and database names.
- [X] T010 Define server storage root and subfolders.
- [X] T011 Define Windows-to-server path rewrite rules.
- [X] T012 Define backup and rollback procedure.
- [X] T013 Define local mirror behavior after the server becomes official.

## Phase 2 - Migration Tooling

- [ ] T014 Reuse or extend existing `scan-math-db` bundle export scripts.
- [ ] T015 Validate exported bundles against the audit report.
- [ ] T016 Restore to a test PostgreSQL database before production.
- [ ] T017 Validate restored row counts and server-side asset paths.

## Phase 3 - Domain Deployment

- [ ] T018 Configure the server web/API runtime.
- [ ] T019 Route `nexumathjf.com`, `studio`, `aula`, and `api`.
- [ ] T020 Validate `/health`, CORS, authentication, static assets, and no secret leakage.

## Phase 4 - Remote Workflows

- [ ] T021 Validate remote library browsing.
- [ ] T022 Validate problem filters from the server database.
- [ ] T023 Validate Word generation and download from server storage.
- [ ] T024 Validate backup and restore from a server snapshot.

## Out Of Scope For This Feature

- Moving all OCR/model processing to the server.
- Autonomous agents.
- Replacing the Hugging Face OCR endpoint.
- Redesigning the normalizer.
