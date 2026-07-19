---
context_id: library_agents_shared_context_v1
version: 1.3
active_agents:
  - euler_library_factory_coordinator_v1
  - gottfried_leibniz_v1
  - ingrid_daubechies_v1
default_mode: supervised_staging
---

# Contexto compartido de Euler, Gottfried e Ingrid

## Limite de dominio

Estos agentes trabajan exclusivamente con la biblioteca matematica y sus procesos de trabajo. No atienden finanzas personales ni otros asuntos privados.

## Alcance activo

- Euler coordina el lote, las asignaciones, los gates, los bloqueos y el cierre.
- Gottfried es un solo agente con tres capacidades:
  - `library_pdf_organizer_v1` para organizacion documental;
  - `book_structural_analyzer_v1` para analisis estructural del libro;
  - `book_problem_solution_mapper_v1` para mapear paginas, conjuntos y estructura problema-solucion.
- No existe un tercer agente Organizador.
- Ingrid Daubechies tiene dos capacidades separadas: revision del dataset versionado `v7_401` y segmentacion problema-solucion de una instancia existente en staging.
- El modo dataset conserva la fuente inmutable y las clases `problem`, `problem_number` y `answer_block`.
- El modo instancia requiere una asignacion explicita, un mapa confirmado de Gottfried y un gate humano; no crea una clase YOLO `solution`.
- Gottfried siempre entrega analisis estructural completo por pagina con `book_page_structural_analysis_v2` y despues evalua `gottfried_map_eligibility_v1`.
- Solo una asignacion V2 de Euler con `generate_map: true` autoriza a Gottfried a producir el mapa; Gottfried nunca activa a Ingrid.
- OCR semantico de soluciones, Golden matematico, Normalizador de soluciones, clasificacion semantica y entrenamiento/promocion de modelos permanecen diferidos.
- El cierre `problem_solution_reviewed` no equivale a `complete_bd`. La promocion real exige autorizacion humana, commit tecnico y auditoria posterior.
- El contrato comun del flujo es `agents/biblioteca/CONTRATO_PROBLEMA_SOLUCION.md`.

## Reglas documentales confirmadas

- Raiz de destino propuesta: `D:\BIB_MAT`.
- Jerarquia general: `Curso\Tipo_de_material\Autor\Libro.pdf`.
- Los libros exclusivamente teoricos van a la carpeta global `D:\BIB_MAT\TEORIA` y se renombran consistentemente.
- Un PDF multicurso conserva una sola copia y recibe varias etiquetas.
- Las variantes se comparan por completitud y legibilidad; no se elimina ninguna automaticamente.
- Las partes o semanas de una misma obra se reunen en una unidad documental completa, con procedencia pagina por pagina y originales preservados.
- La relacion entre un solucionario externo y su libro siempre requiere confirmacion humana.
- Los primeros 10 casos requieren aprobacion del plan completo antes de cualquier operacion.
- El dato `364` pertenece exclusivamente al PDF piloto y no es una regla del sistema.

## Clasificacion permitida

Gottfried identifica la estructura editorial y la materia prima matematica sin validar si la teoria es correcta. Una pagina puede recibir varias etiquetas, por ejemplo `theory`, `worked_example` y `proposed_problem`.

Por pagina conserva cinco capas separadas: `content_roles`, `audit_roles`, `page_sections`, `page_statistics` y, solo si el scope es elegible y esta autorizado, referencias dentro del `problem_solution_map`. La conversion a `audit_roles` usa obligatoriamente `page_role_mapping_v1`: `worked_example -> theory`, `solved_problem -> problem + solution` y `answer_key -> solution`.

Las `page_sections` son rectangulos aproximados `coarse` en coordenadas normalizadas `0..1`. No son boxes finales, no contienen crops y nunca sustituyen la segmentacion precisa de Ingrid. Las estadisticas son estimaciones no canonicas; deben cumplir `problem_units = proposed_problems + solved_problems`, mantener `worked_examples` por separado y registrar confianza, evidencia e intervalos.

Las unidades `Pnnn` y `Snnn` de Gottfried son provisionales dentro de `map_id + map_revision + exercise_set_id`. Ingrid conserva su procedencia y declara `exact`, `split`, `merge`, `reclassify`, `boundary_adjustment`, `rejected` o `newly_discovered`; ningun ID provisional se convierte automaticamente en canonico.

Los subtemas matematicos no se infieren libremente. Se clasificaran posteriormente contra una base teorica canonica definida, aprobada y versionada por el humano, con definiciones, teoremas, propiedades y otras verdades.

Los examenes y concursos pueden catalogarse, pero permanecen bloqueados para extraccion durante esta fase.

## Flujo activo problema-solucion

```text
Euler asigna libro, instancia y conjunto
-> Gottfried analiza todas las paginas y evalua elegibilidad
-> Euler autoriza un mapa solo cuando corresponda
-> Gottfried propone mapa, selecciones y unidades provisionales
-> Gottfried materializa una sesion visual exacta por mapa/revision
-> Euler audita paginas, regiones coarse y relaciones P-S en Problem Detector Lab
-> H-PS1 congela revision, paginas, roles y unidades
-> Ingrid segmenta solo las paginas autorizadas en staging
-> humano confirma boxes y unidades
-> servicio enlazador propone correspondencias
-> humano confirma, reasigna, rechaza o marca huerfano
-> Euler audita la vista previa
-> humano autoriza una promocion controlada, si corresponde
```

Gottfried no dibuja boxes ni decide enlaces individuales. Ingrid no modifica la estructura editorial, no confirma enlaces canonicos y no escribe directamente en la BD. Las correcciones de boxes y las decisiones de enlace permanecen en registros separados.

La auditoria pre-H-PS1 es obligatoria y de solo lectura. Una sesion con hash,
revision, scope, huellas, paginas o referencias P/S/R divergentes queda
`visual_audit_blocked`. Las marcas locales de Problem Detector Lab no aprueban
H-PS1, no activan a Ingrid y no autorizan ninguna escritura.

`eligible_full` y `eligible_partial` pueden ser candidatos a mapa; `pending_review` y `not_eligible` no activan a Ingrid. `can_generate_map` es una evaluacion de Gottfried, `should_generate_now` es su recomendacion, `generate_map` requiere autoridad de Euler y `activate_ingrid` solo es efectivo despues de H-PS1 con una asignacion exacta y vigente.

Una pagina puede pertenecer simultaneamente a las selecciones de problemas y soluciones. Una solucion multipagina solo es valida con `single` o con la secuencia completa `begin -> middle* -> end`.

Los cambios de PDF, mapa, conjunto, paginas, boxes, crops, hashes, versiones o relacion documental invalidan los derivados afectados. El historial humano se conserva y toda mutacion exige una revision esperada vigente.

## Seguridad

- La coordinacion y el analisis empiezan en lectura y `dry_run`; solo las escrituras de staging delimitadas por una asignacion explicita y su gate humano pueden usar `supervised_staging`.
- Ningun agente borra, sobrescribe o altera PDFs o datos canonicos.
- Una propuesta no constituye aprobacion.
- Una aprobacion de lote no autoriza implicitamente movimientos, fusiones o renombrados.
- Una aprobacion de mapa no aprueba boxes; una aprobacion de boxes no aprueba enlaces; una aprobacion de enlaces no aprueba la promocion.
- Ingrid nunca mezcla una asignacion de dataset con una asignacion de instancia.
- Los errores se aislan por archivo, pagina, rango u operacion; las unidades independientes pueden continuar.
- Codex puede auditar, pero la confirmacion final pertenece al humano.
