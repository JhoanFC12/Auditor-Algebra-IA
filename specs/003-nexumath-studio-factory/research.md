# Research: NexumathJF Studio Factory

## Decision 1: Use `scan-math-db` As The Public Deployment Host

**Decision**: The remote Studio replacement will be implemented through `E:/Github/MathContentStudio/scan-math-db`, not as a separate public application.

**Rationale**: This repo already owns domain-aware routing, authentication, `/studio`, `/aula`, `/api`, static web pages, storage mounting, math-bank access, and deployment scripts for `nexumathjf.com`.

**Alternatives considered**:

- Deploy `Auditor-IA` directly as a second public app: rejected because it would duplicate authentication, routing, and domain configuration.
- Keep Biblioteca/Fabrica local and link to it from Studio: rejected because the user explicitly wants remote work from the domain.

## Decision 2: Port Biblioteca/Fabrica By Capability, Not By Copying The Local App Whole

**Decision**: Move the workflow in vertical slices: library/instances, storage, jobs, OCR/model status, review/promotion, Word generation.

**Rationale**: The local app contains desktop assumptions, local paths, and services that must be adapted to public security and server storage rules. Capability slices allow validation and rollback.

**Alternatives considered**:

- Copy all local files into `scan-math-db` at once: rejected because it risks exposing paths/secrets and breaking existing Studio/Aula.
- Keep two separate frontends: rejected because the goal is to replace Studio with one clear workflow.

## Decision 3: Server PostgreSQL And Server Storage Become Official After Cutover

**Decision**: Server PostgreSQL and `/srv/mathcontentstudio`-style storage are the target source of truth, but only after backup, restore, asset validation, and smoke tests.

**Rationale**: The constitution requires remote-first operation and data safety. Existing migration docs already support math-bank export/restore and storage rewrite.

**Alternatives considered**:

- Continue using local DB through tunnels: rejected as final state because it keeps the PC as the operational bottleneck.
- Use object storage immediately: deferred because filesystem server storage is simpler for the first cutover and matches current scripts.

## Decision 4: Jobs Are Mandatory For Long Operations

**Decision**: Segmentation, staging materialization, OCR queues, graph segmentation, promotion, Word generation, migration, and sync must run as jobs with persistent status.

**Rationale**: Remote browser sessions are unstable. Jobs allow refresh/reconnect recovery, progress display, partial failure handling, and cost control.

**Alternatives considered**:

- Synchronous API calls for model steps: rejected because OCR/model work can take minutes and would fail on refresh.
- Client-side background work: rejected because files, models, secrets, and state belong on the server.

## Decision 5: Keep OCR On Hugging Face For This Phase

**Decision**: The trained OCR endpoint remains on Hugging Face; the server controls endpoint status and cost lifecycle.

**Rationale**: The user explicitly wants to keep the OCR model connection. Existing work already includes endpoint status, retry, and scale-down behavior.

**Alternatives considered**:

- Move OCR fully local immediately: deferred because model size/capacity and quality are unresolved.
- Use a different hosted OCR service: rejected for this phase because it would disrupt the current training loop.

## Decision 6: Local Model Stages Move To The Server

**Decision**: Problem segmentation, number/alternative detection, graph segmentation, and auxiliary local model stages must be available from server paths.

**Rationale**: Remote workflow cannot depend on local Windows model paths. Model readiness must be visible and block steps when unavailable.

**Alternatives considered**:

- Keep local model calls through the PC: rejected because it makes the server workflow dependent on a local machine.
- Host every model externally: rejected for now due cost and because local server execution is cheaper for these stages.

## Decision 7: Keep Agents Out Of The First Remote Replacement

**Decision**: Book organizer, OCR verifier, segmentation verifier, and golden-base agents remain future work.

**Rationale**: The immediate target is reliable remote operation. Agents depend on stable storage, jobs, review data, and permissions.

**Alternatives considered**:

- Build agents during migration: rejected because it increases scope before the core workflow is stable.

## Decision 8: Treat Public Routing As A Separate Cutover Gate

**Decision**: Before declaring the remote workflow usable, validate public
routing independently from app functionality. The domain gate checks
`api.nexumathjf.com`, `nexumathjf.com/studio`,
`nexumathjf.com/studio/factory/bootstrap`, `studio.nexumathjf.com`, and
`aula.nexumathjf.com`.

**Rationale**: Current public evidence shows API health is reachable but Studio
routes return 404. That means the application can be locally ready while the
domain still points to the old or incomplete route map. Separating routing from
authenticated workflow smoke makes the blocker explicit.

**Alternatives considered**:

- Debug all Studio behavior from the local app only: rejected because the user
  wants to work from the public domain.
- Treat a 404 as an authentication issue: rejected because protected routes
  should return login, redirect, 401, or 403, not route-not-found.
- Cut over the database before public routing works: rejected because rollback
  risk is higher if users cannot even reach the workflow.
