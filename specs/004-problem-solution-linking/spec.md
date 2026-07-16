# Feature Specification: Enlace Problema-Solucion

**Feature Branch**: `[004-problem-solution-linking]`

**Created**: 2026-07-15

**Status**: Implemented (local, pending controlled pilot)

**Input**: User description: "Clasificar paginas de enunciados y soluciones, enlazar cada problema con su solucion correspondiente y almacenar ambos juntos en la base oficial despues de revision."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Mapear enunciados y soluciones por instancia (Priority: P1)

Como operador de Biblioteca/Fabrica, puedo marcar dentro de una instancia las paginas que contienen enunciados y las que contienen soluciones, incluso cuando una pagina contiene ambos tipos, para que el procesamiento respete la estructura editorial real del libro.

**Why this priority**: Sin un mapa de procedencia confiable no se puede limitar la deteccion ni relacionar ejercicios que reinician su numeracion en distintas practicas.

**Independent Test**: Abrir una instancia de prueba, clasificar rangos separados y paginas intercaladas, guardar la seleccion y volver a abrirla comprobando que todos los roles y estados se conservan.

**Acceptance Scenarios**:

1. **Given** una practica cuyos enunciados y soluciones estan en secciones separadas, **When** el operador asigna ambos rangos, **Then** la instancia conserva las dos selecciones sin mezclarlas.
2. **Given** una pagina con problema y solucion intercalados, **When** el operador le asigna ambos roles, **Then** la pagina queda disponible para ambos procesos.
3. **Given** un libro sin soluciones localizadas, **When** el operador registra el resultado, **Then** el sistema distingue entre ausencia confirmada, busqueda pendiente e incertidumbre.

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

### Key Entities

- **Mapa estructural de instancia**: Patron editorial, conjuntos de ejercicios y roles asignados a cada pagina.
- **Unidad de problema**: Problema revisado con numero, paginas, regiones visuales, recortes y orden de lectura.
- **Unidad de solucion**: Una solucion identificable compuesta por uno o varios fragmentos ordenados.
- **Enlace candidato**: Propuesta auditable entre una unidad de problema y una unidad de solucion, con señales, confianza y decision humana.
- **Paquete problema-solucion**: Unidad confirmada y lista para promocion que contiene un problema, sus soluciones y toda su procedencia.
- **Activo de solucion**: Fragmento visual de una solucion vinculado con su pagina, region, recorte y huella de fuente.
- **Evento de revision**: Decision humana que confirma, cambia, rechaza o deja huerfano un enlace.

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

## Assumptions

- El libro y la instancia ya existen antes de clasificar paginas o enlazar soluciones.
- Gottfried propone la estructura editorial y los conjuntos de ejercicios; el humano confirma las relaciones documentales dudosas.
- Ingrid entrega boxes revisados de problemas y soluciones, pero no decide por si sola la relacion canonica.
- Las soluciones visuales pueden almacenarse antes de contar con OCR o una version matematica normalizada.
- Los problemas sin solucion siguen teniendo un flujo separado y no bloquean los paquetes confirmados.
- La primera version trabaja en modo supervisado; la aprobacion automatica de enlaces queda fuera de alcance.
- El entrenamiento y la promocion de nuevos detectores de soluciones quedan fuera de este incremento.
