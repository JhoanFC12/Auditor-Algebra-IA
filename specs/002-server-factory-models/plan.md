# Implementation Plan: Fabrica Server-Side With Local Models And Hugging Face OCR

**Branch**: `002-server-factory-models` | **Date**: 2026-07-06 | **Spec**: `specs/002-server-factory-models/spec.md`

**Input**: Feature specification from `specs/002-server-factory-models/spec.md`

## Summary

Move the PDF Factory processing pipeline toward the server while preserving the
current OCR strategy: local-style segmentation models run on the server, and the
trained OCR model remains on Hugging Face. Autonomous agents are recorded as
future scope and are not required for this feature.

## Technical Context

**Language/Version**: Python 3.11+, FastAPI-compatible server code, PowerShell
for local scripts, Linux shell for server operations.

**Primary Dependencies**: Existing `modulos/instance_factory`, YOLO/model
runtime dependencies, Hugging Face endpoint client, PostgreSQL, server storage,
existing web UI assets.

**Storage**: Server PostgreSQL plus server filesystem storage for PDFs, pages,
boxes, crops, OCR outputs, graph segments, staging records, and training
corrections.

**Testing**: Existing Python `unittest` tests plus new focused tests for server
job state, model inventory, staging paths, OCR endpoint retry behavior, and
training-correction persistence.

**Target Platform**: Linux server for production jobs; Windows remains
development/local mirror environment.

**Project Type**: Web/API service plus long-running background jobs.

**Performance Goals**:

- Segmentation jobs should expose progress counters.
- OCR calls should be batched/queued to reduce endpoint idle time.
- Browser refresh must not restart or cancel active jobs.

**Constraints**:

- OCR remains Hugging Face endpoint in this phase.
- Server-side models must not depend on Windows paths.
- No autonomous agent automation in the current feature.
- Endpoint scale-down must wait until all active OCR jobs are complete.
- Corrections must be stored as training data.

**Scale/Scope**: Start with one instance/PDF flow, then support multiple active
instances and jobs.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- Remote source of truth: PASS if all new staging artifacts use server storage.
- Data safety: PASS if upstream changes invalidate or regenerate dependent
  artifacts instead of mixing stale data.
- Spec-first: PASS because this feature is tracked under
  `specs/002-server-factory-models`.
- Public/internal boundary: PASS if Hugging Face tokens and local model paths are
  environment/config only and never exposed publicly.
- Observable workflows: PASS only if jobs expose status, counters, logs, and
  errors.

## Project Structure

### Documentation (this feature)

```text
specs/002-server-factory-models/
|-- spec.md
|-- plan.md
|-- research.md
|-- data-model.md
|-- quickstart.md
|-- contracts/
`-- tasks.md
```

### Source Code (expected touch points)

```text
E:/Github/Auditor-IA/
|-- modulos/instance_factory/
|   |-- library_web_server.py
|   |-- web_server.py
|   |-- pipeline.py
|   |-- staging.py
|   |-- model_inventory.py
|   `-- web/
|-- database/
|-- tools/
|-- tests/
`-- docs/

E:/Github/MathContentStudio/scan-math-db/
|-- app/
|-- scripts/
`-- docs/
```

**Structure Decision**: Keep the processing logic owned by `Auditor-IA` and
expose server-safe job/API boundaries to the remote web layer. Avoid duplicating
model logic in frontend JavaScript.

## Implementation Phases

### Phase 0 - Inventory

- List all model paths currently used locally.
- Classify each model as server-local or Hugging Face endpoint.
- Identify staging paths that still assume Windows local filesystem.
- Identify job operations that are currently tied to browser/session lifetime.

### Phase 1 - Server Model Configuration

- Define environment variables for server model paths.
- Add model inventory health output.
- Fail clearly when a model is missing.
- Keep Hugging Face OCR endpoint separate from local model inventory.

### Phase 2 - Server Jobs

- Introduce or formalize server job records for:
  - page rendering;
  - problem segmentation;
  - crop materialization;
  - graph segmentation;
  - OCR Hugging Face calls.
- Persist status, counters, errors, and artifacts.
- Make jobs resumable/reloadable from the web.

### Phase 3 - Staging And Invalidation

- Store server-side staging artifacts under server storage.
- When boxes change, mark downstream crops/OCR/segments as stale.
- Regenerate dependent artifacts explicitly.
- Persist human corrections as training data.

### Phase 4 - Hugging Face OCR Control

- Keep OCR remote.
- Add retry/backoff for 503/cold start.
- Track global active OCR jobs before scale-down.
- Show endpoint state and actionable errors.

### Phase 5 - Web Workflow

- Start jobs from the web.
- Show progress after refresh.
- Allow review/correction from server staging.
- Keep autonomous agents disabled/out of scope.

## Out Of Scope

- Book organizer agent.
- Segmentation review agent.
- OCR review agent.
- Golden base curator agent.
- Normalizer agent.
- Full local replacement of Hugging Face OCR.

These require separate specs after the remote server pipeline is stable.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Server jobs instead of direct request/response | Segmentation/OCR can take too long and browser refreshes must not cancel work | Direct HTTP request would keep reproducing the current fragile workflow |
| Dual model execution modes | OCR remains Hugging Face while detectors run server-local | Hosting OCR locally now increases cost, complexity, and hardware requirements |

