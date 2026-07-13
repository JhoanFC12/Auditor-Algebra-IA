# Research: Remote Platform Migration

## Decision 1: Use Server PostgreSQL As Production Source Of Truth

Decision: make `mathcontentstudio_prod` the official production database after
validated migration.

Rationale:

- The user needs access from anywhere through `nexumathjf.com`.
- A local Windows database cannot be the reliable source for remote workflows.
- Current local paths and local-only storage are the main migration risk.

Rejected alternative: keep local PostgreSQL as production and expose it through
tunnels. This keeps the system dependent on the local PC and creates avoidable
security and availability risks.

## Decision 2: Keep OCR In Hugging Face For This Feature

Decision: this feature does not replace the trained Hugging Face OCR endpoint.

Rationale:

- The OCR endpoint already exists and is part of the current workflow.
- Server migration should first stabilize database, storage, domain, and Word
  workflows.
- Model lifecycle, endpoint costs, and worker queues belong in the follow-up
  `002-server-factory-models` feature.

## Decision 3: Move Local Model Execution To Server Jobs Later

Decision: local segmentation models should become server-side jobs in the next
feature, not in this base migration slice.

Rationale:

- Problem segmentation, number/alternative detection, and graph segmentation
  require model files, GPU/CPU decisions, queues, and artifact storage.
- Mixing this with database migration increases risk.
- The server storage layout in this feature prepares the required directories.

## Decision 4: Store Portable Asset References

Decision: database values should move toward portable asset references resolved
against `/srv/mathcontentstudio`, not raw Windows paths.

Rationale:

- The audit found thousands of Windows/UNC paths.
- Portable references allow API responses, downloads, backups, and server
  workers to remain stable if physical storage moves.

## Decision 5: Make Migration Observable And Reversible

Decision: every migration step must produce reports, manifests, and validation
counts before cutover.

Rationale:

- The current data set includes books, instances, source PDFs, covers, crops,
  images, generated Word files, origins, and training corrections.
- Silent migration errors would be expensive to find after the domain becomes
  the main entry point.
