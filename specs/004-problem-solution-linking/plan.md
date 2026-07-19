# Implementation Plan: Enlace Problema-Solucion

**Branch**: `[004-problem-solution-linking]` | **Date**: 2026-07-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-problem-solution-linking/spec.md`

## Summary

Ampliar la Fabrica para que una instancia conserve selecciones independientes de paginas de enunciados y soluciones, forme unidades visuales auditables, detecte todos los bloques de alternativas, excluya contenido ajeno de los boxes, reconstruya continuidades con evidencia positiva y proponga enlaces revisables. IND-MA-01 agrega un segundo horizonte: convertir las anotaciones supervisadas de Gottfried e Ingrid en ground truth relacional para capacidades especializadas que reduzcan progresivamente la intervencion manual, sin entrenar ni promover modelos dentro de este incremento de planificacion.

## Technical Context

**Language/Version**: Python 3.11.9 y JavaScript ES2020 compatible con navegador moderno

**Primary Dependencies**: biblioteca estandar de Python, servicio HTTP existente de `instance_factory`, interfaz web vanilla, adaptador PostgreSQL del proyecto y contratos de anotacion relacional proyectables a datasets de vision; la arquitectura final de modelos permanece desacoplada del contrato

**Storage**: `config_snapshot` de instancia, archivos JSON versionados bajo staging, datasets supervisados congelados con manifiestos por documento y PostgreSQL para datos oficiales

**Testing**: `unittest` con dobles de controlador/DB, pruebas de contrato, validadores geometricos y relacionales, golden humano estratificado y auditoria de fuga entre splits

**Target Platform**: Fábrica de Auditor-IA en Windows para operacion local controlada y codigo portable al servicio remoto

**Project Type**: aplicacion web monorepo con backend Python y frontend JavaScript

**Performance Goals**: conservar los objetivos V1 de enlace y revision; para IND-MA-01 alcanzar `>=95 %` en estructura/problemas/soluciones/continuidad/enlace, `>=98 %` de cobertura de alternativas, `<=1 %` de contenido ajeno critico y, en operacion regular, `>=90 %` de independencia con `<=10 %` de intervencion manual

**Constraints**: compatibilidad con snapshots antiguos; cero promociones sin confirmacion humana; rollback por paquete y por version de modelo; no duplicar activos; no exponer rutas privadas; no mezclar correcciones de boxes con correcciones de enlaces; splits por documento completo; las regiones `coarse` nunca son boxes; agentes y modelos no heredan autoridad de los gates humanos

**Scale/Scope**: una instancia a la vez para revision, hasta 5 000 unidades por conjunto y documentos completos para dataset; multiples fragmentos, varios `answer_block` y soluciones alternativas; dos capacidades especializadas minimas, sin fijar un unico modelo fisico; entrenamiento efectivo y OCR de soluciones fuera de alcance

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Evidence |
|---|---|---|
| Remote-First Source Of Truth | PASS | El dominio y los contratos son portables; la escritura oficial sigue usando el perfil de BD configurado y no crea una segunda verdad local. |
| Data Safety Before Automation | PASS | Staging, confirmacion humana, validacion de activos, idempotencia, abstencion, splits auditados y rollback por paquete/version son obligatorios. |
| Spec-First Execution | PASS | La funcion y IND-MA-01 tienen requisitos medibles, research, modelo, contratos, quickstart y fases separadas. |
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
|   |-- precision-annotation-v1.md
|   |-- specialized-model-independence-v1.md
|   |-- structural-page-analysis-v2.md
|   |-- problem-solution-map-v2.md
|   |-- ingrid-provisional-traceability-v1.md
|   |-- page-selection-v2.md
|   |-- problem-solution-linking-api.md
|   `-- promotion-bundle.md
|-- checklists/requirements.md
`-- tasks.md
```

### Source Code (repository root)

```text
modulos/instance_factory/
|-- annotation_contracts.py
|-- annotation_quality.py
|-- supervised_annotations.py
|-- document_splits.py
|-- specialized_model_evaluation.py
|-- library_api.py
|-- models.py
|-- problem_solution_linking.py
|-- db_promotion.py
|-- web_server.py
`-- web/
    |-- app.js
    `-- styles.css

tests/
|-- test_precision_annotation_contract.py
|-- test_supervised_annotation_export.py
|-- test_document_split_leakage.py
|-- test_specialized_model_evaluation.py
|-- test_library_web_api.py
|-- test_problem_solution_linking.py
|-- test_instance_factory_db_promotion.py
`-- test_instance_factory_web_server.py
```

**Structure Decision**: Mantener enlace, staging y revision en `modulos/instance_factory`. Agregar una capa contractual de anotacion y calidad independiente del formato de entrenamiento; los exportadores futuros proyectaran esa capa hacia datasets concretos sin convertir YOLO ni un agente en fuente canonica del dominio.

## Phase 0: Research Decisions

See [research.md](./research.md).

Las decisiones nuevas resuelven: uno o varios bloques continuos de
alternativas, perfiles de exclusion por clase, continuidad con evidencia
positiva, anotacion relacional, splits por documento, capacidades
arquitectura-agnosticas y gate reversible de sustitucion de agentes.

## Phase 1: Design Artifacts

- [data-model.md](./data-model.md)
- [contracts/page-selection-v2.md](./contracts/page-selection-v2.md)
- [contracts/problem-solution-linking-api.md](./contracts/problem-solution-linking-api.md)
- [contracts/promotion-bundle.md](./contracts/promotion-bundle.md)
- [contracts/structural-page-analysis-v2.md](./contracts/structural-page-analysis-v2.md)
- [contracts/problem-solution-map-v2.md](./contracts/problem-solution-map-v2.md)
- [contracts/ingrid-provisional-traceability-v1.md](./contracts/ingrid-provisional-traceability-v1.md)
- [contracts/precision-annotation-v1.md](./contracts/precision-annotation-v1.md)
- [contracts/specialized-model-independence-v1.md](./contracts/specialized-model-independence-v1.md)
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

### Phase E - Precision Annotation Contract

1. Materialize class-specific inclusion/exclusion profiles for `problem`,
   `problem_number`, `answer_block` and `solution`.
2. Allow one or several contiguous `answer_block` regions per problem and
   validate that every visible alternative is covered exactly once.
3. Add geometry-quality controls, evidence, inclusion exceptions and abstention.
4. Require positive reciprocal evidence for all multipage continuities.
5. Make H-PS2 reject omitted alternatives, foreign critical content, unsupported
   continuities and unresolved geometry uncertainty.

### Phase F - Relational Ground Truth

1. Export region, unit and relation annotations without losing page or reviewer
   provenance.
2. Preserve the relations `contains`, `belongs_to`, `continues_on`,
   `continues_from`, `solves`, `has_answer_block`, `precedes` and `same_entity`.
3. Freeze approved dataset releases and keep rejected/abstained annotations
   outside training while retaining them for audit.
4. Project task-specific datasets from the relational source instead of making
   a three-class YOLO file the canonical annotation.

### Phase G - Document-Level Evaluation

1. Deduplicate sources and assign complete documents to train, validation, test
   or difficult/OOD.
2. Audit zero document, digest or derivative leakage between splits.
3. Build a golden covering one/two columns, graphical alternatives, open
   questions, mixed pages, headers/footers and true/false continuations.
4. Report aggregate metrics and errors by editorial, course, layout and document
   family so averages cannot hide systematic failures.

### Phase H - Specialized Capability Rollout

1. Evaluate `document_structural_analyzer_v1` and
   `mathematical_region_linker_v1` as capability contracts, independent of the
   chosen model architecture.
2. Require abstention and route low-confidence/OOD cases back to supervised
   review.
3. Progress through offline, shadow and limited operation before regular use.
4. Preserve a rollback target and re-enable Gottfried/Ingrid review when a
   candidate regresses.

## Post-Design Constitution Check

| Principle | Gate | Notes |
|---|---|---|
| Remote-First Source Of Truth | PASS | The same contracts can be carried to the remote Factory adapter. |
| Data Safety Before Automation | PASS | Canonical writes require confirmed bundles; dataset/model releases require frozen manifests, human approval and rollback. |
| Spec-First Execution | PASS | Design artifacts resolve geometry, dataset, split and rollout contracts without selecting an unvalidated model architecture. |
| Public/Internal Boundary | PASS | Internal paths stay in staging/audit payloads and existing file-token routes serve media. |
| Observable, Restartable Workflows | PASS | Candidate, review, annotation release, evaluation and model-rollout states persist independently and can resume or roll back. |

## Complexity Tracking

No constitutional violations are required.

## Implementation Status

**Status**: V1 plus the V2 precision and IND-MA-01 validation infrastructure are implemented and covered locally. The Gottfried structural/mapping phase and the pre-H-PS1 visual audit of the real 20-page pilot are complete. H-PS1 remains pending; Ingrid precision work, dataset freeze and model training/promotion have not started.

**Authorized increment (2026-07-17)**: implement a read-only pre-H-PS1
adapter in Problem Detector Lab. It consumes explicit
`problem_detector_visual_audit_session_v1` manifests, revalidates the exact r0
map and renders pages, provisional P/S/R structure, uncertainty, hashes and
revisions. It has no mutating endpoint and cannot approve H-PS1, activate
Ingrid, create boxes/crops, change maps/PDFs or write canonical data.

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
- a separate read-only Biblioteca audit console inside Problem Detector Lab that overlays Gottfried structural roles and Ingrid precise instance boxes over the same live page;
- version-aware V1 adapters plus native V2 presentation for coarse regions, structural statistics, formal eligibility and provisional-unit traceability;
- opaque media tokens and payload sanitization so the browser never receives private local paths;
- guarded H-PS2 session actions that remain explicitly non-persistent until a controlled human writer is authorized.
- executable `precision-annotation-v1` region, unit and relation validation with class-specific geometry controls, one-or-many answer-block coverage and reciprocal positive continuity evidence;
- opt-in V2 precision gates in the linker that block defective solution bundles while preserving legacy V1 readability;
- read-only Problem Detector Lab precision validation, answer-block overlays, blocker explanations and disabled H-PS2 approval marks while mandatory controls fail;
- deterministic supervised relational releases that admit only valid human-approved annotations and preserve excluded pending, rejected, abstained or invalid rows for audit;
- complete-document split manifests with exact-duplicate, derivative and equivalence-group leakage detection;
- architecture-agnostic IND-MA-01 metric, family-error, abstention, human-approval and rollback gates that only recommend the next rollout state;
- a staging-only artifact validation CLI and a safe synthetic 20-page pilot-manifest contract.
- a GET-only `problem_detector_visual_audit_session_v1` adapter that fails closed on live map, revision, fingerprint, PDF, page, role or ledger divergence and never exposes private paths;
- four Gottfried pre-H-PS1 sessions for `euler-precision-pilot-20p-20260717-r1-phase-b-r1`, rendered side by side with 14 pages, 25 P/S relations, provisional coarse regions, declared uncertainties and full hash/revision inspection;
- an interactive Euler audit of all four sessions with `4/4 ready_for_visual_audit`, zero blockers, zero browser console errors, H-PS1 pending and Ingrid inactive.

Validation:

- 304 focused/regression tests passed in 73.273 seconds, including precision, relational export, document leakage, model-gate and Problem Detector Lab contracts;
- 14 focused Problem Detector Lab tests passed, including valid sessions, hash/revision fail-closed behavior, opaque media, side-by-side UI and non-mutating gate semantics;
- browser validation covered every session, all 14 page cards and all 25 relation cards; first/last and multipage relations loaded both panes and their coarse overlays, with zero console errors;
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
- Existing live campaign artifacts are still V1. The audit console therefore marks missing `page_sections`, `page_statistics` and provisional-unit relations as unavailable; it does not infer or fabricate V2 evidence.
- The current Ingrid batch validated schema, crops and hashes but did not enforce the new semantic-exclusion, alternative-completeness or continuation-evidence controls; it cannot be used as approved precision ground truth without re-review.
- No frozen relational dataset or document-level split manifest exists yet for IND-MA-01.
- The current `v7_401` detector remains a baseline for three visual classes; it is not the canonical schema for solutions, relations or multipage reconstruction.
- H-PS2 buttons in the audit console currently create session-only visual marks. They intentionally do not call `/api/pages/boxes`, staging writers or canonical application/DB routes.
- This run used test doubles for PostgreSQL failure and rollback scenarios; it did not write to a production database.
- The synthetic 20-page manifest validates control shape and case coverage only; it is not evidence that real book pages satisfy the precision thresholds.
- The current real 20-page result validates Gottfried structure, mappings and pre-H-PS1 rendering only. It is not evidence that Ingrid boxes satisfy `precision-annotation-v1` or H-PS2 thresholds.
- Pre-H-PS1 review marks are intentionally local to the browser session. There is no adapter route that approves H-PS1, activates Ingrid or persists a decision.

## Rollback Notes

- V1 artifacts remain readable because precision enforcement is opt-in through `annotation_schema_version` or an embedded `precision_annotation` package.
- Removing or withholding an invalid V2 package returns the artifact to legacy read-only status; it does not mutate staging or canonical records.
- Dataset releases cannot become `frozen` without deterministic validation and explicit human approval; a rejected release remains auditable.
- Model evaluation never changes runtime state automatically. Every report sets `automatic_promotion: false`, preserves the configured rollback target and requires a human to execute any recommended transition.

## Next Controlled Pilot

Do not repeat the full 538-page Ingrid batch first. Gottfried's structure,
mapping and visual audit for the 20-page pilot are complete. The next controlled
sequence is:

1. Let the human review the four pre-H-PS1 sessions and issue an explicit H-PS1
   decision per exact r0 map; visual conformity marks do not constitute that gate.
2. Only after effective H-PS1, have Ingrid annotate problems, identifiers, every answer block and solution
   fragment under `precision-annotation-v1`.
3. Compare overlays with a human golden and require zero omitted/neighboring
   alternatives, zero recurring furniture accepted as solution and zero false
   header continuations.
4. Correct the contract or execution until all required geometry controls pass;
   only then open H-PS2 for that pilot.
5. Export the approved annotations to a draft relational dataset and audit all
   required fields/relations without training.
6. Build a document-level split proposal and prove zero leakage before selecting
   any model architecture.
7. Expand to the rest of the first lot only after the human approves the pilot
   report and its exclusion examples.
