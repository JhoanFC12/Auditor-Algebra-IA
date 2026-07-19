# Contract: Problem Detector Visual Audit Session V1

Este contrato materializa una vista visual, no canonica y de solo lectura, de
una revision exacta de `gottfried_problem_solution_map_v2` antes de H-PS1. La
sesion permite auditar el mapa en Problem Detector Lab; no cambia el mapa ni
constituye una decision humana.

## Artefacto de sesion

Cada mapa y revision tiene un manifiesto independiente bajo
`visual_audit_sessions/<session_id>/session.json`:

```yaml
schema_version: problem_detector_visual_audit_session_v1
session_id: ""
batch_id: ""
stage: pre_h_ps1
status: ready_for_visual_audit
created_by:
  agent_id: gottfried_leibniz_v1
  capability_id: book_problem_solution_mapper_v1
scope:
  book_code: ""
  book_id: null
  instance_type: ""
  instance_id: null
  exercise_set_id: ""
map_ref:
  map_id: ""
  map_revision: 0
  map_sha256: ""
  scope_fingerprint: ""
  context_fingerprint: ""
source_ref:
  pdf_sha256: ""
  page_count: 0
page_numbers: []
problem_provisional_unit_refs: []
solution_provisional_unit_refs: []
relation_ids: []
counts:
  pages: 0
  problems: 0
  solutions: 0
  relations: 0
gates:
  h_ps1: pending
  activate_ingrid: false
  handoff_ready: false
permissions:
  read_only: true
  canonical_writes: false
  boxes_or_crops: false
  map_mutation: false
  pdf_mutation: false
review:
  status: pending
  predecessor_session_id: null
session_fingerprint: ""
```

`session_fingerprint` es el SHA-256 de la serializacion JSON canonica del
manifiesto sin ese campo (`sort_keys=true`, separadores compactos y UTF-8).

## Materializacion y validacion

- Gottfried materializa una sesion por cada `map_id + map_revision`; no usa
  comodines ni agrupa varios mapas en una sola sesion.
- `map_sha256` debe coincidir con los bytes vivos del mapa referenciado.
- scope, revision, huellas, paginas y listas P/S/R deben coincidir literalmente
  con el mapa vivo. Los conteos se derivan de esas listas y deben ser exactos.
- las paginas deben pertenecer a `problem_page_selection` o
  `solution_page_selection`; no se amplian rangos durante la visualizacion.
- el adaptador resuelve mapas, ledgers estructurales e imagenes solo dentro del
  staging configurado y devuelve URLs opacas, nunca rutas privadas.
- cualquier ausencia, discrepancia de hash, revision, scope, huella o referencia
  produce `visual_audit_blocked`; una vista parcial no puede presentarse como
  lista para H-PS1.
- una nueva revision crea una nueva sesion y enlaza la anterior mediante
  `predecessor_session_id`; no reescribe la sesion historica.

## Contenido visual obligatorio

La vista de cada sesion muestra:

- paginas completas y sus roles V2;
- regiones `coarse` de Gottfried, marcadas como provisionales y no utilizables
  como boxes finales;
- unidades P y S, numeros editoriales, paginas, secciones, orden, confianza,
  evidencia, incertidumbres y huellas;
- relaciones R lado a lado, con ambas paginas y sus regiones provisionales;
- `batch_id`, `session_id`, `map_id`, `map_revision`, `map_sha256`,
  `pdf_sha256`, `scope_fingerprint`, `context_fingerprint` y
  `session_fingerprint`;
- estado de revision y gates pendientes.

## Limites de autoridad

La API de estas sesiones es exclusivamente `GET`. El adaptador no puede:

- aprobar H-PS1 ni persistir una marca que aparente esa aprobacion;
- activar a Ingrid;
- crear boxes o crops precisos;
- modificar el mapa, el manifiesto estructural o el PDF;
- escribir en la app, staging canonico o base de datos;
- invocar `/api/pages/boxes` ni rutas de promocion.

Las marcas de inspeccion que use la interfaz permanecen en memoria del
navegador y se etiquetan como `solo_sesion`. H-PS1 requiere una orden humana
posterior, explicita y referida a las huellas auditadas.
