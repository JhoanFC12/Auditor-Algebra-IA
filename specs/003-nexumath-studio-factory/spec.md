# Feature Specification: NexumathJF Studio Factory

**Feature Branch**: `[003-nexumath-studio-factory]`

**Created**: 2026-07-06

**Status**: Draft

**Input**: User description: "Replace NexumathJF Studio with the remote Biblioteca/Fabrica PDF workflow, using nexumathjf.com as the primary web entry, a server PostgreSQL source of truth, server storage for PDFs covers crops segments Word and training assets, Hugging Face OCR endpoint integration, server-side local models, observable resumable jobs, and local PC fallback only for backup and auxiliary work."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Enter Studio From Anywhere (Priority: P1)

As the content owner, I can open NexumathJF Studio from the public domain and see the Biblioteca/Fabrica workflow instead of the previous temporary Studio dashboard, so I can work from any location without depending on the local PC.

**Why this priority**: Remote access is the main business goal. If users still need the local launcher, the migration has not succeeded.

**Independent Test**: Open the public Studio entry, authenticate, and reach the Biblioteca/Fabrica home page with books, instances, workflow status, and current server health visible.

**Acceptance Scenarios**:

1. **Given** the user has a valid Studio account, **When** they enter NexumathJF Studio from the public domain, **Then** the first working surface is the Biblioteca/Fabrica experience.
2. **Given** the user is not authenticated, **When** they attempt to access the Studio workflow, **Then** the system asks for login and returns them to the intended workflow after authentication.
3. **Given** the server is reachable but a required subsystem is unavailable, **When** the user opens Studio, **Then** the page shows a clear operational status and does not expose private server details.

---

### User Story 2 - Work Books And Instances Remotely (Priority: P1)

As the content owner, I can manage books, covers, PDFs, and instances from the web domain, so each book can move through pages, boxes, staging, OCR, review, database promotion, and Word generation without local-only paths.

**Why this priority**: Biblioteca/Fabrica is the core workflow being moved to the server.

**Independent Test**: Register or open a book, enter one instance, select pages, review boxes, materialize staging, and confirm the data and files are stored in the remote environment.

**Acceptance Scenarios**:

1. **Given** a book has a PDF available on server storage, **When** the user opens an instance, **Then** the PDF workflow displays pages and the current stage of the instance.
2. **Given** an instance has reviewed boxes, **When** the user materializes staging, **Then** the resulting crops are stored as server-managed assets.
3. **Given** a book has finished instances, **When** the user views the book list, **Then** they can see counts for total instances, worked instances, database-ready instances, and missing work.

---

### User Story 3 - Run OCR And Model Jobs Without Losing Progress (Priority: P1)

As the content owner, I can start long OCR/model operations from the web and safely leave, refresh, or reconnect while jobs continue on the server.

**Why this priority**: Remote work is unusable if model jobs are tied to a browser tab.

**Independent Test**: Start an OCR or segmentation job, refresh or reconnect from another device, and verify the job status, progress, results, and errors remain available.

**Acceptance Scenarios**:

1. **Given** an OCR queue is started, **When** the browser refreshes, **Then** the queue continues and the user can recover status.
2. **Given** a model job fails for one item, **When** the user opens the job report, **Then** the report identifies the affected item, error type, and next recommended action.
3. **Given** no OCR jobs remain active, **When** the OCR endpoint is no longer needed, **Then** the system can report that the endpoint is ready to scale down for cost control.

---

### User Story 4 - Promote Reviewed Problems And Generate Word From Server Data (Priority: P2)

As the content owner, I can promote reviewed problems to the official database and generate Word documents from server data, so practices no longer depend on local generated files.

**Why this priority**: The server must become the operational source of truth after review.

**Independent Test**: Promote reviewed items from one instance, then generate and download a Word document from those promoted problems.

**Acceptance Scenarios**:

1. **Given** reviewed problems are complete, **When** the user promotes them, **Then** the system records the database state and prevents duplicate or partial promotion without a clear confirmation.
2. **Given** promoted problems exist for an instance, **When** the user generates Word from the instance, **Then** the document is created in server storage and can be downloaded or opened from Studio.
3. **Given** the user filters problems by course, topic, book, author, or source, **When** they select problems across filters, **Then** the selection is preserved until the user clears or converts it.

---

### User Story 5 - Preserve Training Corrections For Future Models (Priority: P2)

As the content owner, I can keep every human correction as training data, so OCR, segmentation, graph detection, and normalization models improve over time.

**Why this priority**: Progressive model improvement is part of the long-term operating model.

**Independent Test**: Correct a box, OCR text, graph segment, or final format and verify a training-ready correction record is stored without promoting it automatically.

**Acceptance Scenarios**:

1. **Given** the user corrects problem boxes, **When** they save the page, **Then** the correction is recorded for the problem detector training bank.
2. **Given** the user corrects OCR or final format, **When** they save it, **Then** the correction is recorded for the corresponding training bank.
3. **Given** a model version changes, **When** new corrections are collected, **Then** the system can distinguish which model produced the original output.

---

### User Story 6 - Keep Local PC As Backup Only (Priority: P3)

As the content owner, I can keep the local PC as a fallback, backup, and auxiliary tool without creating uncontrolled double-writing against the server.

**Why this priority**: Local work remains useful, but it must not corrupt the remote source of truth.

**Independent Test**: Run a local fallback workflow and verify it is clearly marked as local, syncable, or read-only depending on the selected mode.

**Acceptance Scenarios**:

1. **Given** the server is the official source, **When** local tools run, **Then** they do not silently write conflicting official data.
2. **Given** a backup or sync is executed, **When** it completes, **Then** the user can see what changed and how to roll back.

### Edge Cases

- The public domain is reachable but the database is down.
- The database is available but server storage is missing a PDF, crop, cover, or generated Word file.
- A model path points to a local Windows path after deployment.
- A long job is running when the server restarts.
- The Hugging Face OCR endpoint returns cold-start or unavailable errors.
- A user attempts to promote stale staging after boxes were edited.
- A migrated record still contains a private local path.
- A user opens the workflow from mobile or a narrow viewport.
- Multiple users or tabs work on the same instance.
- The migration must be rolled back to the previous Studio experience.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST make NexumathJF Studio the primary entry point for the Biblioteca/Fabrica workflow.
- **FR-002**: The system MUST replace the previous temporary Studio dashboard for authorized content operations while preserving login and role-based access.
- **FR-003**: The system MUST expose a remote Biblioteca view with books, covers, metadata, instance counts, progress state, and actions to open instance workflows.
- **FR-004**: The system MUST allow users to create, edit, and organize books and instances from the remote web workflow.
- **FR-005**: The system MUST store PDFs, covers, crops, graph segments, generated Word documents, and training assets in server-managed storage.
- **FR-006**: The system MUST prevent public responses from exposing private local paths, secrets, tokens, or internal filesystem layout.
- **FR-007**: The system MUST use the server database as the official source of truth once migration is declared complete.
- **FR-008**: The system MUST provide a migration path for the existing local math bank, book library, origins, instances, problems, PDFs, covers, and generated artifacts.
- **FR-009**: The system MUST detect and report records that still depend on local-only paths before they become official remote records.
- **FR-010**: The system MUST provide observable, resumable jobs for page segmentation, staging materialization, OCR, graph segmentation, promotion, and Word generation.
- **FR-011**: The system MUST keep job progress, counters, logs, errors, and retry state available after browser refresh or reconnect.
- **FR-012**: The system MUST keep OCR connected to the trained Hugging Face endpoint and expose user-visible status for endpoint readiness and failures.
- **FR-013**: The system MUST run server-side local models for problem segmentation, number/alternative detection, graph segmentation, and other local model stages.
- **FR-014**: The system MUST show model readiness per stage and block or degrade workflow steps when required models are unavailable.
- **FR-015**: The system MUST preserve human corrections as training data for problem detection, OCR, graph segmentation, and normalization.
- **FR-016**: The system MUST invalidate downstream artifacts when source boxes or page selections change.
- **FR-017**: The system MUST support review-first promotion and MUST NOT insert directly into final problem tables without human confirmation.
- **FR-018**: The system MUST generate Word documents from server-side database records and server-managed images.
- **FR-019**: The system MUST support Word generation by selected instances and by filtered problem selections.
- **FR-020**: The system MUST preserve cross-filter problem selections until the user explicitly clears or converts them.
- **FR-021**: The system MUST provide backup and rollback instructions before declaring the server as the official source.
- **FR-022**: The system MUST keep the local PC workflow available as fallback, backup, or auxiliary operation without uncontrolled double-writing.
- **FR-023**: The system MUST provide operational health views for database, storage, model readiness, OCR endpoint status, and active jobs.
- **FR-024**: The system MUST support a manual smoke validation that proves the remote workflow from book selection through Word generation.

### Key Entities *(include if feature involves data)*

- **Studio User**: Authenticated operator with permissions to access Biblioteca/Fabrica workflows.
- **Book**: A registered content source with metadata, cover, PDF references, and instance collection.
- **Instance**: A work unit for a book, such as a session, topic, proposed problems, resolved problems, or other segment.
- **Server Asset**: A PDF, cover, crop, segment image, generated Word document, exported dataset, or training artifact stored under managed server storage.
- **Workflow Stage**: The current state of an instance across pages, boxes, staging, OCR, review, database promotion, and Word generation.
- **Server Job**: A resumable long-running operation with status, progress, logs, errors, and result references.
- **Model Stage**: A configured model capability used by the workflow, including problem detector, number/alternative detector, graph segmenter, OCR endpoint, and future normalizer.
- **Training Correction**: A human-approved correction linked to the source item, model version, and corrected output.
- **Problem Record**: A reviewed problem ready for or already promoted to the official database.
- **Generated Practice**: A Word output produced from selected problems or selected instances.
- **Migration Batch**: A controlled import/export unit for database records and server assets.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can reach the Biblioteca/Fabrica home page from the public Studio entry in under 10 seconds when the server is healthy.
- **SC-002**: At least 95% of migrated book records have server-managed asset references with no local-only paths before official cutover.
- **SC-003**: A user can process one small test instance from page selection to reviewed staging without using the local desktop launcher.
- **SC-004**: Long-running jobs remain recoverable after browser refresh in 100% of smoke tests.
- **SC-005**: Job reports identify failed items and actionable error categories for 100% of simulated partial failures.
- **SC-006**: A reviewed instance can generate a downloadable Word document from server data in under 2 minutes for a 30-problem practice.
- **SC-007**: The remote UI exposes database, storage, model, OCR endpoint, and job health in one operational view.
- **SC-008**: No public API response in validation contains private Windows paths, access tokens, or server secret values.
- **SC-009**: Every human correction saved during the smoke workflow is represented in the appropriate training correction bank.
- **SC-010**: The migration has a documented rollback path and at least one verified backup before server cutover.

## Assumptions

- NexumathJF Studio remains the operator-facing authenticated surface for content work.
- The student-facing Aula workflow is preserved and not replaced by this feature.
- The trained OCR endpoint remains on Hugging Face for this phase.
- Problem segmentation, number/alternative detection, graph segmentation, and auxiliary local models will run on the server.
- The local PC can remain useful for backup and emergency work, but server data becomes official after cutover.
- Migration will be incremental; local and remote systems may coexist during validation.
- The server will use stable server storage rather than Windows absolute paths.
- Automatic agents are out of scope for the first remote replacement; they remain a later phase after the remote workflow is stable.
