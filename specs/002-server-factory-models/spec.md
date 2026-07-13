# Feature Specification: Fabrica Server-Side With Local Models And Hugging Face OCR

**Feature Branch**: `002-server-factory-models`

**Created**: 2026-07-06

**Status**: Draft

**Input**: User description: "Mantener la conexion con el modelo OCR en Hugging Face, pero mover los modelos locales actuales al servidor. Los agentes para organizar libros, verificar segmentacion y corregir OCR se trabajaran mas adelante."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Process PDFs From The Remote Platform (Priority: P1)

As the operator, I can start the PDF Factory workflow from the remote web app and
have the server execute the local segmentation models against server-side files.

**Why this priority**: Remote access is incomplete if the core PDF pipeline still
requires manually operating the local PC.

**Independent Test**: Upload or select a PDF from the remote app, start problem
segmentation, and verify server-side staging is created without using local
Windows paths.

**Acceptance Scenarios**:

1. **Given** a PDF is available in server storage, **When** I start page/problem
   detection, **Then** the server runs the configured local problem segmentation
   model and stores detected boxes in staging.
2. **Given** the browser is closed during processing, **When** I reopen the
   instance, **Then** the job status and outputs are still available.

---

### User Story 2 - Keep OCR On Hugging Face (Priority: P1)

As the operator, I can continue using the trained OCR model through Hugging Face
while the rest of the pipeline runs on the server.

**Why this priority**: The OCR model is already trained and expensive/heavy to
host locally. Keeping it in Hugging Face reduces server complexity.

**Independent Test**: Process a crop through the server job queue and verify the
OCR request is sent to Hugging Face, then persisted into server staging.

**Acceptance Scenarios**:

1. **Given** server-side crops exist, **When** I run OCR, **Then** the server
   calls the configured Hugging Face OCR endpoint and saves raw OCR in staging.
2. **Given** the Hugging Face endpoint is scaled to zero or temporarily
   unavailable, **When** OCR starts, **Then** the server reports waiting/retry
   status instead of failing silently.
3. **Given** OCR finishes, **When** endpoint lifecycle controls are enabled,
   **Then** the endpoint can be scaled down only after all active OCR jobs finish.

---

### User Story 3 - Run Existing Local Models On The Server (Priority: P1)

As the operator, I can deploy the existing local models to the server and choose
which model version is active for each factory stage.

**Why this priority**: Server-side processing depends on these local models:
problem segmentation, numbering/alternative detection, and graph segmentation.

**Independent Test**: Configure model paths on the server, run a small PDF
sample, and verify each model produces expected artifacts.

**Acceptance Scenarios**:

1. **Given** model files exist on the server, **When** the app starts, **Then**
   model inventory reports configured versions and missing models clearly.
2. **Given** a page is processed, **When** problem segmentation runs, **Then** the
   server uses the configured problem detector model.
3. **Given** a crop is processed, **When** graph segmentation runs, **Then** the
   server uses the configured graph segmentation model.

---

### User Story 4 - Preserve Human Review And Training Data (Priority: P2)

As the operator, I can review server-generated boxes, crops, OCR, and graph
segments from the web while corrections remain usable for training.

**Why this priority**: The system must keep improving progressively from human
corrections.

**Independent Test**: Correct a bad box or OCR result remotely, verify the
correction is stored, and verify it appears in the appropriate training bank.

**Acceptance Scenarios**:

1. **Given** a detected box is corrected, **When** the correction is saved,
   **Then** the server stores the corrected sample for future detector training.
2. **Given** raw OCR is corrected, **When** the correction is saved, **Then** the
   server stores it as OCR training data.
3. **Given** a graph segment is corrected, **When** the correction is saved,
   **Then** the server stores it for graph segmentation training.

---

### User Story 5 - Defer Agent Automation (Priority: P3)

As the operator, I want future agents for book organization, segmentation review,
and OCR verification, but these must not block the current server migration.

**Why this priority**: Agents are useful, but the immediate objective is remote
operation and server-side processing.

**Independent Test**: The active implementation plan excludes autonomous agents
and records them as future scope.

**Acceptance Scenarios**:

1. **Given** the server factory is being implemented, **When** tasks are created,
   **Then** agent automation is listed as out of scope.
2. **Given** corrections are saved, **When** future agents are added, **Then**
   they can use the stored training/correction data.

### Edge Cases

- Server has CPU only and model inference is slower than local GPU.
- Model file is missing or incompatible.
- Hugging Face token is missing or lacks endpoint/inference permissions.
- OCR endpoint returns 503 during cold start.
- Multiple OCR jobs are active and one finishes earlier than the others.
- Browser refreshes while segmentation/OCR is running.
- Server staging contains stale crops after boxes change.
- Local and server model versions differ.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Server MUST run the problem segmentation model from server-side
  model paths.
- **FR-002**: Server MUST run numbering/alternative detection from server-side
  model paths when available.
- **FR-003**: Server MUST run graph segmentation from server-side model paths.
- **FR-004**: Server MUST call the Hugging Face OCR endpoint for OCR, not a local
  OCR model, in this phase.
- **FR-005**: Server MUST persist job status, progress counters, logs, and error
  details.
- **FR-006**: Browser refresh or disconnect MUST NOT cancel active server jobs.
- **FR-007**: Server MUST store staging artifacts in server storage, not in local
  Windows paths.
- **FR-008**: Server MUST persist human corrections as training data for the
  corresponding model family.
- **FR-009**: Server MUST prevent stale downstream artifacts from being treated
  as valid after upstream boxes/crops change.
- **FR-010**: Server MUST expose clear model inventory/configuration status.
- **FR-011**: OCR endpoint scale-down MUST consider all active OCR jobs, not only
  the job that just finished.
- **FR-012**: Autonomous agents for organizing books, verifying segmentation, and
  correcting OCR are OUT OF SCOPE for this feature and must be tracked as future
  work.

### Key Entities

- **FactoryInstance**: A book instance being processed through pages, boxes,
  crops, OCR, graph segmentation, review, and final database promotion.
- **ServerJob**: Restartable server-side process with status, counters, logs,
  inputs, outputs, and errors.
- **ModelInventory**: Server-visible list of configured models, versions, paths,
  and availability.
- **StagingArtifact**: Server-side artifact such as rendered page, boxes, crop,
  OCR output, graph segment, or review data.
- **TrainingCorrection**: Human correction stored for retraining a specific
  model family.
- **HfOcrEndpoint**: Hugging Face OCR endpoint configuration and lifecycle state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A PDF instance can be segmented on the server without local Windows
  paths.
- **SC-002**: A crop can be sent from server staging to Hugging Face OCR and raw
  OCR is persisted.
- **SC-003**: Job status remains visible after browser refresh.
- **SC-004**: Missing model or endpoint configuration is shown as an actionable
  error.
- **SC-005**: Human corrections create training records for detector/OCR/graph
  model improvement.
- **SC-006**: No autonomous agent behavior is required to complete the current
  feature.

## Future Scope: Agents

Agents are intentionally deferred. The planned future agents are:

- **Book Organizer Agent**: organize PDFs by course, create books/instances, and
  classify pages as theory, solved exercises, proposed exercises, or irrelevant.
- **Segmentation Review Agent**: verify whether problem boxes are correct and
  suggest corrections for the detector golden base.
- **OCR Review Agent**: compare crop image against raw OCR, detect common OCR
  mistakes, and propose corrections for OCR training data.
- **Golden Base Curator Agent**: count useful corrections, prepare datasets, and
  trigger retraining when thresholds are reached.
- **Normalizer Agent**: future stage after OCR/segmentation are reliable.

These agents must be specified in a separate feature after the server-side
factory and data flow are stable.

## Assumptions

- The production web/API and storage will run on the server.
- PostgreSQL central database exists or will be created by the remote platform
  feature.
- Hugging Face OCR endpoint remains available and configurable by environment.
- Server has enough resources to run YOLO-style segmentation models.
- Full agent automation is not part of the first remote factory implementation.
