# Contract: Gottfried Problem-Solution Map V2

Este contrato separa tres resultados: analisis estructural completo,
elegibilidad para mapear y mapa problema-solucion. No todos los libros que se
analizan deben producir un mapa.

## Evaluacion formal de elegibilidad

```yaml
schema_version: gottfried_map_eligibility_v1
eligibility_id: ""
analysis_run_id: ""
scope:
  book_code: ""
  book_id: null
  instance_type: ""
  instance_id: null
  exercise_set_id: ""
source:
  pdf_path: ""
  pdf_sha256: ""
  page_count: 0
status: pending_review
confidence: 0.0
reason_code: ""
reason: ""
evidence: []
priority: normal
can_generate_map: unknown
should_generate_now: false
generate_map: false
activate_ingrid: false
authority:
  generate_map: euler_assignment_required
  activate_ingrid: h_ps1_and_euler_assignment_required
context_fingerprint: ""
```

Estados:

| Estado | Criterio | `can_generate_map` |
|---|---|---|
| `eligible_full` | existen soluciones desarrolladas suficientes para un mapa completo | `true` |
| `eligible_partial` | existen claves, respuestas breves, pistas o evidencia parcial util | `true` |
| `pending_review` | la evidencia es incierta, incompleta o existe una posible fuente externa no confirmada | `unknown` |
| `not_eligible` | no existe una relacion problema-solucion util; incluye teoria pura y problemas sin evidencia de solucion | `false` |

`priority` admite `high`, `normal`, `low` o `deferred`. Los `reason_code`
minimos son `worked_solutions_detected`, `partial_solution_material`,
`external_solution_candidate`, `uncertain_solution_evidence`,
`problem_only_no_solution`, `theory_only` y `no_problem_solution_scope`.

## Autoridad de los flags

- Gottfried determina `status`, `confidence`, `reason`, `evidence`, `priority`,
  `can_generate_map` y recomienda `should_generate_now`.
- `generate_map: true` requiere una asignacion explicita de Euler que cite esta
  elegibilidad, su huella y la revision vigente. No es una autorizacion que
  Gottfried pueda autoemitir.
- `activate_ingrid` permanece `false` en toda salida de Gottfried.
- `activate_ingrid: true` solo existe en la decision/assignment de Euler cuando
  el mapa esta `handoff_ready`, H-PS1 esta aprobado y scope, huella y revision
  coinciden.
- `pending_review` y `not_eligible` nunca activan a Ingrid.

## Asignacion de mapa V2

```yaml
schema_version: gottfried_problem_solution_mapping_assignment_v2
assignment_id: ""
batch_id: ""
agent_id: gottfried_leibniz_v1
capability_id: book_problem_solution_mapper_v1
mode: shadow_analysis
scope:
  book_code: ""
  book_id: null
  instance_type: ""
  instance_id: null
  exercise_set_id: ""
source:
  pdf_path: ""
  pdf_sha256: ""
  page_count: 0
approved_pages: []
eligibility_ref:
  eligibility_id: ""
  status: eligible_full
  context_fingerprint: ""
generate_map: true
expected_revision: 0
input_context_fingerprint: ""
human_comments: []
required_outputs:
  - problem_solution_map_v2
status: proposed
```

La asignacion se rechaza si `generate_map` no es `true`, la elegibilidad no es
`eligible_full` o `eligible_partial`, o la huella no coincide.

## Salida de mapa V2

```yaml
schema_version: gottfried_problem_solution_map_v2
map_id: ""
assignment_id: ""
status: mapping_requires_human
map_revision: 0
scope: {}
source: {}
eligibility_ref: {}
page_role_manifest_ref:
  schema_version: book_page_structural_analysis_v2
  analysis_run_id: ""
  mapping_version: page_role_mapping_v1
  pdf_sha256: ""
  page_count: 0
  context_fingerprint: ""
page_role_snapshot: []
problem_page_selection: {}
solution_page_selection: {}
problem_solution_structure: {}
provisional_units: []
document_relation: null
evidence: []
uncertainties: []
human_decisions_required: []
scope_fingerprint: ""
context_fingerprint: ""
```

Cada elemento de `page_role_snapshot` incluye `page_number`, `content_roles`,
`audit_roles`, `page_sections_ref`, `page_statistics_ref`, `confidence`,
`evidence` e `uncertainty_reasons`. Solo referencia paginas del scope del mapa.

## Unidades provisionales

```yaml
provisional_unit_id: P001
provisional_unit_ref: "<map_id>:r<map_revision>:<exercise_set_id>:P001"
unit_kind: problem
source_pages: []
source_section_ids: []
reading_order: 0
confidence: 0.0
evidence: []
unit_fingerprint: ""
predecessor_provisional_unit_refs: []
compatibility_status: new
```

Reglas:

- problemas usan `P001`, `P002`, ... y soluciones `S001`, `S002`, ... dentro
  del scope `map_id + map_revision + exercise_set_id`;
- son referencias estructurales provisionales, no IDs canonicos;
- no contienen boxes precisos, crops ni hashes de crops;
- `compatibility_status` admite `new`, `compatible_reused`, `changed` o
  `retired`;
- una nueva revision invalida solamente los scopes cuyo `scope_fingerprint`
  cambio;
- una unidad no afectada puede reutilizarse despues de verificar
  `unit_fingerprint` y registrar su predecesor;
- H-PS1 congela `map_revision`, `page_role_snapshot`, selecciones y unidades
  provisionales del artefacto aprobado.

## Estados

```text
structural_analysis_complete
-> eligibility_evaluated
-> mapping_requested
-> mapping_in_progress
-> mapping_requires_human
-> mapping_confirmed
-> handoff_ready
```

`handoff_ready` requiere H-PS1. Ningun estado anterior permite activar Ingrid.

