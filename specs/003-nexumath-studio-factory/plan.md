# Implementation Plan: NexumathJF Studio Factory

**Branch**: `[003-nexumath-studio-factory]` | **Date**: 2026-07-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-nexumath-studio-factory/spec.md`

## Summary

Replace the current temporary NexumathJF Studio experience with the remote Biblioteca/Fabrica PDF workflow. The public Studio entry remains authenticated, but the working surface becomes the book library, instance workflow, server-side staging/OCR/model jobs, database promotion, and Word generation. The server database and server storage become the official source of truth after a validated cutover; the local PC remains a fallback, backup, and auxiliary tool only.

Technical approach: reuse the existing `scan-math-db` web/API host as the public Studio shell, integrate the Biblioteca/Fabrica domain from `Auditor-IA` behind explicit API contracts, migrate database and storage assets to the server, and convert long-running operations into observable, resumable server jobs.

## Plan Operativo En Espanol

### Objetivo Cerrado

El objetivo de esta feature no es abrir la Fabrica local desde otro boton. El
objetivo es reemplazar la experiencia de NexumathJF Studio por Biblioteca/Fabrica
PDF y operar desde el dominio con:

1. base oficial en PostgreSQL del servidor;
2. PDFs, portadas, crops, segmentos, golden bases y Word en storage del servidor;
3. OCR entrenado conectado a Hugging Face;
4. modelos locales ejecutandose en el servidor;
5. procesos largos como jobs recuperables;
6. PC local solo como respaldo, espejo o herramienta auxiliar.

### Estado Actual Real

Ya esta implementada la base de Studio Factory en `scan-math-db`:

- entrada remota de Fabrica desde Studio;
- biblioteca de libros e instancias;
- snapshots de instancia;
- jobs recuperables;
- guardado de revision;
- seleccion Word persistente;
- registro de job Word.

Esto valida la frontera web/API, pero no significa que el corte remoto completo
este terminado. Aun faltan los puntos de cierre listados abajo.

### Estado Publico Verificado 2026-07-06

La verificacion publica actual muestra que el servidor responde por API, pero
Studio Factory aun no esta publicado en el dominio principal:

| Ruta | Resultado actual | Interpretacion |
|------|------------------|----------------|
| `https://api.nexumathjf.com/health` | 200 OK | La API publica existe y responde. |
| `https://nexumathjf.com/studio` | 404 | La ruta Studio no esta cableada/publicada. |
| `https://nexumathjf.com/studio/factory/bootstrap` | 404 | El contrato Factory no esta disponible en el dominio. |

Por tanto, el siguiente hito no es agregar mas funciones locales. El siguiente
hito es publicar la app web de Studio Factory en la infraestructura del dominio,
con proxy/tunel, variables productivas, storage del servidor y smoke remoto.

### Separacion Dominio Publico vs SSH

`nexumathjf.com`, `api.nexumathjf.com`, `studio.nexumathjf.com` y
`aula.nexumathjf.com` son rutas publicas HTTP. En la verificacion actual
resuelven a Cloudflare, por lo que no deben usarse como destino SSH/SCP.

El despliegue operativo debe usar `NEXUMATH_SSH_HOST` o `-SshHost` con la
IP/host SSH real del servidor Linux. `NEXUMATH_SERVER_HOST` queda solo como
alias heredado cuando `NEXUMATH_SSH_HOST` esta vacio. Sin ese host SSH real no
se puede ejecutar T083-T088 ni declarar el servidor como fuente oficial.

### Pendiente Critico Antes De Despliegue Productivo

1. **Routing del dominio**: `nexumathjf.com/studio`,
   `studio.nexumathjf.com` y `/studio/factory/bootstrap` deben apuntar al
   despliegue productivo de `scan-math-db`.
2. **Variables productivas**: `.env.production` debe tener base de datos,
   storage, modelos, OCR Hugging Face, CORS y credenciales sin exponer secretos.
3. **Bundle migrable verificado**: validar manifest, conteos, PDFs, portadas
   y rutas server-safe con `scripts/test_math_bank_bundle_readiness.ps1`
   antes de subir/restaurar datos.
4. **Migracion de BD y assets**: empaquetar y subir el bundle de BD/assets con
   helper SSH reproducible, luego restaurar PostgreSQL, PDFs, portadas, crops,
   segmentos, Words y golden bases usando helper con backup previo, restore por
   Docker Compose y reporte de migracion.
5. **Smoke remoto real**: validar login, biblioteca, instancia, job, OCR,
   revision, promocion y Word desde `nexumathjf.com` o subdominio definido.
6. **Declaracion de fuente oficial**: activar el servidor como source of truth
   solo despues de backup, rollback, assets migrados y smoke remoto.

### Criterio De Terminado

La feature se considera lista solo cuando un usuario pueda, desde el dominio:

1. iniciar sesion;
2. abrir Biblioteca/Fabrica;
3. entrar a un libro e instancia;
4. ejecutar un flujo con jobs recuperables;
5. guardar revision/promocion;
6. generar y descargar Word;
7. verificar que no hay rutas Windows ni secretos en respuestas publicas;
8. restaurar o hacer rollback con una guia probada.

## Technical Context

**Language/Version**: Python 3.11+ for backend services; browser-native HTML/CSS/JavaScript for the current Studio frontend; existing Python model tooling from `Auditor-IA`.

**Primary Dependencies**: FastAPI, SQLAlchemy, PostgreSQL driver stack, static frontend assets, existing Biblioteca/Fabrica services, Hugging Face OCR endpoint, server-side YOLO/model runtimes.

**Storage**: PostgreSQL on the server as official database; server filesystem storage under a stable root such as `/srv/mathcontentstudio`; local Windows paths only in fallback or artifact-local compatibility tables.

**Testing**: Existing `scan-math-db` API tests plus `Auditor-IA` unittest suites for factory jobs, staging, model inventory, OCR endpoint control, and training banks. Add migration, contract, and smoke validation for the remote Studio workflow.

**Target Platform**: Linux server exposed through `nexumathjf.com`, `studio.nexumathjf.com`, `aula.nexumathjf.com`, and `api.nexumathjf.com`; Windows local machine remains fallback and backup client.

**Project Type**: Web service plus authenticated web application with background jobs, server storage, and model integrations.

**Performance Goals**:

- Studio home loads Biblioteca/Fabrica summary in under 10 seconds on healthy server.
- Job status polling returns enough state for progress recovery after refresh.
- 30-problem Word generation completes in under 2 minutes for validated server data.
- Large books and instances load incrementally instead of blocking on all crops/assets.

**Constraints**:

- PostgreSQL must not be exposed directly to the Internet.
- Public routes must not expose local Windows paths, tokens, server secrets, or raw internal tracebacks.
- Browser refresh or disconnect must not cancel OCR/model/Word/migration jobs.
- Human review remains required before final database promotion.
- OCR remains connected to Hugging Face for this phase; local model stages move to the server.
- Existing Aula/student workflows must not be broken by replacing Studio.

**Scale/Scope**:

- Initial scope covers the authenticated Studio operator workflow, math-bank data, PDFs/covers/crops/segments/Word artifacts, model readiness, and job orchestration.
- Autonomous agents are documented as future work and excluded from the first remote replacement.
- The first cutover may be incremental, but the final operating mode uses server database/storage as source of truth.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate Result | Notes |
|-----------|-------------|-------|
| Remote-First Source Of Truth | PASS | Plan explicitly moves official data to server PostgreSQL and server storage. |
| Data Safety Before Automation | PASS | Migration requires backup, dry-run/report, validation, and rollback before cutover. |
| Spec-First Execution | PASS | New feature spec and planning artifacts exist under `specs/003-nexumath-studio-factory`. |
| Clear Boundary Between Public Web And Internal Factory | PASS | Contracts split public Studio/API from internal factory, jobs, models, secrets, and storage. |
| Observable, Restartable Workflows | PASS | Long operations are job-based with status, counters, logs, errors, and recovery. |

## Project Structure

### Documentation (this feature)

```text
specs/003-nexumath-studio-factory/
|-- spec.md
|-- plan.md
|-- research.md
|-- data-model.md
|-- quickstart.md
|-- contracts/
|   |-- studio-factory-api.md
|   |-- job-lifecycle.md
|   `-- migration-cutover.md
`-- checklists/
    `-- requirements.md
```

### Source Code

```text
E:/Github/MathContentStudio/scan-math-db/
|-- app/
|   |-- main.py
|   |-- api/
|   |   |-- studio.py
|   |   `-- health.py
|   |-- math_bank.py
|   |-- models.py
|   |-- schemas.py
|   `-- web/
|       |-- studio-login.html
|       |-- studio-dashboard.html
|       |-- studio-instances.html
|       |-- studio-problems.html
|       |-- studio-pdf-open.html
|       |-- studio-pdf-viewer.html
|       `-- styles.css
|-- scripts/
|   |-- export_math_bank_bundle.py
|   |-- restore_math_bank_bundle.py
|   |-- setup_math_bank_server.sh
|   |-- backup_math_bank.sh
|   `-- sync_math_bank_from_server.ps1
`-- tests/
    `-- test_api_flow.py

E:/Github/Auditor-IA/
|-- modulos/instance_factory/
|   |-- library_web_server.py
|   |-- web_server.py
|   |-- pipeline.py
|   |-- staging.py
|   |-- server_jobs.py
|   |-- server_storage.py
|   |-- model_inventory.py
|   |-- hf_endpoint_manager.py
|   `-- web/
|       |-- app.js
|       `-- styles.css
|-- docs/
`-- tests/
```

**Structure Decision**: `scan-math-db` is the public deployment host and must own domain routing, authentication, public/static pages, server storage exposure, and public Studio APIs. `Auditor-IA` remains the source of Biblioteca/Fabrica domain logic until it is ported behind explicit contracts. Integration should move capability by capability, starting with remote library/instance visibility, then server jobs, then review/promotion/Word workflows.

## Phase 0: Research Decisions

See [research.md](./research.md).

## Phase 1: Design Artifacts

- [data-model.md](./data-model.md)
- [contracts/studio-factory-api.md](./contracts/studio-factory-api.md)
- [contracts/job-lifecycle.md](./contracts/job-lifecycle.md)
- [contracts/migration-cutover.md](./contracts/migration-cutover.md)
- [contracts/domain-routing.md](./contracts/domain-routing.md)
- [quickstart.md](./quickstart.md)

## Implementation Phases

### Phase A - Inventory And Compatibility Report

1. Audit current `scan-math-db` Studio routes, schemas, storage settings, authentication, and math-bank endpoints.
2. Audit Biblioteca/Fabrica APIs and assets needed for remote operation.
3. Produce a compatibility report for data fields, required tables, server storage paths, and public route replacements.
4. Identify local-only paths and classify them as official server assets, fallback local artifacts, or obsolete references.

### Phase B - Remote Shell Replacement

1. Replace the current Studio dashboard entry with the Biblioteca/Fabrica library surface.
2. Preserve existing authentication, roles, collaborator scoping, and Aula routes.
3. Add health/status surface for database, storage, OCR endpoint, model readiness, and active jobs.
4. Keep a rollback switch to open the previous Studio pages during validation.

### Phase C - Server Storage And Migration

1. Define the server storage root and asset naming policy.
2. Use or extend the existing math-bank bundle export/restore flow.
3. Validate the exported bundle manifest, counts, PDFs, covers and portable server paths before upload/restore.
4. Upload and extract the validated bundle with `scripts/deploy_math_bank_bundle.ps1`
   so the server receives a reproducible `storage/math_bank_bundle_release`.
5. Restore the bundle on the server through the documented helper so backup,
   PostgreSQL restore, asset copy, path rewrites and restore report are produced
   together.
6. Use `scripts/run_nexumath_studio_cutover.ps1` with `-RunBundleDeploy`,
   `-RunServerRestoreDryRun` and `-RunServerRestore` when the data migration
   must be executed from the same cutover runner.
7. Migrate PDFs, covers, generated Word files, and server-ready asset references.
8. Validate no official record depends on private Windows paths after cutover.

### Phase D - Factory Jobs On Server

1. Port page segmentation, box review, staging materialization, OCR queue, graph segmentation, and Word generation into server job contracts.
2. Persist job state, progress, logs, errors, result references, and retry status.
3. Ensure refresh/reconnect recovery.
4. Enforce stale-artifact invalidation when source boxes/pages change.

### Phase E - Review, Promotion, And Training Data

1. Preserve staging-first review and prevent direct writes to final problem tables.
2. Add promotion checks and duplicate prevention.
3. Store human corrections in training banks with model version/source metadata.
4. Keep future normalizer and autonomous agents out of the first cutover.

### Phase F - Validation And Cutover

1. Run local integration validation against `scan-math-db`.
2. Run server smoke validation on domain/subdomain routes.
3. Validate backup, restore, rollback, and local mirror sync.
4. Declare the server source of truth only after counts, assets, and workflow smoke tests pass.

### Phase G - Public Domain Publication

1. Deploy the clean `scan-math-db` release bundle to the server without copying
   local `.env`, caches, databases, or storage.
2. Configure the reverse proxy or Cloudflare tunnel so these routes reach the
   API container:
   - `https://nexumathjf.com/studio`;
   - `https://nexumathjf.com/studio/factory/bootstrap`;
   - `https://studio.nexumathjf.com/`;
   - `https://api.nexumathjf.com/health`.
3. Keep `https://aula.nexumathjf.com/` routed to the existing Aula workflow.
4. Run a public-domain routing smoke before data cutover. A 401/403 is
   acceptable on protected Studio routes, but 404 is not acceptable after
   deployment.
5. Run authenticated Studio Factory smoke after routing works.
6. Run strict cutover readiness only after migration counts, backup and rollback
   evidence are present.

## Post-Design Constitution Check

| Principle | Gate Result | Notes |
|-----------|-------------|-------|
| Remote-First Source Of Truth | PASS | Design moves source of truth to server only after validated cutover. |
| Data Safety Before Automation | PASS | Migration and promotion contracts require backup, dry-run/report, validation, and rollback. |
| Spec-First Execution | PASS | Requirements, plan, research, data model, contracts, and quickstart are present. |
| Clear Boundary Between Public Web And Internal Factory | PASS | Contracts define public payloads and block private paths/secrets. |
| Observable, Restartable Workflows | PASS | Job lifecycle contract covers progress, recovery, errors, and retries. |

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |

## Agent Context Update

No `.specify` agent-context update script is present in this checkout. The feature context is recorded in this Spec Kit directory and in the Obsidian project note as requested by the user.
