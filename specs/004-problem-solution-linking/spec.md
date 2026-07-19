# Feature Specification: Enlace Problema-Solucion

**Feature Branch**: `[004-problem-solution-linking]`

**Created**: 2026-07-15

**Status**: Implemented V1 locally; V2 precision and specialized-model independence plan in design

**Input**: User description: "Clasificar paginas de enunciados y soluciones, enlazar cada problema con su solucion correspondiente y almacenar ambos juntos en la base oficial despues de revision."

**Additional Input**: `IND-MA-01 - Independencia operativa mediante modelos especializados`: las anotaciones supervisadas de Gottfried e Ingrid deben convertirse en datos versionados que permitan sustituir progresivamente su intervención repetitiva por modelos especializados evaluados sobre documentos no vistos.

## Clarifications

### Session 2026-07-16

- Q: Que debe entregar Gottfried por pagina? -> A: Roles editoriales detallados, roles normalizados `theory|problem|solution`, zonas aproximadas, estadisticas estructurales y un mapa problema-solucion solo cuando exista elegibilidad.
- Q: Que precision tienen las zonas de Gottfried? -> A: Rectangulos aproximados normalizados `0..1`, marcados `coarse`, con orden, confianza, evidencia e incertidumbre; nunca sustituyen boxes de Ingrid.
- Q: Como se cuentan los elementos de pagina? -> A: Conteos jerarquicos, estimados y no canonicos con `problem_units = proposed_problems + solved_problems`; ejemplos y soluciones no crean problemas adicionales.
- Q: Como se relacionan unidades de Gottfried e Ingrid? -> A: IDs provisionales versionados y relaciones `exact|split|merge|reclassify|boundary_adjustment|rejected|newly_discovered`, sin conversion automatica a identidad canonica.
- Q: Cuando se genera el mapa? -> A: Despues del analisis completo se evalua `eligible_full|eligible_partial|pending_review|not_eligible`; solo una autorizacion efectiva permite generar el mapa y solo H-PS1 mas una asignacion exacta de Euler permite activar Ingrid.

### Session 2026-07-17

- Q: Como se representan las alternativas? -> A: El box principal `problem` conserva todas las alternativas; cada problema usa uno o varios `answer_block` según la continuidad visual, nunca un box obligatorio por alternativa.
- Q: Que debe excluir una anotacion precisa? -> A: Encabezados y pies repetitivos, numeros de pagina, publicidad, marcas editoriales no semanticas, artefactos de escaneo y contenido perteneciente a unidades vecinas; cualquier excepcion debe justificarse porque sea necesaria para comprender la unidad.
- Q: Como se valida una solucion multipagina? -> A: Cada continuidad necesita evidencia semantica y geometrica en ambos extremos; un encabezado repetido o una franja de pagina no constituye continuidad.
- Q: Para que se conservan las salidas de Gottfried e Ingrid? -> A: Como anotaciones supervisadas, versionadas y relacionales reutilizables para entrenamiento, validacion y evaluacion de modelos especializados.
- Q: Como se evita fuga de informacion en la evaluacion? -> A: Los conjuntos se separan por documento completo; ninguna pagina de un libro de prueba puede aparecer en entrenamiento o validacion.
- Q: Como se audita un mapa V2 antes de H-PS1? -> A: Gottfried materializa una sesion visual inmutable por `map_id + map_revision`; Problem Detector Lab revalida sus hashes y muestra paginas completas, regiones coarse, unidades P/S y relaciones R lado a lado, sin aprobar H-PS1 ni activar a Ingrid.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Mapear enunciados y soluciones por instancia (Priority: P1)

Como operador de Biblioteca/Fabrica, puedo marcar dentro de una instancia las paginas que contienen enunciados y las que contienen soluciones, incluso cuando una pagina contiene ambos tipos, para que el procesamiento respete la estructura editorial real del libro.

**Why this priority**: Sin un mapa de procedencia confiable no se puede limitar la deteccion ni relacionar ejercicios que reinician su numeracion en distintas practicas.

**Independent Test**: Abrir una instancia de prueba, clasificar rangos separados y paginas intercaladas, guardar la seleccion y volver a abrirla comprobando que todos los roles y estados se conservan.

**Acceptance Scenarios**:

1. **Given** una practica cuyos enunciados y soluciones estan en secciones separadas, **When** el operador asigna ambos rangos, **Then** la instancia conserva las dos selecciones sin mezclarlas.
2. **Given** una pagina con problema y solucion intercalados, **When** el operador le asigna ambos roles, **Then** la pagina queda disponible para ambos procesos.
3. **Given** un libro sin soluciones localizadas, **When** el operador registra el resultado, **Then** el sistema distingue entre ausencia confirmada, busqueda pendiente e incertidumbre.
4. **Given** una pagina con teoria, problemas y soluciones, **When** Gottfried la analiza, **Then** conserva los roles detallados y los tres roles normalizados simultaneamente.
5. **Given** una pagina con varias zonas editoriales, **When** Gottfried registra su distribucion, **Then** las zonas se muestran como regiones aproximadas y no como boxes finales.
6. **Given** un problema resuelto, **When** se calculan estadisticas, **Then** cuenta como un problema resuelto y una solucion identificable sin crear dos problemas.
7. **Given** un libro con problemas pero sin evidencia de soluciones, **When** se evalua elegibilidad, **Then** no genera un mapa problema-solucion ni activa a Ingrid.

---

### User Story 2 - Revisar enlaces problema-solucion (Priority: P1)

Como operador, puedo revisar propuestas que muestran lado a lado un problema y su solucion candidata, junto con la evidencia utilizada, para confirmar, cambiar o rechazar el enlace antes de almacenar datos oficiales.

**Why this priority**: Un enlace incorrecto es mas perjudicial que una solucion temporalmente huerfana y no debe convertirse automaticamente en verdad canonica.

**Independent Test**: Preparar una practica con problemas y soluciones numerados, generar propuestas, confirmar una, corregir otra y dejar una huerfana; cada decision debe persistir de forma independiente.

**Acceptance Scenarios**:

1. **Given** numeros unicos dentro del mismo conjunto de ejercicios, **When** se generan candidatos, **Then** las coincidencias exactas aparecen como propuestas con evidencia verificable.
2. **Given** contenido problema-solucion intercalado sin numero de solucion, **When** existe un unico problema precedente compatible, **Then** el sistema puede proponerlo por proximidad y orden de lectura, pero requiere confirmacion.
3. **Given** numeros duplicados, conteos incompatibles o continuaciones incompletas, **When** se intenta enlazar, **Then** solo las unidades afectadas quedan en conflicto y las demas pueden continuar.
4. **Given** una solucion dividida entre paginas, **When** sus fragmentos forman una continuidad aprobada, **Then** se revisa y enlaza como una sola unidad de solucion.
5. **Given** un mapa `mapping_requires_human`, **When** Gottfried materializa su sesion pre-H-PS1, **Then** el operador puede inspeccionar cada relacion P-S lado a lado con paginas, regiones provisionales, evidencia, incertidumbres, hashes y revision.
6. **Given** una sesion cuyo mapa, scope, revision, huella o lista P/S/R no coincide con el artefacto vivo, **When** se abre en Problem Detector Lab, **Then** queda `visual_audit_blocked` y no se presenta como elegible para H-PS1.
7. **Given** una sesion visual valida, **When** el operador marca una observacion en la interfaz, **Then** la marca permanece solo en memoria y no aprueba H-PS1, no activa a Ingrid y no escribe datos canonicos.

---

### User Story 3 - Promover paquetes completos a la base oficial (Priority: P1)

Como operador, puedo promover un paquete confirmado que contiene un problema y una o varias soluciones, para que la base oficial reciba la relacion completa sin una segunda carga manual ni estados parciales.

**Why this priority**: El objetivo operativo es que el problema llegue listo para su uso con toda solucion confirmada y con procedencia auditable.

**Independent Test**: Confirmar un paquete con dos fragmentos de solucion, promoverlo y comprobar que el problema, las soluciones y sus fuentes quedaron relacionados; simular un fallo y comprobar que ese paquete no produjo escritura parcial.

**Acceptance Scenarios**:

1. **Given** un paquete confirmado, **When** se promueve, **Then** el problema y todas sus soluciones se almacenan como una unidad y quedan vinculados.
2. **Given** un paquete pendiente, conflictivo o huerfano, **When** se solicita la promocion, **Then** el sistema lo bloquea con una razon accionable.
3. **Given** un fallo al almacenar cualquier parte del paquete, **When** finaliza el intento, **Then** ninguna parte incompleta queda oficial y los demas paquetes independientes pueden continuar.
4. **Given** una repeticion de la misma promocion, **When** se procesa otra vez, **Then** no se duplican ni el problema ni sus soluciones.

---

### User Story 4 - Conservar procedencia y decisiones (Priority: P2)

Como auditor, puedo reconstruir de que libro, instancia, conjunto, paginas y regiones visuales surgieron el problema, la solucion y el enlace, para corregir errores sin perder el material fuente.

**Why this priority**: La procedencia permite auditar, volver a segmentar y aprender de correcciones sin contaminar datos canonicos.

**Independent Test**: Abrir un paquete promovido y comprobar que conserva sus identificadores de fuente, fragmentos, metodo de enlace, estado humano y versiones de los procesos que lo generaron.

**Acceptance Scenarios**:

1. **Given** un enlace confirmado, **When** se consulta su auditoria, **Then** se muestran fuentes, paginas, regiones, evidencia y decision humana.
2. **Given** una correccion posterior del enlace, **When** se guarda, **Then** se conserva el historial anterior y la nueva decision sin alterar los boxes originales.

---

### User Story 5 - Construir independencia operativa con modelos especializados (Priority: P2)

Como responsable de la Fabrica, puedo reutilizar las anotaciones revisadas de Gottfried e Ingrid como ground truth versionado para entrenar y evaluar capacidades especializadas, de modo que los documentos nuevos no dependan permanentemente de agentes de razonamiento general.

**Why this priority**: El flujo supervisado es necesario para construir calidad, pero no puede convertirse en el motor permanente ni escalar libro por libro mediante intervención manual completa.

**Independent Test**: Formar un dataset con documentos completos separados entre entrenamiento, validacion, prueba y fuera de distribucion; evaluar una version candidata y comprobar metricas, abstenciones, errores criticos, procedencia y capacidad de rollback antes de autorizarla.

**Acceptance Scenarios**:

1. **Given** una anotacion revisada de problema o solucion, **When** se exporta al dataset, **Then** conserva documento, pagina, clase, geometria, unidad logica, relaciones, contrato, esquema, revisor y estado humano.
2. **Given** alternativas dispuestas en bloques separados, **When** se anotan, **Then** todos los bloques quedan relacionados con el mismo problema y la cobertura de alternativas se puede verificar.
3. **Given** una solucion con encabezado, pie o contenido vecino, **When** Ingrid delimita la solucion, **Then** el box excluye ese contenido ajeno y conserva solamente la envolvente semantica necesaria.
4. **Given** un libro reservado para prueba, **When** se construyen los splits, **Then** ninguna pagina de ese documento aparece en entrenamiento o validacion.
5. **Given** un modelo que no supera los umbrales o presenta un error critico sistematico, **When** se evalua su promocion, **Then** permanece bloqueado y el flujo supervisado sigue disponible.

### Edge Cases

- Una practica reinicia la numeracion que ya aparecio en otra parte del libro.
- Una solucion no muestra numero y existen varios problemas cercanos compatibles.
- Una pagina contiene el final de una solucion y el inicio de otro problema.
- Un problema posee varias soluciones o metodos alternativos.
- Una solucion resuelve varios subapartados o parece compartida por varios problemas.
- El solucionario se encuentra en otro PDF cuya relacion con el libro aun no fue confirmada.
- El numero detectado en la solucion contradice el numero del problema mas cercano.
- Cambian las paginas o boxes despues de haber generado enlaces candidatos.
- Un archivo de recorte falta o deja de coincidir con su huella registrada.
- Dos pestañas intentan revisar o promover el mismo paquete simultaneamente.

- Una region aproximada de Gottfried se superpone con otra porque la pagina mezcla teoria y solucion.
- Una solucion multipagina aparece en estadisticas de varias paginas y no debe duplicarse al agregar el libro.
- Ingrid divide una unidad provisional en varias unidades precisas.
- Ingrid fusiona varias unidades provisionales que en realidad forman una sola solucion.
- Ingrid descubre una unidad omitida dentro de una pagina autorizada.
- Una nueva revision cambia solo un conjunto y las relaciones compatibles de otros conjuntos deben poder reutilizarse.
- Las alternativas aparecen en dos columnas, alrededor de una figura o en regiones visualmente separadas.
- Una pregunta abierta no tiene alternativas y no debe generar un `answer_block` falso.
- El box de alternativas omite una opcion, corta una formula o incluye opciones del problema vecino.
- Un encabezado repetitivo es confundido con el final de una solucion multipagina.
- El numero de pagina o la clave editorial queda dentro del box aunque no pertenezca a la unidad.
- Un mismo libro aporta paginas a mas de un split y produce fuga de informacion.
- Un modelo alcanza la media requerida pero falla sistematicamente en una familia documental.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El sistema MUST permitir seleccionar paginas de enunciados y paginas de soluciones por instancia.
- **FR-002**: El sistema MUST permitir que una pagina tenga simultaneamente los roles de enunciado y solucion.
- **FR-003**: El sistema MUST registrar el patron editorial como secciones separadas, intercalado, hibrido, sin soluciones o desconocido.
- **FR-004**: El sistema MUST distinguir solucion identificada, ausencia confirmada, fuente externa, incertidumbre y revision pendiente.
- **FR-005**: El sistema MUST limitar todo candidato al mismo libro, instancia y conjunto de ejercicios.
- **FR-006**: El sistema MUST usar numeros normalizados cuando existan y MUST detectar duplicados o contradicciones antes de proponer enlaces.
- **FR-007**: El sistema MUST poder proponer enlaces intercalados usando proximidad y orden de lectura cuando no haya numero explicito.
- **FR-008**: Un enlace basado solamente en orden MUST requerir revision humana.
- **FR-009**: El sistema MUST agrupar fragmentos continuos antes de enlazar una solucion multipagina.
- **FR-010**: El sistema MUST admitir varias soluciones para un problema y MUST enviar las soluciones compartidas a revision humana.
- **FR-011**: Cada candidato MUST conservar señales, evidencia, nivel de confianza, alternativa competidora y razones de ambiguedad.
- **FR-012**: Ningun enlace MUST ser canonico sin confirmacion humana durante la primera version.
- **FR-013**: El sistema MUST aislar conflictos por paquete para que unidades independientes puedan continuar.
- **FR-014**: La promocion MUST tratar el problema, sus soluciones y su procedencia como una unica unidad de escritura.
- **FR-015**: La promocion MUST revertir completamente un paquete si falla cualquier parte de su escritura.
- **FR-016**: La promocion MUST ser idempotente y evitar duplicados en repeticiones o reintentos.
- **FR-017**: El sistema MUST bloquear la promocion de paquetes pendientes, conflictivos, huerfanos o con activos faltantes.
- **FR-018**: El sistema MUST conservar procedencia de libro, instancia, conjunto, documento, pagina, region visual y recorte.
- **FR-019**: El sistema MUST conservar por separado las correcciones de boxes y las correcciones de enlaces.
- **FR-020**: Los cambios posteriores en paginas o boxes MUST invalidar los enlaces y paquetes derivados afectados.
- **FR-021**: El sistema MUST permitir revisar problema y solucion juntos antes de promoverlos.
- **FR-022**: Un solucionario externo MUST requerir confirmacion humana de su relacion documental antes de generar enlaces.
- **FR-023**: La relacion de un solucionario externo MUST conservar una referencia estable al PDF o documento confirmado.
- **FR-024**: Una solucion multipagina incompleta MUST quedar bloqueada hasta que su continuidad y orden sean revisados.
- **FR-025**: Un problema sin paquete de solucion en un flujo configurado MUST quedar bloqueado salvo que exista una decision humana terminal de ausencia.
- **FR-026**: Guardar la decision, el evento y la reconciliacion del paquete MUST ser una operacion atomica de staging.
- **FR-027**: Gottfried MUST producir un registro para cada pagina con roles editoriales detallados y roles normalizados multietiqueta `theory`, `problem` y `solution`.
- **FR-028**: La conversion entre roles detallados y normalizados MUST estar definida por una regla contractual versionada y compartida por Gottfried, Ingrid y la interfaz.
- **FR-029**: Gottfried MUST poder registrar regiones editoriales aproximadas mediante rectangulos normalizados y MUST marcarlas como no aptas para segmentacion final.
- **FR-030**: Cada region aproximada MUST conservar rol, orden de lectura, confianza, evidencia e incertidumbres.
- **FR-031**: Las estadisticas de pagina MUST distinguir problemas totales, propuestos, resueltos, soluciones y ejemplos desarrollados como estimaciones no canonicas.
- **FR-032**: Las estadisticas MUST cumplir `problem_units = proposed_problems + solved_problems`; los ejemplos desarrollados MUST permanecer fuera de `problem_units`.
- **FR-033**: Cada estadistica MUST conservar confianza, evidencia e intervalo estimado cuando exista incertidumbre.
- **FR-034**: Gottfried MUST emitir los controles `problem_partition_ok`, `solution_count_valid` y `statistics_consistent` sin presentarlos como aprobacion humana.
- **FR-035**: Despues del analisis completo, el flujo MUST registrar la elegibilidad `eligible_full`, `eligible_partial`, `pending_review` o `not_eligible` con confianza, motivo, evidencia y prioridad.
- **FR-036**: El flujo MUST distinguir entre poder generar un mapa, recomendar generarlo ahora y contar con autorizacion efectiva para generarlo.
- **FR-037**: `pending_review` y `not_eligible` MUST bloquear la activacion de Ingrid.
- **FR-038**: Cada unidad estructural de un mapa MUST recibir un identificador provisional estable dentro de la revision y conjunto aprobados, sin convertirse automaticamente en identidad canonica.
- **FR-039**: Ingrid MUST conservar las referencias provisionales y declarar si su refinamiento es `exact`, `split`, `merge`, `reclassify`, `boundary_adjustment`, `rejected` o `newly_discovered`.
- **FR-040**: La trazabilidad MUST admitir relaciones uno a uno, uno a muchos, muchos a uno y descubrimientos sin unidad provisional de origen.
- **FR-041**: Un descubrimiento de Ingrid que cambie paginas, roles, elegibilidad o estructura MUST volver a Gottfried; una omision dentro del alcance ya aprobado puede continuar pendiente de H-PS2.
- **FR-042**: Una nueva revision MUST invalidar solo los scopes afectados y MUST permitir reutilizar resultados no afectados despues de verificar su compatibilidad.
- **FR-043**: H-PS1 MUST congelar la revision del mapa, la conversion de roles, las selecciones y las unidades provisionales revisadas.
- **FR-044**: Ingrid MUST producir boxes precisos de solucion independientes de las regiones aproximadas de Gottfried.
- **FR-045**: Cada box `problem` MUST incluir el numero cuando pertenezca visualmente al problema, el enunciado completo, datos, formulas, figuras necesarias y todas las alternativas asociadas.
- **FR-046**: Un problema con alternativas MUST relacionarse con uno o varios `answer_block`; se usa un bloque por region visual continua y varios bloques solo cuando la disposicion sea genuinamente discontinua.
- **FR-047**: Cada `answer_block` MUST incluir marcadores, texto, formulas y figuras de todas las alternativas que cubre, MUST registrar su pertenencia al problema y MUST excluir enunciado, solucion, clave y alternativas vecinas.
- **FR-048**: Una pregunta abierta o sin alternativas visibles MUST registrar `answer_block_status: not_applicable` y MUST NOT producir un box de alternativas artificial.
- **FR-049**: Los perfiles de exclusion MUST impedir que `problem`, `problem_number`, `answer_block` y `solution` absorban encabezados o pies repetitivos, numeros de pagina, publicidad, marcas editoriales no semanticas, artefactos de escaneo, espacios excesivos o contenido de unidades vecinas.
- **FR-050**: Una region normalmente excluida MAY incluirse solo cuando sea semanticamente necesaria para la unidad y la anotacion registre `inclusion_exception`, evidencia y confianza.
- **FR-051**: Cada fragmento `solution` MUST incluir la cabecera local de resolucion, desarrollo, formulas, figuras y respuesta final que pertenezcan a esa solucion, y MUST excluir el enunciado independiente, otras soluciones y mobiliario repetitivo de pagina.
- **FR-052**: Un identificador visible de problema o solucion MUST capturarse y vincularse a su unidad; solo puede quedar vacio cuando no sea visible o exista abstencion justificada.
- **FR-053**: Una continuidad multipagina MUST conservar evidencia en la salida y entrada de los fragmentos; un encabezado, pie, numero de pagina o franja sin contenido matematico no puede justificar `continues_on` ni `continues_from`.
- **FR-054**: Cada anotacion precisa MUST emitir controles `content_complete`, `foreign_content_excluded`, `unit_boundary_valid`, `alternatives_complete`, `visible_identifier_captured`, `continuation_supported` y `geometry_precise` con evidencia y estado `pass|fail|uncertain|not_applicable`.
- **FR-055**: H-PS2 MUST bloquear cualquier unidad con un control obligatorio `fail`, con `uncertain` no revisado, con alternativas omitidas o con continuidad no sustentada.
- **FR-056**: Toda salida supervisada de Gottfried o Ingrid destinada a aprendizaje MUST conservar documento, pagina fisica, clase, coordenadas normalizadas, unidad logica, relaciones, confianza, incertidumbre, contrato, esquema, revisor y estado humano.
- **FR-057**: El esquema relacional MUST admitir `contains`, `belongs_to`, `continues_on`, `continues_from`, `solves`, `has_answer_block`, `precedes` y `same_entity`.
- **FR-058**: Los datasets MUST dividirse por documento completo en entrenamiento, validacion, prueba y un conjunto dificil o fuera de distribucion; una pagina no puede cruzar splits mediante su documento fuente.
- **FR-059**: El sistema MUST contemplar como minimo una capacidad especializada de analisis estructural documental y otra de segmentacion y vinculacion matematica, sin exigir que sean un unico modelo fisico cada una.
- **FR-060**: Ningun modelo MUST sustituir el flujo supervisado hasta superar los umbrales aprobados en documentos no vistos, auditar errores criticos, ofrecer abstencion y contar con aprobacion humana y rollback de version.
- **FR-061**: Gottfried e Ingrid MUST quedar como generadores de ground truth, revisores de baja confianza, auditores por muestreo y curadores de casos dificiles, no como dependencia obligatoria de cada documento nuevo.
- **FR-062**: Antes de solicitar H-PS1, cada revision de mapa MUST materializarse como una sesion visual independiente y trazable en Problem Detector Lab.
- **FR-063**: La sesion pre-H-PS1 MUST mostrar paginas completas, roles, regiones `coarse`, unidades P/S, relaciones R lado a lado, numeros editoriales, confianza, evidencia, incertidumbres, hashes y revisiones.
- **FR-064**: El adaptador MUST revalidar el hash vivo del mapa y la coincidencia literal de scope, revision, huellas, paginas y referencias P/S/R antes de declarar una sesion lista para auditoria.
- **FR-065**: Una discrepancia o activo obligatorio no representable MUST producir `visual_audit_blocked`; una vista parcial MUST NOT habilitar la solicitud de H-PS1.
- **FR-066**: Los endpoints pre-H-PS1 MUST ser de solo lectura, usar referencias de medios opacas y MUST NOT exponer rutas privadas del sistema de archivos.
- **FR-067**: El adaptador pre-H-PS1 MUST NOT aprobar H-PS1, activar a Ingrid, crear boxes/crops, modificar mapas/PDFs ni escribir en app, staging canonico o base de datos.
- **FR-068**: Una revision nueva MUST producir una sesion nueva con referencia a su predecesora y MUST conservar las sesiones historicas sin reescritura.
- **FR-069**: H-PS1 MUST requerir una orden humana posterior y explicita que cite la revision y huellas visualmente auditadas; ninguna marca local de interfaz equivale a esa orden.

### Key Entities

- **Registro estructural de pagina V2**: Salida completa de Gottfried para una pagina, con roles editoriales, roles normalizados, regiones aproximadas, estadisticas y evidencia.
- **Evaluacion de elegibilidad**: Decision estructural versionada que distingue si un scope puede mapearse, si conviene hacerlo ahora y si existe autorizacion efectiva.
- **Unidad provisional de Gottfried**: Referencia estructural temporal y estable dentro de `map_id + map_revision + exercise_set_id`; nunca es una identidad canonica.
- **Relacion de refinamiento de Ingrid**: Trazabilidad entre una o varias unidades provisionales y las unidades visuales precisas resultantes.
- **Mapa estructural de instancia**: Patron editorial, conjuntos de ejercicios y roles asignados a cada pagina.
- **Unidad de problema**: Problema revisado con numero, paginas, regiones visuales, recortes y orden de lectura.
- **Unidad de solucion**: Una solucion identificable compuesta por uno o varios fragmentos ordenados.
- **Enlace candidato**: Propuesta auditable entre una unidad de problema y una unidad de solucion, con señales, confianza y decision humana.
- **Paquete problema-solucion**: Unidad confirmada y lista para promocion que contiene un problema, sus soluciones y toda su procedencia.
- **Activo de solucion**: Fragmento visual de una solucion vinculado con su pagina, region, recorte y huella de fuente.
- **Evento de revision**: Decision humana que confirma, cambia, rechaza o deja huerfano un enlace.
- **Bloque de alternativas**: Region rectangular continua que agrupa una o mas alternativas de un problema y conserva la relacion `has_answer_block`; un problema puede tener varios bloques.
- **Perfil de exclusion**: Reglas versionadas por clase que determinan contenido obligatorio, contenido prohibido y excepciones justificadas.
- **Anotacion supervisada relacional**: Ground truth versionado con regiones, unidades y relaciones entre paginas, apto para entrenamiento, validacion o evaluacion despues de aprobacion humana.
- **Version candidata de modelo**: Artefacto evaluable con dataset, splits por documento, metricas, errores, abstenciones, version y mecanismo de rollback.
- **Sesion visual pre-H-PS1**: Manifiesto no canonico e inmutable que fija una revision de mapa y permite inspeccionarla en Problem Detector Lab sin transferir autoridad de gate.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El operador puede clasificar y recuperar paginas de enunciados y soluciones de una instancia sin perder selecciones en el 100 % de las pruebas de guardado y reapertura.
- **SC-002**: El sistema representa correctamente tanto secciones separadas como contenido intercalado en todos los casos piloto aprobados.
- **SC-003**: Ningun enlace pendiente, conflictivo o huerfano llega a la base oficial durante las pruebas de aceptacion.
- **SC-004**: El 100 % de los paquetes confirmados almacenan el problema, todas sus soluciones y su procedencia sin estados parciales.
- **SC-005**: Los reintentos de promocion producen cero problemas o soluciones duplicados.
- **SC-006**: Un fallo simulado en cualquier parte de un paquete revierte solamente ese paquete y permite continuar con los demas.
- **SC-007**: El 100 % de los enlaces promovidos puede reconstruirse hasta sus paginas y regiones fuente.
- **SC-008**: El operador puede confirmar, corregir o rechazar una propuesta individual en menos de 30 segundos cuando las imagenes estan disponibles.
- **SC-009**: Las pruebas piloto registran cero enlaces canonicos falsos porque toda promocion requiere confirmacion humana.
- **SC-010**: El 100 % de las paginas analizadas por Gottfried incluye `content_roles`, `audit_roles`, `page_sections`, `page_statistics`, evidencia y version de conversion.
- **SC-011**: El 100 % de las regiones de Gottfried usa coordenadas `0..1`, `precision: coarse` y `usable_as_final_box: false`.
- **SC-012**: El 100 % de las paginas con estadisticas cumple la particion de problemas o queda marcado `fail`/`uncertain` con evidencia para revision.
- **SC-013**: Ningun scope `pending_review` o `not_eligible` activa a Ingrid, y ningun mapa se genera sin autorizacion efectiva de Euler.
- **SC-014**: El 100 % de las unidades precisas de Ingrid conserva una relacion con unidades provisionales o una justificacion `newly_discovered`.
- **SC-015**: Una revision nueva invalida solamente los scopes afectados y reutiliza derivados no afectados solo despues de verificar compatibilidad.
- **SC-016**: La cobertura de alternativas en documentos de prueba no vistos alcanza al menos `98 %`, contabilizando una alternativa como cubierta solo si no esta cortada y pertenece al problema correcto.
- **SC-017**: La deteccion de problemas alcanza precision y exhaustividad minimas de `95 %` en documentos de prueba separados por libro.
- **SC-018**: La deteccion de soluciones alcanza precision minima de `95 %` y ningun error critico sistematico queda oculto por la media global.
- **SC-019**: La exactitud de continuidad entre paginas alcanza al menos `95 %` y los encabezados repetitivos aceptados como continuidad son `0` en el golden critico.
- **SC-020**: El contenido ajeno critico dentro de boxes no supera `1 %` de las unidades evaluadas y las alternativas de otro problema dentro de un box son `0`.
- **SC-021**: La clasificacion estructural de paginas alcanza al menos `95 %` en libros no vistos.
- **SC-022**: La vinculacion problema-solucion alcanza al menos `95 %` sobre unidades completas y conserva abstencion para evidencia insuficiente.
- **SC-023**: El indice de independencia operativa alcanza al menos `90 %` y la intervencion manual regular no supera `10 %` de las unidades evaluadas.
- **SC-024**: El `100 %` de los splits de evaluacion pasa una auditoria de separacion por documento completo sin fuga de paginas.
- **SC-025**: El `100 %` de las anotaciones exportadas para aprendizaje conserva version contractual, procedencia, relaciones y estado de revision humana.
- **SC-026**: El `100 %` de las sesiones pre-H-PS1 declaradas listas coincide exactamente con el hash, revision, scope, huellas, paginas y referencias P/S/R de su mapa vivo.
- **SC-027**: El `100 %` de las relaciones de una sesion valida puede revisarse lado a lado con ambas paginas y regiones provisionales sin exponer rutas privadas.
- **SC-028**: La validacion del adaptador produce cero aprobaciones H-PS1, activaciones de Ingrid, boxes/crops, mutaciones de mapa/PDF o escrituras canonicas.

## Assumptions

- El libro y la instancia ya existen antes de clasificar paginas o enlazar soluciones.
- Gottfried propone la estructura editorial y los conjuntos de ejercicios; el humano confirma las relaciones documentales dudosas.
- Ingrid entrega boxes revisados de problemas y soluciones, pero no decide por si sola la relacion canonica.
- `worked_example` se normaliza como `theory`, conserva su rol detallado y no cuenta como problema.
- `solved_problem` se normaliza simultaneamente como `problem` y `solution`; `answer_key` se normaliza como `solution`.
- Gottfried evalua `can_generate_map` y recomienda `should_generate_now`; Euler autoriza `generate_map`, y la activacion efectiva de Ingrid requiere ademas H-PS1 y una asignacion exacta.
- Las soluciones visuales pueden almacenarse antes de contar con OCR o una version matematica normalizada.
- Los problemas sin solucion siguen teniendo un flujo separado y no bloquean los paquetes confirmados.
- La primera version trabaja en modo supervisado; la aprobacion automatica de enlaces queda fuera de alcance.
- El entrenamiento y la promocion de nuevos detectores de soluciones quedan fuera de este incremento.
- IND-MA-01 define contratos, datasets, evaluacion y gates; la seleccion de arquitectura y el entrenamiento efectivo de modelos especializados pertenecen a incrementos posteriores controlados.
- `answer_block` representa bloques visuales continuos, no obliga a crear un box por alternativa; la completitud se verifica mediante miembros/etiquetas observados y pertenencia al problema.
