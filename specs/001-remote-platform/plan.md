# Implementation Plan: Remote Platform With Central Database

**Branch**: `001-remote-platform` | **Date**: 2026-07-06 | **Spec**: `specs/001-remote-platform/spec.md`

**Input**: Feature specification from `specs/001-remote-platform/spec.md`

## Summary

Move the operating model from local-only execution to a remote platform served
from `nexumathjf.com`, backed by server PostgreSQL and server storage. The first
implementation slice should not attempt to move every OCR/model workflow to the
server. It should establish the central database, storage migration, domain
routes, backups, and remote Word/library flows. The PDF Factory/OCR pipeline can
continue as a controlled local or worker process until the remote job system is
explicitly implemented.

## Technical Context

**Language/Version**: Python 3.11+ for `Auditor-IA` and `scan-math-db`;
PowerShell for Windows operations; Bash for Linux server setup.

**Primary Dependencies**: FastAPI stack in `scan-math-db`, PostgreSQL drivers,
existing `Auditor-IA` factory modules, Cloudflare Tunnel or reverse proxy,
existing Word/LaTeX conversion tooling.

**Storage**: PostgreSQL server database plus server filesystem storage under a
stable root such as `/srv/mathcontentstudio`.

**Testing**: Python `unittest` suites already present in both projects; focused
smoke scripts for server health, database counts, storage existence, and Word
generation.

**Target Platform**: Linux server for production; Windows local machine remains
available for development, controlled sync, and heavy model workflows.

**Project Type**: Web/API platform plus internal desktop/local processing tools.

**Performance Goals**: Remote pages should load from server data without waiting
on local PC services. Long-running OCR, migration, and Word generation must be
job-based or otherwise observable.

**Constraints**:

- PostgreSQL must not be open directly to the Internet.
- No committed secrets.
- No public Windows paths in API responses.
- No uncontrolled double-writing between local mirror and server database.
- Migration must be backed up and validated before declaring server as official.

**Scale/Scope**: Initial scope is one owner/operator plus future student/teacher
web access. Data scope includes current math bank, books, instances, PDFs,
covers, generated Word outputs, and future training/correction artifacts.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Remote source of truth: PASS if server PostgreSQL and storage are explicitly
  selected as production sources.
- Data safety: PASS only with backup, dry-run/report, restore validation, and
  rollback procedure.
- Spec-first: PASS because this feature is tracked under `specs/001-remote-platform`.
- Public/internal boundary: PASS only if public web/API does not expose internal
  model secrets, local filesystem paths, or private factory state.
- Observable workflows: PASS only if long-running operations report status,
  logs, counters, and failure reasons.

## Project Structure

### Documentation (this feature)

```text
specs/001-remote-platform/
|-- spec.md
|-- plan.md
|-- research.md          # to create during migration research
|-- data-model.md        # to create before schema/storage changes
|-- quickstart.md        # to create before deployment
|-- contracts/           # API contracts for remote jobs and migration health
`-- tasks.md             # to create after plan is accepted
```

### Source Code (repository roots)

```text
E:/Github/Auditor-IA/
|-- docs/
|-- database/
|-- modulos/instance_factory/
|-- modulos/modulo6_practicas/
|-- scripts/
|-- tools/
|-- tests/
`-- specs/001-remote-platform/

E:/Github/MathContentStudio/scan-math-db/
|-- app/
|-- scripts/
|-- docs/
|-- tests/
|-- storage/
`-- docker-compose.yml
```

**Structure Decision**: Keep `scan-math-db` as the public web/API deployment
foundation and keep `Auditor-IA` as the internal factory/training/production
tooling. Remote deployment work must bridge the two through explicit database,
storage, and job contracts instead of mixing all code into one app.

## Implementation Phases

### Phase 0 - Inventory And Risk Report

- Identify current source databases.
- Count critical rows: problems, books, instances, origins, Word outputs.
- Detect Windows-only paths stored in tables.
- Detect missing PDFs, covers, images, and generated artifacts.
- Produce `docs/reporte_pre_migracion_servidor.md`.

### Phase 1 - Server Database And Storage Design

- Decide production database URL and roles.
- Decide server storage root.
- Define path rewrite rules.
- Define backup and restore validation.
- Define local mirror behavior.

### Phase 2 - Migration Tooling

- Use existing `scan-math-db` migration scripts when possible.
- Add missing audit or validation scripts in the correct repo.
- Validate migration on a test database before production.

### Phase 3 - Domain Deployment

- Deploy web/API.
- Route `nexumathjf.com`, `studio`, `aula`, and `api`.
- Validate auth, health, CORS, and static assets.

### Phase 4 - Remote Workflows

- Validate remote library browsing.
- Validate Word generation and download.
- Validate problem filters.
- Validate backups.

### Phase 5 - Factory/OCR Remote Strategy

- Decide whether OCR/segmentation stays local-sync or becomes server jobs.
- If server jobs are needed, create a separate Spec Kit feature.

## Complexity Tracking

No constitution violations are accepted for the first slice. If moving the full
PDF Factory/OCR stack to the server becomes necessary, that must be justified in
a separate spec because it introduces model costs, worker lifecycle, storage
volume, and security constraints.

