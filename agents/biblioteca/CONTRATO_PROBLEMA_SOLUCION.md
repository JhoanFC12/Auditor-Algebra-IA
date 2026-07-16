---
contract_id: library_problem_solution_agent_contract_v1
version: 1.1
status: active_staging_pilot
approved_on: 2026-07-16
agents:
  - euler_library_factory_coordinator_v1
  - gottfried_leibniz_v1
  - ingrid_daubechies_v1
technical_spec: specs/004-problem-solution-linking/spec.md
---

# Contrato compartido - Flujo problema-solucion

## 1. Decision y alcance

Este contrato activa de forma controlada el trabajo problema-solucion para los agentes de Biblioteca. Sustituye unicamente las declaraciones anteriores que mantenian este flujo completamente diferido. No cambia las decisiones de organizacion documental, teoria canonica, OCR semantico, entrenamiento o autoridad humana.

La autorizacion actual permite:

- construir y aprobar el mapa estructural de problemas y soluciones de una instancia;
- revisar boxes de problemas y unidades visuales de solucion en staging;
- proponer enlaces problema-solucion;
- revisar esos enlaces con un humano;
- preparar una vista previa de promocion.

No autoriza por si sola:

- mover, borrar o sobrescribir PDFs;
- modificar el dataset fuente de entrenamiento;
- agregar una clase YOLO `solution` al modelo actual;
- entrenar, promover o desplegar modelos;
- ejecutar OCR semantico sobre las soluciones;
- clasificar problemas contra la futura base teorica;
- escribir automaticamente en la BD canonica;
- omitir cualquiera de los gates humanos definidos aqui.

Una escritura real en BD sigue requiriendo una accion humana explicita sobre la vista previa de promocion y un Promotor controlado.

## 2. Fuentes tecnicas obligatorias

Este contrato debe interpretarse junto con:

1. `specs/004-problem-solution-linking/spec.md`;
2. `specs/004-problem-solution-linking/data-model.md`;
3. `specs/004-problem-solution-linking/contracts/page-selection-v2.md`;
4. `specs/004-problem-solution-linking/contracts/problem-solution-linking-api.md`;
5. `specs/004-problem-solution-linking/contracts/promotion-bundle.md`.

Si un perfil antiguo contradice este contrato solamente respecto del flujo problema-solucion, prevalece este contrato por ser la decision humana mas reciente. Las contradicciones sobre cualquier otro tema se muestran al humano y no se resuelven por inferencia.

## 3. Separacion de responsabilidades

| Participante | Responsabilidad | No le corresponde |
|---|---|---|
| Euler | seleccionar la instancia, emitir asignaciones, verificar dependencias, abrir gates y auditar el cierre | analizar paginas, dibujar boxes, confirmar enlaces o escribir directamente en BD |
| Gottfried | identificar la estructura editorial, conjuntos de ejercicios y rangos de problemas/soluciones con evidencia | segmentar boxes, resolver problemas o decidir enlaces individuales |
| Ingrid | revisar visualmente las paginas autorizadas y producir boxes/unidades de solucion trazables en staging | cambiar la estructura de Gottfried, confirmar enlaces canonicos, entrenar o promover a BD |
| Enlazador | generar propuestas deterministas y evidencia de correspondencia | convertir una puntuacion en verdad canonica |
| Humano | aprobar mapas, boxes, enlaces, relaciones externas y promociones | ningun agente puede sustituirlo |
| Promotor controlado | validar y escribir el paquete aprobado de forma atomica e idempotente | promover propuestas pendientes o evidencia obsoleta |

## 4. Dos modos de Ingrid que nunca se mezclan

### Modo A - Revision del dataset

```text
capability_id: problem_detector_training_dataset_reviewer_v1
mode: dataset_review
```

Conserva el piloto existente:

- fuente inmutable;
- workspace versionado;
- clases YOLO `problem`, `problem_number` y `answer_block`;
- `baseline_labels` y evidencia antes/despues;
- correcciones pendientes de revision humana;
- cero instancias, OCR, entrenamiento o BD.

### Modo B - Segmentacion de una instancia

```text
capability_id: instance_problem_solution_segmenter_v1
mode: instance_staging
```

Trabaja sobre una instancia existente y un mapa estructural confirmado:

- no escribe labels del dataset de entrenamiento;
- no produce muestras de entrenamiento por defecto;
- usa unidades JSON de staging, no una cuarta clase YOLO;
- revisa los boxes de problema que entren en el alcance y forma unidades de solucion;
- entrega propuestas pendientes de aprobacion humana;
- no decide el enlace canonico ni promueve a BD.

El `capability_id` es obligatorio. Si falta o es ambiguo, Ingrid usa `blocked_ambiguous_assignment` y no actua.

El ID heredado `problem_segmentation_reviewer_v1` queda deprecado porque no distingue ambos modos. Ingrid usa `blocked_deprecated_capability` y exige que Euler reemita una capacidad activa; no existe traduccion automatica.

## 5. Flujo obligatorio y gates

```text
Euler selecciona libro, instancia y conjunto
-> Gottfried propone mapa estructural y rangos
-> H-PS1: humano confirma estructura, paginas y relacion documental
-> Euler asigna a Ingrid el modo instance_staging
-> Ingrid revisa paginas y propone boxes/unidades
-> H-PS2: humano aprueba o corrige boxes
-> servicio registra unidades aprobadas en staging
-> Enlazador genera candidatos con evidencia
-> H-PS3: humano confirma, reasigna, rechaza o marca huerfano
-> servicio forma paquetes versionados
-> Euler audita la vista previa
-> H-PS4: humano autoriza o rechaza la promocion
-> Promotor controlado escribe problema, soluciones y origen atomicamente
```

Ninguna salida de un paso autoriza automaticamente el siguiente. Un gate parcial se limita al libro, instancia, conjunto, pagina, box, enlace u operacion identificados.

## 6. Contrato de Gottfried

### 6.1 Entrada

```yaml
schema_version: gottfried_problem_solution_mapping_assignment_v1
assignment_id: ""
batch_id: ""
agent_id: gottfried_leibniz_v1
capability_id: book_problem_solution_mapper_v1
mode: shadow_analysis
book_code: ""
book_id: null
instance_type: ""
instance_id: null
exercise_set_id: ""
pdf_path: ""
pdf_sha256: ""
page_count: 0
approved_pages: []
expected_revision: 0
input_context_fingerprint: ""
human_comments: []
required_outputs:
  - problem_solution_map
definition_of_done: []
status: proposed
```

El libro, instancia, PDF, hash y numero real de paginas deben ser identificables. Gottfried puede proponer un `exercise_set_id`, pero no declararlo confirmado sin gate humano.

### 6.2 Trabajo permitido

Gottfried:

1. inspecciona todas las paginas necesarias para comprender la estructura;
2. identifica practicas, listas, capitulos, examenes, concursos, claves y solucionarios;
3. separa paginas de enunciados y paginas de soluciones;
4. permite que una pagina tenga ambos roles;
5. determina si la disposicion es separada, intercalada, hibrida, sin soluciones o desconocida;
6. distingue solucion identificada, ausencia confirmable, fuente externa, incertidumbre y revision pendiente;
7. registra evidencia por pagina y toda incertidumbre;
8. propone una relacion documental cuando el solucionario sea externo.

Gottfried no dibuja boxes, no cuenta problemas como dato canonico, no enlaza ejercicios individuales y no confirma por si mismo que una solucion externa pertenece al libro.

### 6.3 Salida

```yaml
schema_version: gottfried_problem_solution_map_v1
map_id: ""
assignment_id: ""
status: mapping_requires_human
map_revision: 0
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
problem_page_selection:
  schema_version: library_instance_page_selection_v1
  selected_pages: []
  page_ranges: []
  review_status: pending
solution_page_selection:
  schema_version: library_instance_solution_page_selection_v1
  selected_pages: []
  page_ranges: []
  review_status: pending
problem_solution_structure:
  schema_version: library_instance_problem_solution_structure_v1
  structure_mode: separate_sections|interleaved|hybrid|no_solutions|unknown
  solution_status: identified|confirmed_absent|external_source|uncertain|pending_review
  exercise_set_id: ""
  source_mapping_confirmed: false
document_relation:
  external: false
  status: not_applicable|proposed|confirmed|rejected
  document_reference: ""
evidence: []
uncertainties: []
human_decisions_required: []
context_fingerprint: ""
```

Reglas:

- `status` usa solamente `mapping_requires_human`, `mapping_confirmed`, `handoff_ready` o `mapping_blocked`;
- `selected_pages` y `page_ranges` se normalizan y no pueden salir de `1..page_count`;
- las selecciones de problema y solucion pueden superponerse;
- `confirmed_absent` solo se guarda despues de confirmacion humana;
- `source_mapping_confirmed` solo cambia a `true` despues de H-PS1;
- un documento externo requiere referencia estable y confirmacion humana;
- un cambio semantico incrementa `map_revision`, produce un nuevo `context_fingerprint` e invalida el gate anterior.

### 6.4 Estados de Gottfried

```text
mapping_requested
mapping_in_progress
mapping_requires_human
mapping_confirmed
handoff_ready
mapping_blocked
```

`handoff_ready` requiere H-PS1 aprobado. Antes de ese estado Euler no puede activar a Ingrid sobre la instancia.

## 7. Contrato de Ingrid para instancias

### 7.1 Entrada

```yaml
schema_version: ingrid_instance_segmentation_assignment_v1
assignment_id: ""
agent_id: ingrid_daubechies_v1
capability_id: instance_problem_solution_segmenter_v1
mode: instance_staging
scope:
  book_code: ""
  book_id: null
  instance_type: ""
  instance_id: null
  exercise_set_id: ""
expected_revision: 0
structure_snapshot:
  map_id: ""
  map_revision: 0
  context_fingerprint: ""
  map_status: handoff_ready
  structure_mode: separate_sections|interleaved|hybrid
  solution_status: identified|external_source
  problem_selected_pages: []
  solution_selected_pages: []
  source_mapping_confirmed: true
h_ps1_gate_ref:
  request_id: ""
  stage: problem_solution_structure
  artifact_ref:
    schema_version: gottfried_problem_solution_map_v1
    artifact_id: ""
    context_fingerprint: ""
    expected_revision: 0
  decision:
    action: approve
    reviewer: ""
    comment: ""
    decided_at: ""
  status: approved
document_relation: null
source_pages: []
problem_units: []
detector_proposals: []
human_comments: []
```

Son obligatorios `book_code`, `instance_type` y `exercise_set_id`; no existen comodines vacios. El mapa debe estar `handoff_ready` y los rangos confirmados. `h_ps1_gate_ref` debe apuntar al mismo `map_id`, `map_revision` y huella, con decision humana `approve`. El `expected_revision` superior pertenece al workspace problema-solucion; no se confunde con la revision del mapa. Si `solution_status` es `external_source`, `document_relation` debe estar confirmada y contener una referencia estable.

Ingrid no amplia rangos ni cambia `structure_mode`. Si la evidencia visual contradice el mapa, devuelve `structure_mismatch_requires_gottfried` y se reabre H-PS1.

### 7.2 Revision visual

Ingrid inspecciona la pagina completa y no solo los boxes existentes. Debe detectar:

- problemas o soluciones omitidos;
- falsos positivos;
- boxes cortados o sobredimensionados;
- dos unidades unidas o una unidad dividida;
- numeracion visible incompleta;
- alternativas separadas del problema;
- orden de lectura incorrecto;
- continuaciones entre paginas;
- final de una solucion y comienzo de otro problema en la misma pagina.

En modo instancia, `solution` es un rol visual de staging. No es una clase YOLO ni modifica `dataset.yaml`.

### 7.3 Revision de box de problema en instancia

```yaml
schema_version: ingrid_instance_problem_box_review_v1
review_id: ""
assignment_id: ""
scope: {}
problem_record_id: ""
page_number: 0
source_page_ref: ""
source_page_sha256: ""
image_width: 0
image_height: 0
context_fingerprint: ""
expected_revision: 0
original_boxes:
  - box_id: ""
    role: problem|problem_number|answer_block
    bbox_xyxy: [0, 0, 0, 0]
    parent_box_id: null
proposed_boxes:
  - box_id: ""
    role: problem|problem_number|answer_block
    bbox_xyxy: [0, 0, 0, 0]
    parent_box_id: null
operations:
  - action: accept|add|remove_false_positive|move|resize|reclassify|split|merge|reorder|abstain
    target_box_id: ""
    before: null
    after: null
    reason: ""
issues_found: []
reasoning_summary: ""
overlay_before: ""
overlay_after: ""
source_version: ""
review_version: ""
status: accepted_unchanged|agent_corrected_pending_human|abstained
human_review: pending
```

Este esquema pertenece exclusivamente a una instancia. No modifica labels YOLO ni se convierte automaticamente en dato de entrenamiento. Cada box debe tener ID estable, rol permitido, coordenadas ordenadas dentro de la imagen y procedencia verificable. `reasoning_summary` contiene solo criterios observables breves.

### 7.4 Unidad de solucion

```yaml
unit_id: ""
scope:
  book_code: ""
  book_id: null
  instance_type: ""
  instance_id: null
  exercise_set_id: ""
number_raw: ""
number_normalized: ""
number_bbox_xyxy: null
solution_kind: worked|short_answer|hint|unknown
variant_index: 1
page_span: [0, 0]
continuation_complete: false
source_mapping_status: confirmed
source_digest: ""
fragments:
  - fragment_id: ""
    page_number: 0
    bbox_xyxy: [0, 0, 0, 0]
    crop_path: ""
    crop_sha256: ""
    fragment_role: single|begin|middle|end
    reading_order: 0
    column_index: null
provenance:
  source_version: ""
  review_version: ""
```

No se inventa numeracion. `number_raw`, `number_normalized` y `number_bbox_xyxy` pueden quedar vacios cuando el identificador no sea visible.

Una solucion de un fragmento usa `single` y `continuation_complete: true`. Una solucion multipagina usa exactamente:

```text
begin -> middle* -> end
```

Todos los fragmentos deben tener ID, pagina, box valido, crop, hash y orden. Si falta una parte o la continuidad es dudosa, `continuation_complete` queda en `false` y la unidad no puede aprobarse para staging.

Metodos alternativos se registran como unidades o variantes diferentes. No se fusionan solamente por compartir numero.

`unit_id`, `bbox_xyxy` y `crop_sha256` son los nombres canonicos de entrada definidos por el modelo tecnico. El servicio de staging puede materializar el alias interno `solution_unit_id`; Ingrid no emite aliases heredados como `bbox_px` o `sha256`.

### 7.5 Salida

```yaml
schema_version: ingrid_instance_solution_segmentation_v1
assignment_id: ""
scope: {}
context_fingerprint: ""
expected_revision: 0
source_version: ""
review_version: ""
pages_inspected: []
problem_box_reviews: []
solution_units: []
issues_found: []
evidence_overlays: []
status: agent_segmented_pending_human
human_review: pending
```

Cada elemento de `problem_box_reviews` usa `ingrid_instance_problem_box_review_v1`; cada elemento de `solution_units` usa el esquema canonico de esta seccion. Las correcciones de boxes y las decisiones de enlace se almacenan por separado.

### 7.6 Aplicacion controlada despues de H-PS2

H-PS2 aprobado no significa que staging ya cambio. La aplicacion se realiza en este orden:

1. el `InstanceProblemBoxApplier` consume solo `ingrid_instance_problem_box_review_v1` aprobados, aplica la geometria mediante el servicio de boxes de la Fabrica y regenera o invalida los problemas staging afectados;
2. el operador recarga el estado y obtiene los nuevos hashes, registros y revision del workspace;
3. el `SolutionUnitStagingWriter` envia las `solution_units` aprobadas a `/api/problem-solutions/solution-units` con la revision recien observada;
4. Euler verifica que ambos resultados coincidan con el scope y la evidencia aprobada;
5. solo entonces el estado pasa a `segmentation_confirmed` y puede ejecutarse el Enlazador.

En el piloto actual, el aplicador de boxes usa la operacion humana existente `/api/pages/boxes`. Mientras esa ruta no exponga `expected_revision`, debe ejecutarse serialmente desde la interfaz humana, con recarga inmediatamente antes y despues. Euler e Ingrid no pueden invocarla de forma autonoma. Un cambio concurrente, una huella distinta o un registro regenerado fuera del gate devuelve el flujo a H-PS2.

### 7.7 Estados de Ingrid en modo instancia

```text
assigned
validating_inputs
reviewing_pages
agent_segmented_pending_human
human_approved_for_staging
human_rejected
structure_mismatch_requires_gottfried
stale
abstained
blocked
blocked_ambiguous_assignment
blocked_deprecated_capability
```

Solo `human_approved_for_staging` puede entregarse al endpoint de unidades de solucion. Ingrid puede informar `no_solution_observed`, pero no puede declarar `confirmed_absent`, `solutions_absent_confirmed`, `human_confirmed`, `ready_for_db` ni `promoted`.

## 8. Enlaces y autoridad humana

El Enlazador puede usar numeracion, conjunto, orden, proximidad, columnas y estructura editorial para proponer candidatos. Una puntuacion alta sigue siendo una propuesta.

Las unicas decisiones humanas de enlace son:

```text
confirm
reassign
reject
mark_orphan
```

Ingrid puede aportar evidencia visual, pero no ejecuta esas decisiones en nombre del humano. Una reasignacion debe actualizar la referencia y huella del problema destino y conservar la referencia original en el historial.

Un problema sin solucion requiere ausencia global confirmada o una decision humana por problema. Una observacion automatica de Ingrid no satisface ese gate.

## 9. Invalidaciones y concurrencia

Una salida queda obsoleta cuando cambia cualquiera de estos elementos:

- PDF, hash o numero de paginas;
- libro, instancia o `exercise_set_id`;
- paginas o rangos de problemas/soluciones;
- `structure_mode` o `solution_status`;
- documento externo confirmado;
- imagen, box, crop, hash, orden o rol de fragmento;
- version de fuente o revision;
- problema o solucion referenciados.

Consecuencias:

1. el mapa vuelve a revision cuando cambia su evidencia estructural;
2. las unidades afectadas pasan a `stale`;
3. los candidatos activos se archivan, no se borran silenciosamente;
4. los paquetes derivados quedan bloqueados;
5. una ausencia confirmada vuelve a `pending_review` cuando aparece evidencia nueva;
6. el historial humano se conserva de forma append-only.

Toda mutacion usa `expected_revision`. Ante conflicto se recarga el estado y se presenta nuevamente la propuesta. Ningun agente sobrescribe ni reintenta a ciegas.

Cada gate humano referencia de forma inmutable el artefacto revisado mediante su `artifact_id`, `schema_version`, `context_fingerprint` y `expected_revision`. La decision registra accion, revisor, comentario y fecha; una aprobacion sin esas referencias no habilita el paso siguiente.

Para H-PS1, `artifact_ref.expected_revision` referencia `map_revision`. Para H-PS2 a H-PS4, referencia la revision del workspace o artefacto mutable correspondiente.

## 10. Gates de calidad

### H-PS1 - Estructura

- PDF, hash y `page_count` verificados;
- cobertura estructural de paginas: `100 %` o abstencion explicita;
- rangos fuera del PDF: `0`;
- conjunto e instancia identificables;
- incertidumbres visibles;
- relacion externa confirmada cuando corresponda.

### H-PS2 - Boxes y unidades

- paginas inspeccionadas dentro del alcance: `100 %`;
- boxes degenerados: `0`;
- glifos, formulas, figuras o alternativas necesarias cortadas: `0` en golden humano;
- crops sin hash o procedencia: `0`;
- unidades multipagina incompletas aprobadas: `0`;
- mezcla entre labels de dataset y unidades de instancia: `0`.

### H-PS3 - Enlaces

- enlaces canonicos sin decision humana: `0`;
- enlaces entre scopes diferentes: `0`;
- candidatos obsoletos promovidos: `0`;
- problemas sin bundle ni ausencia revisada promovidos: `0`.

### H-PS4 - Promocion

- paquetes pendientes o invalidos escritos: `0`;
- escrituras parciales: `0`;
- duplicados por reintento: `0`;
- escritura sin aprobacion humana explicita: `0`.

Las metricas de precision, recall e IoU se calculan contra un golden humano; no se declaran por inspeccion informal.

## 11. Casos minimos del piloto

El piloto controlado debe incluir:

1. seccion separada con problemas y soluciones numeradas;
2. problema seguido por su solucion;
3. pagina con ambos roles;
4. estructura hibrida;
5. solucion multipagina;
6. numeracion duplicada en conjuntos distintos;
7. problema sin solucion;
8. solucionario externo confirmado;
9. reasignacion humana;
10. cambio de box o rango que invalide derivados.

## 12. Definicion de cierre

```text
structure_confirmed
-> segmentation_confirmed
-> link_review_confirmed
-> problem_solution_reviewed
-> promotion_preview_ready
-> promotion_approved
-> promotion_executed
-> complete_bd
```

`problem_solution_reviewed` es el cierre maximo sin autorizacion de promocion. `promotion_approved` no significa que la transaccion ya se ejecuto. `promotion_executed` exige evidencia de commit y `complete_bd` exige ademas auditoria posterior satisfactoria.
