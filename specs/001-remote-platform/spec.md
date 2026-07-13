# Feature Specification: Remote Platform With Central Database

**Feature Branch**: `001-remote-platform`

**Created**: 2026-07-06

**Status**: Draft

**Input**: User description: "Dejar de trabajar solo en local; poder trabajar desde cualquier parte mediante el dominio `nexumathjf.com` y subir toda la base de datos al servidor."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Access The Platform From The Domain (Priority: P1)

As the owner/operator, I can open the platform from `nexumathjf.com` and the
related subdomains without depending on the local PC browser or localhost.

**Why this priority**: Remote access is the main objective. Without this, all
other improvements remain local-only.

**Independent Test**: From a device outside the local network, open the public
domain, authenticate, and access the main web surfaces.

**Acceptance Scenarios**:

1. **Given** the server is running, **When** I open `https://nexumathjf.com`,
   **Then** I see the workflow entry page.
2. **Given** the server is running, **When** I open `studio.nexumathjf.com`,
   **Then** I can access the content/studio interface.
3. **Given** the server is running, **When** I open `api.nexumathjf.com/health`,
   **Then** I receive a healthy API response without exposing secrets.

---

### User Story 2 - Use A Central Math Database (Priority: P1)

As the operator, I can use the server PostgreSQL database as the official math
bank so that books, instances, problems, origins, PDFs, covers, and generated
outputs are not trapped on a single local machine.

**Why this priority**: Remote web access is not useful if the real data remains
only in the local mirror.

**Independent Test**: Query the server database from the deployed API and verify
counts for books, instances, problems, and origins match the migration report.

**Acceptance Scenarios**:

1. **Given** a validated local export, **When** it is restored on the server,
   **Then** the expected counts match the pre-migration report.
2. **Given** a book has PDF and cover assets, **When** it is viewed remotely,
   **Then** the server serves the assets from server storage, not from Windows
   local paths.
3. **Given** the server database is official, **When** local tools need access,
   **Then** they use a controlled sync/tunnel workflow and do not create
   uncontrolled double writes.

---

### User Story 3 - Generate Word Documents Remotely (Priority: P2)

As the operator, I can generate and download Word documents from the web using
server-side data and server-side storage.

**Why this priority**: Word generation is one of the concrete outputs that must
work from anywhere.

**Independent Test**: From the remote UI, select a book/session/filter, generate
a Word file, download it, and verify it references the expected problems and
images.

**Acceptance Scenarios**:

1. **Given** a book has instances already in the server database, **When** I
   request "Word completo", **Then** the server generates a `.docx` in the
   configured output storage.
2. **Given** a generated Word already exists, **When** I click open/download,
   **Then** I receive the latest generated file or choose to regenerate it.

---

### User Story 4 - Preserve Heavy Factory Workflows (Priority: P2)

As the operator, I can continue using the PDF Factory/OCR/model pipeline without
losing corrections or training data while the platform moves to remote access.

**Why this priority**: The factory is expensive and data-sensitive. It should
move in stages, not by forcing all model jobs into the public web immediately.

**Independent Test**: Run a local or remote factory job, verify job status is
tracked, and verify the output lands in staging or the central database through
an explicit approved flow.

**Acceptance Scenarios**:

1. **Given** a local factory job produces reviewed problems, **When** it is
   published, **Then** the central database receives approved data with origin
   metadata.
2. **Given** a model job is long-running, **When** the browser disconnects,
   **Then** the job continues or remains resumable.

---

### User Story 5 - Back Up And Restore The Server (Priority: P1)

As the operator, I can recover the platform if the server or migration fails.

**Why this priority**: The remote server becomes the official source of truth,
so backups are mandatory.

**Independent Test**: Execute backup, restore into a test database or local
mirror, and verify counts and asset paths.

**Acceptance Scenarios**:

1. **Given** the server has current data, **When** the backup task runs,
   **Then** it stores a database dump and required assets.
2. **Given** a backup exists, **When** it is restored in a test environment,
   **Then** counts and core workflows remain valid.

### Edge Cases

- Server is online but PostgreSQL is unavailable.
- Domain points to the server but API health fails.
- Local database has Windows-only paths that cannot exist on Linux.
- PDF or cover files referenced in the database are missing.
- A model/OCR job is running when the browser closes.
- A local mirror and server database diverge.
- A migration partially succeeds and must be rolled back.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose the platform through `nexumathjf.com` and the
  planned subdomains.
- **FR-002**: System MUST run against PostgreSQL on the server for production.
- **FR-003**: System MUST migrate or restore the math bank with validated counts
  for `problemas`, books, instances, origins, and related metadata.
- **FR-004**: System MUST store PDFs, covers, generated Word files, uploads, and
  other assets under controlled server storage.
- **FR-005**: System MUST rewrite local Windows file paths into server-safe
  storage references during migration.
- **FR-006**: System MUST provide backup and restore procedures for database and
  required assets.
- **FR-007**: System MUST avoid exposing PostgreSQL directly to the public
  Internet.
- **FR-008**: System MUST keep secrets out of committed files.
- **FR-009**: System MUST support a local mirror or local production tool mode
  without uncontrolled double-writing.
- **FR-010**: System MUST track long-running jobs with status, logs, and failure
  details.
- **FR-011**: System SHOULD initially keep heavy OCR/segmentation/model jobs as
  controlled local or worker processes until remote execution is deliberately
  planned.
- **FR-012**: System SHOULD allow remote Word generation from books, instances,
  and problem filters.

### Key Entities

- **ServerDatabase**: PostgreSQL database that becomes the official production
  source of truth.
- **MathBank**: Problems, topics, origins, books, instances, and metadata used
  by Studio, Aula, Word generation, and future learning workflows.
- **ServerStorage**: Stable server-side filesystem or object-storage root for
  PDFs, covers, generated Word files, uploads, staging, and backups.
- **DomainRoute**: Public hostname mapped to a specific surface such as landing,
  studio, aula, or API.
- **FactoryJob**: Long-running unit of OCR, segmentation, normalization, Word
  generation, migration, or sync work.
- **LocalMirror**: Optional local PostgreSQL copy used for offline work,
  fallback, or controlled sync.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The platform is reachable from a non-local network through
  `https://nexumathjf.com`.
- **SC-002**: `/health` reports healthy API and database status on the server.
- **SC-003**: Migrated database counts match the signed pre-migration report.
- **SC-004**: At least one migrated book with PDF and cover opens correctly from
  server storage.
- **SC-005**: At least one Word document is generated and downloaded from the
  remote web flow.
- **SC-006**: A backup can be restored into a test/local mirror with matching
  core counts.
- **SC-007**: No public API response exposes local Windows paths, tokens, or
  database credentials.

## Assumptions

- The public domain is `nexumathjf.com`.
- PostgreSQL is the target production database.
- The existing `scan-math-db` project remains the public web/API foundation.
- `Auditor-IA` remains the internal factory and model workflow at first.
- Heavy OCR/model execution may be moved to server jobs later, after the
  database and domain deployment are stable.
- The server will provide SSH or equivalent deployment access.
- A final migration window will freeze local writes before declaring the server
  official.
