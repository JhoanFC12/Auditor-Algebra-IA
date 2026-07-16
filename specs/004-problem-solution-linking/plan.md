# Implementation Plan: Enlace Problema-Solucion

**Branch**: `[004-problem-solution-linking]` | **Date**: 2026-07-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-problem-solution-linking/spec.md`

## Summary

Ampliar la Fábrica para que una instancia conserve selecciones independientes de paginas de enunciados y soluciones, formar unidades visuales auditables, proponer enlaces segun la estructura editorial, exigir revision humana y promover cada problema con sus soluciones confirmadas en una transaccion idempotente. La primera version reutiliza `config_snapshot`, staging y la promocion existente; no entrena ni promueve modelos.

## Technical Context

**Language/Version**: Python 3.11.9 y JavaScript ES2020 compatible con navegador moderno

**Primary Dependencies**: biblioteca estandar de Python, servicio HTTP existente de `instance_factory`, interfaz web vanilla, adaptador PostgreSQL del proyecto

**Storage**: `config_snapshot` de instancia, archivos JSON versionados bajo staging y PostgreSQL para datos oficiales

**Testing**: `unittest` con dobles de controlador/DB y pruebas de contrato del servidor web

**Target Platform**: Fábrica de Auditor-IA en Windows para operacion local controlada y codigo portable al servicio remoto

**Project Type**: aplicacion web monorepo con backend Python y frontend JavaScript

**Performance Goals**: generar propuestas para 500 problemas y 500 soluciones en menos de un segundo sin lectura de imagenes; guardar una decision individual en menos de 500 ms en almacenamiento local saludable

**Constraints**: compatibilidad con snapshots antiguos; cero promociones sin confirmacion humana; rollback por paquete; no duplicar activos; no exponer rutas privadas en contratos publicos; no mezclar correcciones de boxes con correcciones de enlaces

**Scale/Scope**: una instancia a la vez, hasta 5 000 unidades por conjunto, multiples fragmentos y soluciones alternativas; detector de soluciones y OCR de soluciones fuera de alcance

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Evidence |
|---|---|---|
| Remote-First Source Of Truth | PASS | El dominio y los contratos son portables; la escritura oficial sigue usando el perfil de BD configurado y no crea una segunda verdad local. |
| Data Safety Before Automation | PASS | Staging, confirmacion humana, validacion de activos, idempotencia y rollback por paquete son obligatorios. |
| Spec-First Execution | PASS | La funcion tiene spec, checklist, research, modelo, contratos, quickstart y tareas separadas. |
| Public/Internal Boundary | PASS | Los candidatos internos conservan procedencia, mientras las respuestas web usan referencias controladas. |
| Observable, Restartable Workflows | PASS | Estados de enlace y promocion quedan persistidos e idempotentes para recuperar el trabajo. |

## Project Structure

### Documentation (this feature)

```text
specs/004-problem-solution-linking/
|-- spec.md
|-- plan.md
|-- research.md
|-- data-model.md
|-- quickstart.md
|-- contracts/
|   |-- page-selection-v2.md
|   |-- problem-solution-linking-api.md
|   `-- promotion-bundle.md
|-- checklists/requirements.md
`-- tasks.md
```

### Source Code (repository root)

```text
modulos/instance_factory/
|-- library_api.py
|-- models.py
|-- problem_solution_linking.py
|-- db_promotion.py
|-- web_server.py
`-- web/
    |-- app.js
    `-- styles.css

tests/
|-- test_library_web_api.py
|-- test_problem_solution_linking.py
|-- test_instance_factory_db_promotion.py
`-- test_instance_factory_web_server.py
```

**Structure Decision**: Integrar la funcion en `modulos/instance_factory`, porque ahi viven el contexto de instancia, staging, revision y promocion. El enlazador sera un modulo de dominio puro con almacenamiento de staging; la UI y el servidor solo adaptan sus contratos.

## Phase 0: Research Decisions

See [research.md](./research.md).

## Phase 1: Design Artifacts

- [data-model.md](./data-model.md)
- [contracts/page-selection-v2.md](./contracts/page-selection-v2.md)
- [contracts/problem-solution-linking-api.md](./contracts/problem-solution-linking-api.md)
- [contracts/promotion-bundle.md](./contracts/promotion-bundle.md)
- [quickstart.md](./quickstart.md)

No agent-context update script exists in `.specify/scripts`; project context remains in this feature package.

## Implementation Phases

### Phase A - Page Roles And Instance Context

1. Preserve `selected_pages` as problem pages for backward compatibility.
2. Add sibling solution-page selection plus structure and solution-status enums.
3. Restore both selections into `InstancePipelineContext` and the web UI.
4. Allow overlap and persist legacy requests without deleting solution metadata.

### Phase B - Linker And Review Workspace

1. Add pure domain contracts for problem units, solution units, candidate links and bundles.
2. Persist solution units, candidates, reviews and bundles under instance staging with atomic file replacement.
3. Generate candidates for separated and interleaved layouts with explicit evidence and conflict states.
4. Expose review endpoints and a side-by-side UI surface.

### Phase C - Atomic Promotion

1. Attach only human-confirmed bundles to their staging problem records.
2. Validate solution assets and link state during promotion preflight.
3. Store rich visual-solution groups with the problem in the same DB transaction and origin update.
4. Preserve idempotence and isolate rollback to the affected problem bundle.

### Phase D - Validation

1. Run focused unit and contract tests.
2. Verify legacy problem-only promotion remains unchanged.
3. Run a local quickstart with one separated and one interleaved fixture.
4. Confirm no model training, environment mutation or production deployment occurred.

## Post-Design Constitution Check

| Principle | Gate | Notes |
|---|---|---|
| Remote-First Source Of Truth | PASS | The same contracts can be carried to the remote Factory adapter. |
| Data Safety Before Automation | PASS | Canonical writes require confirmed bundles and per-record transactions. |
| Spec-First Execution | PASS | Design artifacts resolve all implementation choices. |
| Public/Internal Boundary | PASS | Internal paths stay in staging/audit payloads and existing file-token routes serve media. |
| Observable, Restartable Workflows | PASS | Candidate, review, bundle and promotion states persist independently. |

## Complexity Tracking

No constitutional violations are required.

## Implementation Status

**Status**: implemented and covered by the local test suite on 2026-07-15.

Delivered:

- independent and overlapping problem/solution page roles with legacy preservation;
- deterministic linking for separated, interleaved and hybrid review flows;
- staging-only solution units, candidates, append-only reviews and versioned bundles;
- human confirm, reassign, reject and orphan decisions with bundle reconciliation;
- stale source/asset detection and optimistic revision conflicts;
- atomic problem, visual solutions and origin promotion with non-destructive solution merging;
- rollback-safe staging decisions covering candidate, event, bundle and record attachment in one revision;
- strict scope/provenance contracts and page-role filtering with semantic map invalidation;
- durable managed solution crops, write-boundary bundle revalidation and retired-group cleanup;
- explicit external-document confirmation, completed-continuation gates and per-problem absence decisions;
- true cross-problem reassignment with regenerated-evidence preservation;
- solution-unit change invalidation and record-writer serialization around recoverable review transactions;
- bundle IDs, solution totals and blocking evidence in API and UI responses.
- portable Euler-Gottfried-Ingrid operating contracts with separate Ingrid dataset/instance capabilities, immutable human-gate references and synchronized Obsidian supersession notes.

Validation:

- 267 focused/regression tests passed in 84.771 seconds;
- a read-only independent audit found no remaining P0/P1 issue in the five final hardening areas;
- affected Python modules compile;
- browser JavaScript passes syntax validation;
- `git diff --check` passes;
- no model was trained or promoted, no `.env` was changed and no production deployment was performed.

## Known Limits

- V1 does not detect solution boxes automatically. Ingrid or another reviewed segmentation source must submit valid solution units and fragment hashes.
- Solutions enter the official problem first as audited visual groups. Semantic solution OCR/LaTeX and theorem-based classification remain later stages.
- High-scoring links are proposals only; every first-version link still requires a human decision.
- A problem without a confirmed bundle requires either global `confirmed_absent` or an explicit per-problem human absence decision.
- External solucionarios are supported only after a human supplies and confirms their document reference.
- The existing `/api/pages/boxes` correction route does not expose `expected_revision`; Ingrid can only propose instance box reviews, and the controlled pilot keeps their application human-operated and serialized until a guarded adapter is implemented.
- This run used test doubles for PostgreSQL failure and rollback scenarios; it did not write to a production database.

## Next Controlled Pilot

Use one non-production book instance with a small reviewed sample:

1. Map one separated section with five numbered problems and their five solutions.
2. Map one interleaved section with five problem-solution pairs, including one multipage solution.
3. Have Ingrid submit reviewed solution boxes and hashes.
4. Confirm, reassign, reject and orphan at least one candidate each.
5. Mark one genuinely unsolved problem with the explicit absence decision and verify a pending candidate cannot bypass it.
6. Run promotion preview, inspect the bundles and then explicitly approve a local-mirror write.
7. Re-run the same promotion to verify zero duplicate visual solution groups before expanding the batch.
8. Exercise one external-solucionario mapping in dry-run mode before enabling that source type for real books.
