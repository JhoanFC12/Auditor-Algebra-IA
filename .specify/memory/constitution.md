# Auditor-IA Constitution

## Core Principles

### I. Remote-First Source Of Truth

The long-term operating mode is remote access through `nexumathjf.com`, with a
server-side PostgreSQL database and server-side storage as the official source
of truth. Local tools may remain available for heavy production, recovery, or
offline work, but they must not create uncontrolled double-writing once the
server is declared official.

### II. Data Safety Before Automation

Any migration, bulk update, deletion, model promotion, or database rewrite must
have an explicit backup, a dry-run or report when practical, and a validation
step. The system must prefer reversible operations and must not silently discard
local staging, training corrections, PDFs, covers, generated Word files, or
problem records.

### III. Spec-First Execution

Major work must start from a Spec Kit feature under `specs/`. A feature is not
ready to implement until the expected user journeys, data entities, success
criteria, risks, and rollback path are explicit. Implementation details belong
in the plan and tasks, not in vague chat context.

### IV. Clear Boundary Between Public Web And Internal Factory

The public web/API layer, the math-bank database, and the internal
PDF/OCR/model factory must have explicit contracts. Heavy model execution,
endpoint lifecycle, training datasets, and local correction workflows must not
leak secrets or internal paths into the public domain.

### V. Observable, Restartable Workflows

Long processes must be job-based, resumable, and observable. OCR, segmentation,
normalization, Word generation, database migration, and server sync must expose
status, logs, counters, failure reasons, and retry behavior. Browser refreshes
or remote disconnects must not cancel server jobs.

## Security And Deployment Constraints

- PostgreSQL must not be exposed directly to the public Internet.
- Secrets must live in environment variables or server secret storage, never in
  committed source files.
- Public routes must not reveal local Windows paths, tokens, model credentials,
  or private filesystem structure.
- Domain routing must keep the intended split:
  - `nexumathjf.com` for landing/workflow entry.
  - `studio.nexumathjf.com` for content/teacher operations.
  - `aula.nexumathjf.com` for student workflows.
  - `api.nexumathjf.com` for API/health/integration endpoints.
- Server storage paths must be stable and portable, for example under
  `/srv/mathcontentstudio`.

## Development Workflow

1. Capture the objective in Obsidian when it changes project direction.
2. Create or update a Spec Kit spec under `specs/`.
3. Produce an implementation plan before touching high-risk code.
4. Add focused tests or validation scripts for migration, API, storage, and
   job behavior.
5. Verify locally before deployment.
6. Deploy with backup and rollback instructions.
7. Record operational decisions in `docs/` and, when useful, in Obsidian.

## Governance

This constitution overrides ad-hoc implementation choices for remote deployment,
database migration, public API design, storage layout, and long-running jobs.
Changes to these principles require updating this file and the affected Spec Kit
feature documents.

**Version**: 1.0.0 | **Ratified**: 2026-07-06 | **Last Amended**: 2026-07-06
