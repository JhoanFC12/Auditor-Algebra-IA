# Contract: Structural Page Analysis V2

Este contrato define la salida obligatoria de Gottfried por cada pagina. Amplia
`book_page_structural_analysis_v1` sin convertir a Gottfried en segmentador de
boxes. Los artefactos V1 siguen siendo legibles; las nuevas ejecuciones que
participen en el flujo problema-solucion deben producir V2.

## Registro por pagina

```yaml
schema_version: book_page_structural_analysis_v2
analysis_run_id: ""
page_number: 1
printed_page_number: null
content_roles: []
audit_roles:
  schema_version: library_page_audit_roles_v1
  mapping_version: page_role_mapping_v1
  roles: []
problem_formats: []
contains_problems: uncertain
problem_density: not_applicable
course_candidates: []
editorial_context:
  visible_heading_raw: ""
  editorial_topic_candidates: []
division_id: ""
page_sections: []
page_statistics: {}
confidence: 0.0
evidence:
  image_asset_key: ""
  visible_headings: []
  notes: ""
uncertainty_reasons: []
review_status: pending
human_correction: null
```

`content_roles` es multietiqueta. `audit_roles.roles` solo admite `theory`,
`problem` y `solution`, tambien de forma multietiqueta. Una pagina puede tener
los tres valores simultaneamente.

## Conversion contractual `page_role_mapping_v1`

| `content_role` | `audit_roles` derivados |
|---|---|
| `theory` | `theory` |
| `definition_property_theorem` | `theory` |
| `worked_example` | `theory` |
| `proposed_problem` | `problem` |
| `solved_problem` | `problem`, `solution` |
| `answer_key` | `solution` |
| `solution` | `solution` |
| `cover_or_credits`, `index`, `presentation_or_instructions`, `bibliography_or_appendix`, `advertising`, `blank` | ninguno |
| `unknown` | ninguno y una incertidumbre obligatoria |

Reglas:

- `worked_example` conserva su rol detallado y se contabiliza por separado; no
  crea un `problem_unit`.
- `solved_problem` representa simultaneamente materia prima de problema y de
  solucion.
- una nueva regla de conversion requiere un nuevo `mapping_version`; no se
  modifica silenciosamente `page_role_mapping_v1`.
- la app, Ingrid y cualquier auditor deben consumir el mismo
  `mapping_version`; no pueden redefinir la conversion localmente.

## `page_sections`

Cada elemento representa una zona editorial aproximada, no un box final.

```yaml
section_id: "sec-p0001-001"
geometry_kind: coarse_rect
coordinate_space: normalized_0_1
bbox_norm_xyxy: [0.0, 0.0, 1.0, 1.0]
precision: coarse
content_roles: []
audit_roles: []
reading_order: 0
confidence: 0.0
evidence: []
uncertainty_reasons: []
usable_as_final_box: false
```

Reglas geometricas:

- `0 <= x1 < x2 <= 1` y `0 <= y1 < y2 <= 1`;
- las regiones pueden superponerse cuando el contenido editorial se mezcla;
- `reading_order` es unico dentro de la pagina salvo abstencion explicita;
- `precision` siempre es `coarse` en la salida de Gottfried;
- no contiene crop, hash de crop ni coordenadas de segmentacion en pixeles;
- Ingrid debe inspeccionar la pagina original y no puede convertir estas
  regiones automaticamente en boxes precisos.

## `page_statistics`

Cada metrica usa el mismo objeto:

```yaml
estimate: 0
minimum_estimate: 0
maximum_estimate: 0
confidence: 0.0
evidence: []
```

La estructura completa es:

```yaml
schema_version: library_page_statistics_v1
problem_units: {}
proposed_problems: {}
solved_problems: {}
solution_units: {}
worked_examples: {}
other_elements: []
validations:
  problem_partition_ok: uncertain
  solution_count_valid: uncertain
  statistics_consistent: uncertain
```

`other_elements` contiene objetos `{kind, estimate, minimum_estimate,
maximum_estimate, confidence, evidence}` para claves, pistas, definiciones,
figuras u otros elementos observables.

Invariantes:

```text
problem_units = proposed_problems + solved_problems
problem_units.minimum_estimate = proposed_problems.minimum_estimate + solved_problems.minimum_estimate
problem_units.maximum_estimate = proposed_problems.maximum_estimate + solved_problems.maximum_estimate
```

- `worked_examples` no forma parte de `problem_units`;
- una solucion no crea un problema adicional;
- `solution_units` cuenta unidades logicas estimadas, no fragmentos, paginas ni
  boxes;
- una solucion multipagina puede aparecer en estadisticas de varias paginas;
  no se suman esos valores para obtener un total del libro sin deduplicar por
  las unidades provisionales del mapa;
- una solucion que resuelve varios problemas cuenta como una unidad de
  solucion y puede corresponder a varios `solved_problems`;
- las estadisticas son estructurales, estimadas y no canonicas;
- los estados de validacion admiten `pass`, `fail` o `uncertain`.

## Calidad minima

- cobertura de paginas: `100 %` o abstencion registrada;
- registros con `mapping_version`: `100 %`;
- regiones fuera de `0..1`: `0`;
- regiones presentadas como boxes finales: `0`;
- `problem_partition_ok: fail` sin incertidumbre o correccion: `0`;
- estadisticas presentadas como conteos canonicos: `0`.

