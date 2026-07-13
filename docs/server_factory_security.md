# Server Factory Security Checklist

Use this checklist before exposing Biblioteca/Fabrica beyond local development.

## Secrets

- [ ] Do not commit `HF_TOKEN`, `HUGGINGFACEHUB_API_TOKEN`, database passwords, or
  endpoint credentials.
- [ ] Load tokens from environment variables or deployment secret storage.
- [ ] Keep Hugging Face endpoint-management permissions separate from general
  user-facing access.
- [ ] Rotate tokens after local debugging sessions where logs may have captured
  request metadata.

## API Output

- [ ] API errors must not expose full internal tracebacks to clients.
- [ ] Job status may expose actionable summaries, counters, and safe artifact
  keys, but not raw token values.
- [ ] Server storage artifacts should expose `asset_key` or public URL, not
  arbitrary absolute local paths.
- [ ] `server_models` may expose model identifiers and readiness actions, but
  must not expose secrets.

## Filesystem

- [ ] `MCS_SERVER_STORAGE_ROOT` must point to a directory controlled by the
  application.
- [ ] All server artifact writes must stay under server storage.
- [ ] Windows/UNC paths are development-only unless they are inside the configured
  storage root.
- [ ] Reject or sanitize asset keys containing `..`, absolute paths, or drive
  prefixes.

## Database

- [ ] Factory staging must not write directly into `problemas`.
- [ ] Promotion to database must remain explicit and human-triggered.
- [ ] Store source/origin metadata structurally, not mixed into mathematical
  statement text.
- [ ] Retry deadlock-prone DB operations with transaction boundaries if promotion
  is parallelized later.

## Jobs

- [ ] Browser refresh must not cancel jobs.
- [ ] OCR endpoint scale-down must wait until all active OCR jobs finish.
- [ ] Cold-start retries must be bounded.
- [ ] Non-recoverable errors such as `403` must fail fast.

## Training Data

- [ ] Human corrections must include provenance: book, instance, record, source
  path/artifact, model source, and timestamp.
- [ ] Training manifests must not require database writes.
- [ ] Golden bases must distinguish model output from human-corrected labels.
- [ ] Dataset promotion/retraining remains manual until a separate agent spec is
  approved.
