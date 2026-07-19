# Data Model: Enlace Problema-Solucion

## Extension contractual V2

Los modelos siguientes amplian, sin reemplazar destructivamente, las entidades
V1 descritas mas abajo. Sus contratos normativos son:

- `contracts/structural-page-analysis-v2.md`;
- `contracts/problem-solution-map-v2.md`;
- `contracts/ingrid-provisional-traceability-v1.md`;
- `contracts/precision-annotation-v1.md`;
- `contracts/specialized-model-independence-v1.md`.
- `contracts/problem-detector-visual-audit-session-v1.md`.

## Extension de auditoria visual pre-H-PS1

### I. Problem Detector Visual Audit Session

Proyeccion inmutable y no canonica de una revision exacta de mapa para revision
humana antes de H-PS1.

Fields:

- `session_id`, `batch_id`, `stage: pre_h_ps1`, `status`
- `created_by`, `scope`
- `map_ref`: `map_id`, `map_revision`, `map_sha256`, `scope_fingerprint`,
  `context_fingerprint`
- `source_ref`: `pdf_sha256`, `page_count`
- `page_numbers`
- `problem_provisional_unit_refs`, `solution_provisional_unit_refs`,
  `relation_ids`
- `counts`, `gates`, `permissions`, `review`
- `session_fingerprint`

Rules:

- existe una sesion por `map_id + map_revision`;
- listas, conteos, scope, revision y huellas coinciden literalmente con el mapa;
- `map_sha256` y `session_fingerprint` se revalidan al leer;
- cualquier discrepancia produce `visual_audit_blocked`;
- solo expone medios mediante tokens opacos y no contiene autoridad de H-PS1;
- una revision posterior crea otra sesion y conserva la anterior.

## Extension IND-MA-01: anotacion supervisada e independencia operativa

### A. Annotation Document

Una fuente documental indivisible para asignacion de splits.

Fields:

- `document_id`, `source_digest`, `document_kind`, `page_count`
- `book_code`, `book_id`, `source_asset_ref`
- `split`: `train`, `validation`, `test`, `difficult_ood`
- `split_manifest_id`, `contract_version`, `annotation_schema_version`

Rules:

- todas las paginas, crops y unidades derivadas heredan el mismo `split`;
- un `source_digest` no puede pertenecer a mas de un split;
- duplicados exactos y derivados equivalentes se agrupan antes de dividir;
- el split se audita antes de entrenar o evaluar.

### B. Annotated Logical Unit

Identidad supervisada de un problema o solucion, independiente de la region.

Fields:

- `annotation_unit_id`, `unit_kind`: `problem|solution`
- `document_id`, `exercise_set_id`, `source_pages`
- `visible_identifier_raw`, `visible_identifier_normalized`
- `region_ids`, `relation_ids`, `reading_order`
- `source_provisional_unit_ids`, `human_review`

Rules:

- una unidad puede contener varias regiones y atravesar paginas;
- la identidad no se deduce de un unico box;
- un identificador visible es obligatorio salvo ausencia o abstencion registrada.

### C. Region Annotation

Una region geometrica precisa reutilizable como ground truth.

Fields:

- `region_id`, `annotation_unit_id`, `document_id`, `page_number`
- `region_class`: `problem|problem_number|problem_statement|answer_block|formula|table|graph|figure|solution`
- `bbox_norm_xyxy`, `bbox_xyxy`, `reading_order`, `column_index`
- `content_members`, `geometry_quality`, `confidence`
- `contract_version`, `annotation_schema_version`, `annotator`, `human_review`

Rules:

- `bbox_norm_xyxy` cumple `0 <= x1 < x2 <= 1` y `0 <= y1 < y2 <= 1`;
- `problem` contiene todo el material necesario, incluidas las alternativas;
- clases auxiliares refinan el contenido sin reemplazar la envolvente `problem`;
- contenido de otra unidad invalida `unit_boundary_valid`.

### D. Answer Block Annotation

Subtipo relacional de `Region Annotation` para alternativas.

Fields:

- `answer_block_id`, `parent_problem_unit_id`, `region_id`
- `block_index`, `alternative_labels_observed`, `alternative_count_observed`
- `expected_alternative_count`, `completeness_status`
- `continuity_kind`: `single_block|discontinuous_block`

Rules:

- un problema puede tener uno o varios bloques;
- un bloque contiene una region visual continua, no obliga a un box por opcion;
- todas las opciones del problema deben quedar cubiertas entre sus bloques;
- preguntas abiertas usan `completeness_status: not_applicable` y cero bloques;
- ninguna opcion de un problema vecino puede quedar dentro del bloque.

### E. Geometry Quality Record

Controles observables por region o unidad.

Fields:

- `content_complete`
- `foreign_content_excluded`
- `unit_boundary_valid`
- `alternatives_complete`
- `visible_identifier_captured`
- `continuation_supported`
- `geometry_precise`
- `warnings`, `inclusion_exceptions`, `evidence`

Cada control usa `pass|fail|uncertain|not_applicable`. Un control obligatorio
`fail` o `uncertain` sin resolver bloquea H-PS2. Las bandas superiores,
inferiores y las areas extremas generan advertencias, no decisiones automaticas.

### F. Annotation Relation

Fields:

- `relation_id`, `relation_type`
- `source_ids`, `target_ids`, `document_id`
- `source_pages`, `target_pages`, `evidence`, `confidence`
- `contract_version`, `human_review`

`relation_type` admite `contains`, `belongs_to`, `continues_on`,
`continues_from`, `solves`, `has_answer_block`, `precedes` y `same_entity`.

Una continuidad exige evidencia positiva en ambos extremos. Encabezados, pies,
numeros de pagina y franjas sin contenido matematico son evidencia negativa.

### G. Annotation Dataset Release

Fields:

- `dataset_id`, `dataset_version`, `schema_version`, `contract_version`
- `document_manifest`, `split_manifest`, `annotation_manifest`
- `class_counts`, `relation_counts`, `quality_summary`
- `source_digests`, `approved_by`, `approved_at`, `status`

States:

```text
draft -> validated -> human_approved -> frozen
draft|validated -> rejected
frozen -> superseded
```

### H. Specialized Model Evaluation

Fields:

- `evaluation_id`, `capability_id`, `model_id`, `model_version`
- `dataset_id`, `test_document_ids`, `ood_document_ids`
- `metrics`, `critical_error_audit`, `abstention_rate`
- `independence_index`, `manual_intervention_rate`
- `human_decision`, `rollback_target`, `status`

Rules:

- las metricas se calculan sobre documentos no vistos;
- la media global no compensa un error critico sistematico;
- un candidato sin abstencion o rollback no puede promoverse;
- `promoted` requiere dataset congelado, umbrales aprobados y decision humana.

### A. Structural Page Analysis V2

Un registro no canonico por cada pagina inspeccionada por Gottfried.

Fields:

- `analysis_run_id`, `page_number`, `printed_page_number`
- `content_roles[]`: roles editoriales detallados y multietiqueta
- `audit_roles.mapping_version`, `audit_roles.roles[]`
- `page_sections[]`: regiones `coarse_rect` en `normalized_0_1`
- `page_statistics`
- `confidence`, `evidence`, `uncertainty_reasons`, `review_status`

Rules:

- `page_role_mapping_v1` es la unica conversion vigente de roles detallados a
  `theory`, `problem` y `solution`.
- Cada region cumple `0 <= x1 < x2 <= 1` y `0 <= y1 < y2 <= 1`.
- Toda region declara `precision: coarse` y `usable_as_final_box: false`.
- Las regiones no tienen crop, hash de crop ni autoridad de segmentacion.

### B. Page Statistics

Fields:

- `problem_units`
- `proposed_problems`
- `solved_problems`
- `solution_units`
- `worked_examples`
- `other_elements[]`
- `validations.problem_partition_ok`
- `validations.solution_count_valid`
- `validations.statistics_consistent`

Cada metrica conserva `estimate`, `minimum_estimate`, `maximum_estimate`,
`confidence` y `evidence`.

Rules:

- `problem_units = proposed_problems + solved_problems`, tambien para los
  limites minimo y maximo.
- `worked_examples` permanece fuera de `problem_units`.
- Una solucion no crea un problema adicional y `solution_units` cuenta
  unidades logicas, no fragmentos ni paginas.
- Todos los valores son estimaciones estructurales no canonicas.

### C. Map Eligibility

Evaluacion posterior al analisis estructural completo y anterior a cualquier
mapa problema-solucion.

Fields:

- `eligibility_id`, `analysis_run_id`, `scope`, `source`
- `status`: `eligible_full`, `eligible_partial`, `pending_review`,
  `not_eligible`
- `confidence`, `reason_code`, `reason`, `evidence`, `priority`
- `can_generate_map`, `should_generate_now`, `generate_map`, `activate_ingrid`
- `authority`, `context_fingerprint`

Rules:

- Gottfried determina la elegibilidad y recomienda el momento de generacion.
- Euler debe autorizar expresamente `generate_map: true` con huella vigente.
- `activate_ingrid: true` solo puede aparecer en una asignacion de Euler para
  un mapa `handoff_ready` aprobado por H-PS1.

### D. Provisional Unit

Referencia estructural de Gottfried dentro de una revision de mapa.

Fields:

- `provisional_unit_id`: `Pnnn` o `Snnn`
- `provisional_unit_ref`
- `unit_kind`: `problem` o `solution`
- `source_pages`, `source_section_ids`, `reading_order`
- `confidence`, `evidence`, `unit_fingerprint`
- `predecessor_provisional_unit_refs`, `compatibility_status`

Rules:

- La estabilidad se limita a `map_id + map_revision + exercise_set_id`.
- Nunca se promueve automaticamente como ID canonico.
- Una revision nueva invalida solo los scopes cuyo `scope_fingerprint` cambia.

### E. Provisional Refinement Relation

Relacion explicita entre las unidades provisionales de Gottfried y las
unidades visuales precisas de Ingrid.

Fields:

- `relation_id`, `assignment_id`, `scope`
- `relation_type`: `exact`, `split`, `merge`, `reclassify`,
  `boundary_adjustment`, `rejected`, `newly_discovered`
- `source_provisional_unit_ids[]`, `target_unit_ids[]`
- `reason`, `evidence`, `context_fingerprint`, `expected_revision`
- `human_review`

Rules:

- Admite uno a uno, uno a muchos, muchos a uno y cero a muchos para
  `newly_discovered`.
- Un descubrimiento que cambie paginas, roles, elegibilidad o estructura vuelve
  a Gottfried y reabre H-PS1.
- Ninguna relacion pasa a staging sin H-PS2.

## 1. Instance Structure Map

Represents the editorial organization of one existing book instance.

Fields:

- `schema_version`
- `structure_mode`: `separate_sections`, `interleaved`, `hybrid`, `no_solutions`, `unknown`
- `solution_status`: `identified`, `confirmed_absent`, `external_source`, `uncertain`, `pending_review`
- `exercise_set_id`
- `problem_page_selection`
- `solution_page_selection`
- `solution_source`
- `review_status`
- `updated_at`

Rules:

- Problem and solution page sets may overlap.
- A legacy instance has empty solution pages, `unknown` mode and `pending_review` status.
- `external_source` requires a document reference confirmed by a human before link generation.

## 2. Problem Unit

Projection of one reviewed staging problem used by the linker.

Fields:

- `unit_id`
- `record_id`
- `book_id`, `instance_id`, `exercise_set_id`
- `number_raw`, `number_normalized`
- `page_span`
- `box_ids`
- `answer_block_ids`
- `answer_block_status`: `complete|incomplete|not_applicable|uncertain`
- `crop_paths`
- `reading_order`
- `column_index`
- `source_digest`

Rules:

- The `record_id` must identify one non-continuation staging problem.
- Page and box changes alter `source_digest` and invalidate derived links.
- Every visible alternative must be covered by the parent `problem` and exactly
  one related `answer_block`; multiple answer blocks may belong to one problem.
- Open-ended problems use `answer_block_status: not_applicable`.

## 3. Solution Fragment

One continuous visual region of a solution on one page.

Fields:

- `fragment_id`
- `page_number`
- `bbox_xyxy`
- `crop_path`
- `crop_sha256`
- `fragment_role`: `single`, `begin`, `middle`, `end`
- `reading_order`
- `column_index`
- `geometry_quality`
- `relation_ids`

Rules:

- Coordinates must be ordered and non-negative.
- Existing crop assets must match their registered digest when a digest is present.
- The fragment contains only solution-local heading, development, required
  figures and final answer; recurring page furniture and neighboring units are
  excluded unless an inclusion exception is documented.
- A fragment cannot participate in a multipage sequence unless
  `continuation_supported: pass` and the reciprocal page relation exists.

## 4. Solution Unit

One identifiable solution composed of one or more ordered fragments.

Fields:

- `unit_id`
- `book_id`, `instance_id`, `exercise_set_id`
- `number_raw`, `number_normalized`
- `solution_kind`: `worked`, `short_answer`, `hint`, `unknown`
- `fragments`
- `page_span`
- `variant_index`
- `source_mapping_status`
- `source_digest`
- `provenance.source_version`, `provenance.review_version`
- `continuation_complete`

Rules:

- Fragments must have unique IDs and a valid single/begin-middle-end sequence.
- A solution with incomplete continuity cannot enter a confirmed bundle.
- Multiple solution units may target one problem.

## 5. Candidate Link

Auditable proposal relating one problem unit and one solution unit.

Fields:

- `candidate_link_id`
- `pattern`
- `relation_kind`: `one_to_one`, `alternative_solution`, `shared_solution`
- `problem_ref`, `solution_ref`
- `signals`
- `score`, `runner_up_score`, `score_margin`
- `gates`
- `decision`: `link_proposed_high`, `review_required`, `weak_candidate`, `conflict`, `orphan`, `rejected`
- `ambiguity_reasons`
- `human_review`
- `provenance`
- `candidate_evidence_fingerprint`, `review_fingerprint`

State transitions:

```text
generated -> link_proposed_high|review_required|weak_candidate|conflict|orphan
link_proposed_high|review_required -> human_confirmed|rejected
human_confirmed -> bundled
any derived state -> stale when source_digest changes
```

Rules:

- Both units must share book, instance and exercise set.
- A conflicting explicit number caps the candidate below reviewable automatic proposal.
- Human confirmation records reviewer, timestamp and optional comment.

## 6. Problem-Solution Bundle

Canonical promotion input for one problem and all confirmed visual solutions.

Fields:

- `schema_version`
- `bundle_id`
- `idempotency_key`
- `problem_record_id`
- `scope`
- `problem`
- `solutions`
- `confirmed_link_ids`
- `human_review`
- `provenance`
- `dependency_snapshot`: semantic page map plus unit, candidate, review and event fingerprints
- `status`: persisted review state `human_confirmed`;
- `promotion_status`: derived state `ready_for_db`, `promoted`, `blocked` or `stale`
- `created_at`, `updated_at`

Rules:

- Every solution must come from a human-confirmed link targeting the same problem.
- All source assets and digests must validate before promotion.
- `idempotency_key` is stable for the problem identity and confirmed solution digests.
- A bundle becomes stale when any referenced source digest changes.
- A semantic page-map change archives active candidates and invalidates affected bundles.

## 7. Canonical Visual Solution Group

Stored with the official problem for immediate use before semantic normalization.

Fields:

- `solution_group_id`
- `variant_index`
- `images`
- `fragments` with page, box, role and digest
- `source`
- `link` with method, score, confirmation and evidence
- `bundle_id`

Relationship:

```text
Official Problem 1 --- N Canonical Visual Solution Group
```

Later OCR/LaTeX solution rows may reference the visual group without replacing its provenance.

## 8. Review Event

Append-only audit entry for a candidate or bundle decision.

Fields:

- `event_id`
- `target_type`, `target_id`
- `action`: `confirm`, `reassign`, `reject`, `mark_orphan`, `invalidate`
- `before`, `after`
- `reviewer`, `comment`, `created_at`

Rules:

- Link review events never mutate detector-correction history.
- Reassignment creates a new candidate/bundle version and retains the previous event.

## 9. Per-Problem Solution Status

Append-only human decision used only when an opted-in solution workflow has no
bundle for a specific problem.

Fields:

- `record_id`
- `status`: `pending_review` or `solutions_absent_confirmed`
- `reviewer`, `comment`, `reviewed_at`

Rules:

- `solutions_absent_confirmed` is forbidden while an unresolved candidate still
  points to the problem.
- A configured solution workflow blocks problem-only promotion until it has a
  confirmed bundle, global `confirmed_absent`, or this per-record terminal state.
