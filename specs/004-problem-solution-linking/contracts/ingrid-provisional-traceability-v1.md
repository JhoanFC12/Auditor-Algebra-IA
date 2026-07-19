# Contract: Ingrid Provisional Traceability V1

Este contrato vincula las unidades estructurales provisionales de Gottfried con
las unidades visuales precisas creadas por Ingrid. No convierte los IDs
provisionales en identidades canonicas.

## Entrada obligatoria

La asignacion de Ingrid debe citar:

- `gottfried_problem_solution_map_v2` en estado `handoff_ready`;
- `map_id`, `map_revision`, `exercise_set_id`, `scope_fingerprint` y
  `context_fingerprint` aprobados por H-PS1;
- `page_role_manifest_ref` con `mapping_version` compatible;
- paginas autorizadas;
- unidades provisionales `Pnnn` y `Snnn` dentro del scope;
- `expected_revision` vigente del workspace.

Las `page_sections` de Gottfried son solo contexto `coarse`. Ingrid inspecciona
la pagina original y produce geometria precisa en pixeles.

## Relacion de refinamiento

```yaml
schema_version: ingrid_provisional_refinement_v1
relation_id: ""
assignment_id: ""
scope: {}
relation_type: exact
source_provisional_unit_ids: []
target_unit_ids: []
reason: ""
evidence: []
context_fingerprint: ""
expected_revision: 0
human_review: pending
```

`relation_type` admite:

| Tipo | Cardinalidad permitida |
|---|---|
| `exact` | uno a uno |
| `split` | uno a muchos |
| `merge` | muchos a uno |
| `reclassify` | uno a uno con cambio de tipo |
| `boundary_adjustment` | uno a uno con cambio geometrico |
| `rejected` | uno o muchos a cero |
| `newly_discovered` | cero a uno o muchos |

Reglas:

- `source_provisional_unit_ids` nunca se reemplaza por el ID preciso;
- `split` y `merge` conservan todos los IDs fuente;
- `newly_discovered` dentro de paginas ya autorizadas puede proponerse para
  H-PS2 si no cambia roles, rangos ni elegibilidad;
- si un descubrimiento exige ampliar paginas, cambiar `audit_roles`, modificar
  `map_eligibility` o contradecir la estructura, Ingrid devuelve
  `structure_mismatch_requires_gottfried` y se reabre H-PS1;
- ninguna relacion pasa a staging sin H-PS2;
- una relacion no crea por si sola un enlace canonico problema-solucion.

## Extension de unidad de solucion

Cada unidad precisa de Ingrid agrega:

```yaml
unit_id: ""
source_provisional_unit_ids: []
provisional_refinement:
  relation_id: ""
  relation_type: exact
fragments: []
```

Cada revision precisa de problema agrega los mismos campos
`source_provisional_unit_ids` y `provisional_refinement`.

## Invalidacion y reutilizacion

- un cambio del `scope_fingerprint` vuelve `stale` solo las relaciones del
  scope afectado;
- una relacion puede reutilizarse cuando el nuevo mapa declara
  `compatible_reused`, conserva `unit_fingerprint` y el operador verifica la
  compatibilidad;
- cambios de box, crop, hash, orden o tipo invalidan la unidad precisa y sus
  derivados, aunque el ID provisional siga siendo compatible;
- el historial se conserva de forma append-only.

## Calidad minima

- unidades precisas sin relacion o justificacion `newly_discovered`: `0`;
- IDs provisionales convertidos automaticamente en IDs canonicos: `0`;
- regiones `coarse` usadas como boxes finales: `0`;
- relaciones fuera del scope H-PS1: `0`;
- `split` o `merge` sin trazabilidad completa: `0`.

