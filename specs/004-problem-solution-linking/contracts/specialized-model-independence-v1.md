# Contract: Specialized Model Independence V1

**Indicator**: `IND-MA-01`

Este contrato impide que la operacion regular dependa permanentemente de
Gottfried, Ingrid u otro agente de razonamiento general. Durante la etapa
supervisada, sus salidas forman ground truth para modelos especializados; no se
promueven automaticamente a dato canonico ni a entrenamiento.

## Capacidades autonomas minimas

### `document_structural_analyzer_v1`

Debe poder analizar documentos completos pagina por pagina y producir:

- roles editoriales multietiqueta;
- limites de capitulos, secciones e instancias;
- tipo documental: libro, separata, practica, solucionario o mixto;
- paginas vacias, ilegibles, cifradas o ambiguas;
- orden editorial y unidades multipagina;
- propuestas estructurales con confianza y abstencion.

Su contrato objetivo es compatible con `book_page_structural_analysis_v2` y
`gottfried_problem_solution_map_v2`. No hereda autoridad para aprobar H-PS1.

### `mathematical_region_linker_v1`

Debe poder producir:

- problemas y numeros;
- enunciados y regiones matematicas auxiliares;
- uno o varios bloques de alternativas completos;
- soluciones desarrolladas y sus fragmentos;
- agrupacion de unidades multipagina;
- relaciones problema-solucion;
- confianza, evidencia y abstencion.

Su geometria cumple `precision-annotation-v1.md`. No hereda autoridad para
aprobar H-PS2, H-PS3 o H-PS4.

Una capacidad puede implementarse mediante uno o varios modelos o etapas. El
contrato evalua la capacidad completa, no obliga a una arquitectura concreta.

## Papel transitorio de los agentes

Gottfried e Ingrid pueden:

- producir anotaciones supervisadas;
- corregir propuestas automaticas;
- documentar casos ambiguos;
- validar muestras y formar ground truth;
- auditar baja confianza y familias fuera de distribucion;
- curar ejemplos dificiles para versiones posteriores.

No deben convertirse en requisito de procesamiento completo para cada nuevo
documento una vez que la capacidad especializada supera su gate.

## Paquete minimo de anotacion reutilizable

```yaml
schema_version: supervised_relational_annotation_v1
annotation_id: ""
document:
  document_id: ""
  source_digest: ""
  page_count: 0
page:
  page_number: 0
  printed_page_number: null
regions: []
units: []
relations: []
confidence: 0.0
uncertainty_reasons: []
contract_version: ""
annotation_schema_version: ""
annotator:
  agent_id: ""
  capability_id: ""
review:
  status: pending|approved|corrected|rejected|abstained
  reviewer: ""
  reviewed_at: null
```

Las relaciones minimas son `contains`, `belongs_to`, `continues_on`,
`continues_from`, `solves`, `has_answer_block`, `precedes` y `same_entity`.

Un overlay aislado o una descripcion narrativa no constituyen un paquete de
entrenamiento.

## Separacion obligatoria por documento

```yaml
schema_version: document_split_manifest_v1
dataset_id: ""
splits:
  train: []
  validation: []
  test: []
  difficult_ood: []
deduplication_report: ""
leakage_audit:
  documents_in_multiple_splits: 0
  source_digests_in_multiple_splits: 0
  derivative_leaks: 0
status: pending|passed|failed
```

Reglas:

- todas las paginas de un documento pertenecen al mismo split;
- crops, overlays y derivados heredan el split de su documento;
- duplicados exactos o equivalentes se agrupan antes de dividir;
- un split por instancia solo es valido si no comparte documento fuente con
  otro split;
- `test` y `difficult_ood` permanecen fuera de entrenamiento y ajuste de
  umbrales.

## Metricas de evaluacion

### Piloto

- clasificacion estructural de paginas: `>= 95 %`;
- precision y exhaustividad de problemas: `>= 95 %`;
- cobertura integra de alternativas: `>= 98 %`;
- precision de soluciones: `>= 95 %`;
- continuidad entre paginas: `>= 95 %`;
- vinculacion problema-solucion: `>= 95 %`;
- unidades con contenido ajeno critico: `<= 1 %`;
- intervencion manual requerida: `<= 15 %`.

### Operacion regular

- indice de independencia operativa: `>= 90 %`;
- intervencion manual: `<= 10 %`;
- errores criticos sistematicos en una familia evaluada: `0`.

```text
independence_index =
  correct_units_without_agent_intervention / evaluated_units * 100
```

Una unidad es correcta solamente si sus regiones obligatorias estan
completas, no omite alternativas, no incorpora contenido ajeno critico,
reconstruye sus fragmentos, identifica su posicion estructural y establece la
relacion problema-solucion cuando corresponde.

## Auditoria de errores criticos

Son errores criticos, como minimo:

- problema o solucion omitidos;
- alternativa omitida, cortada o asignada al problema vecino;
- encabezado, pie o numero de pagina aceptado como solucion o continuidad;
- dos unidades fusionadas o una unidad dividida incorrectamente;
- relacion problema-solucion falsa;
- fuga de un documento de prueba hacia entrenamiento;
- salida de baja confianza aceptada sin abstencion o gate humano.

La media global no compensa un patron critico repetido en una editorial,
curso, layout o tipo documental.

## Gate de sustitucion progresiva

```text
draft_dataset
-> validated_dataset
-> human_approved_dataset
-> model_candidate
-> offline_evaluated
-> critical_error_audited
-> human_approved_model
-> shadow_operation
-> limited_operation
-> regular_operation
```

Requisitos acumulativos:

1. dataset revisado, versionado y congelado;
2. separacion por documento verificada;
3. metricas y errores documentados en libros no vistos;
4. mecanismo de abstencion;
5. aprobacion humana;
6. version de rollback disponible;
7. operacion shadow antes de sustituir el flujo supervisado;
8. monitoreo por familia documental y muestreo posterior.

Un fallo vuelve el candidato a `model_candidate` o reactiva la ruta
supervisada. Ningun estado de modelo modifica por si mismo los gates H-PS1 a
H-PS4 ni autoriza escritura canonica.
