# Contract: Precision Annotation V1

Este contrato define la geometria precisa que Ingrid debe producir para
problemas, numeros, alternativas y soluciones. Complementa
`ingrid-provisional-traceability-v1.md`; no convierte las regiones `coarse` de
Gottfried en boxes finales ni autoriza escrituras canonicas.

## Principio de envolvente semantica

Un box preciso cumple simultaneamente:

1. **cobertura completa**: no corta glifos, formulas, figuras ni alternativas
   necesarias;
2. **pureza de unidad**: no contiene contenido perteneciente a otra unidad;
3. **margen controlado**: conserva un margen visual pequeno y consistente sin
   absorber mobiliario editorial;
4. **procedencia**: conserva pagina, dimensiones, coordenadas, evidencia,
   contrato, esquema y revision;
5. **abstencion segura**: si no puede satisfacerse lo anterior, queda
   `uncertain` o `abstained`, nunca aprobado por inferencia.

No se exige ajustar al ultimo pixel. La prioridad es cero contenido matematico
perdido con el minimo contenido ajeno razonable.

## Perfil `problem`

### Debe incluir

- identificador visible cuando esta unido editorialmente al problema;
- enunciado completo e instrucciones locales;
- datos, condiciones, formulas y expresiones;
- tablas, graficos y figuras necesarios;
- todas las alternativas y sus marcadores;
- notas locales imprescindibles para interpretar la pregunta;
- fragmentos de continuacion que pertenezcan a la misma unidad.

### Debe excluir

- teoria, ejemplos o explicaciones no referenciados por el problema;
- soluciones, desarrollos, pistas o claves de respuesta;
- problemas y alternativas vecinos;
- titulos de capitulo o instrucciones globales aplicables a varias unidades,
  salvo que se registren por separado como contexto compartido;
- encabezados y pies repetitivos, autor, editorial y nombre del curso;
- numero fisico de pagina;
- publicidad, marcas de agua decorativas y artefactos de escaneo;
- margen en blanco excesivo.

Una instruccion global nunca se duplica dentro de todos los boxes. Si es
indispensable, se conserva como region de contexto relacionada y el problema
la referencia.

## Perfil `problem_number`

Debe incluir solamente el identificador visible y su puntuacion inseparable,
por ejemplo `1.`, `07)`, `Problema 12` o `P-3`.

Debe excluir:

- palabras del enunciado que siguen al identificador;
- numeros de pagina, capitulo, formula o figura;
- numeros de solucion y claves;
- identificadores de un problema vecino.

Si el identificador es visible, `visible_identifier_captured` no puede ser
`not_applicable`. Si no existe, se registra `not_applicable`; si no se puede
leer con seguridad, se registra `uncertain` y no se inventa.

## Perfil `answer_block`

### Relacion con `problem`

- el box padre `problem` siempre contiene todas las alternativas;
- `answer_block` es un subbox relacionado mediante `has_answer_block`;
- cada `answer_block` pertenece exactamente a un problema;
- un problema puede tener cero, uno o varios bloques;
- una pregunta abierta usa `answer_block_status: not_applicable`.

### Uno o varios bloques

- usa un bloque cuando todas las alternativas forman una region visual
  continua, incluso si se distribuyen en filas o columnas dentro de ella;
- usa varios bloques cuando las alternativas estan separadas por una figura,
  por columnas no contiguas o por contenido que no pertenece a las opciones;
- no crea obligatoriamente un box por alternativa;
- cada alternativa visible queda cubierta por exactamente un bloque del mismo
  problema;
- todos los bloques conservan `parent_problem_unit_id` y `block_index`.

### Debe incluir

- marcadores `A`, `B`, `C`, `D`, `E` o equivalentes;
- texto, formulas, diagramas o imagenes de cada alternativa cubierta;
- puntuacion y signos inseparables del marcador;
- espacio minimo necesario para no cortar trazos.

### Debe excluir

- enunciado e instrucciones previas;
- alternativas de otro problema;
- solucion, clave o respuesta correcta destacada en otra seccion;
- numero del problema salvo superposicion visual inevitable documentada;
- encabezados, pies, numeros de pagina, publicidad y decoracion;
- espacios o columnas vacias que permitan usar un bloque mas preciso.

### Completitud

```yaml
answer_block_status: complete|incomplete|not_applicable|uncertain
alternative_labels_observed: []
alternative_count_observed: 0
expected_alternative_count: null
```

`complete` requiere que todos los marcadores y contenidos visibles esten
enteros y pertenezcan al problema correcto. Una opcion cortada, omitida o
duplicada produce `incomplete`. Cuando el numero esperado no pueda inferirse
visualmente, queda `null`; no se inventa.

## Perfil `solution`

### Debe incluir

- cabecera local como `Solucion`, `Resolucion` y su identificador cuando
  pertenezcan a la unidad;
- desarrollo matematico completo;
- datos retomados dentro del desarrollo;
- formulas, tablas, graficos y figuras usados por la solucion;
- conclusion, resultado final, respuesta o clave local;
- todos los fragmentos que constituyan la misma solucion.

### Debe excluir

- enunciado independiente que tenga su propio box `problem`;
- solucion anterior o siguiente;
- problemas propuestos posteriores;
- encabezado o pie repetitivo de pagina;
- autor, editorial, curso y numero fisico de pagina;
- publicidad, marca de agua decorativa y artefactos de escaneo;
- franjas vacias o contenido vecino usado solo para completar un rectangulo.

Cuando una pagina contiene problema y solucion, ambos se segmentan por
separado siempre que exista una frontera visual verificable. Una repeticion
local de datos dentro del desarrollo pertenece a la solucion; el enunciado
editorial completo no se absorbe por comodidad.

Una solucion continua en la misma pagina usa un solo fragmento. Solo se divide
si una region ajena interrumpe su geometria o si el layout exige regiones
discontinuas; ambas partes conservan la misma unidad y orden.

## Continuidad entre paginas

Una relacion multipagina requiere:

- el primer fragmento termina sin cierre semantico o editorial suficiente;
- el siguiente fragmento comienza continuando la misma expresion, frase,
  figura, enumeracion o desarrollo;
- no aparece antes una cabecera inequívoca de otra unidad;
- la numeracion, tema, estilo y orden son compatibles;
- existe evidencia visual de ambos bordes;
- las relaciones reciprocas `continues_on` y `continues_from` coinciden.

No son evidencia de continuidad:

- encabezado o pie repetitivo;
- numero de pagina;
- nombre del autor, editorial o curso;
- franja en blanco;
- proximidad vertical por si sola;
- que dos paginas consecutivas compartan el rol `solution`;
- una clave final seguida por una nueva `Resolucion N`.

Si la evidencia es incompleta, `continuation_supported: uncertain`,
`continuation_complete: false` y la unidad queda bloqueada.

## Registro de calidad geometrica

```yaml
schema_version: ingrid_geometry_quality_v1
region_id: ""
annotation_unit_id: ""
checks:
  content_complete: uncertain
  foreign_content_excluded: uncertain
  unit_boundary_valid: uncertain
  alternatives_complete: not_applicable
  visible_identifier_captured: uncertain
  continuation_supported: not_applicable
  geometry_precise: uncertain
warnings: []
inclusion_exceptions: []
evidence: []
confidence: 0.0
status: pending
human_review: pending
```

Cada control admite `pass`, `fail`, `uncertain` o `not_applicable`.

`inclusion_exceptions` usa:

```yaml
- excluded_kind: header|footer|page_number|advertising|watermark|neighbor|other
  reason: ""
  evidence: []
  approved: false
```

Una excepcion no aprobada mantiene el control en `uncertain`.

## Advertencias automaticas no decisorias

El validador puede marcar para revision:

- box en el `12 %` superior o inferior de la pagina;
- region horizontal delgada proxima a un encabezado o pie;
- area superior al `70 %` de la pagina;
- superposicion con otra unidad;
- varios fragmentos adyacentes de una misma unidad en una pagina;
- identificador visible sin `problem_number` o `number_bbox_xyxy`;
- alternativa esperada sin cobertura;
- relacion multipagina sustentada solo por geometria.

Estos umbrales generan `warnings`; no convierten automaticamente un box en
error porque una solucion valida puede ocupar una pagina completa.

## Gate H-PS2

H-PS2 solo puede aprobar una unidad cuando:

- todos los controles obligatorios son `pass` o `not_applicable` valido;
- no existe `fail`;
- todo `uncertain` fue resuelto o termino en abstencion;
- `answer_block_status` es `complete` o `not_applicable`;
- identificadores visibles fueron capturados;
- continuidades tienen evidencia positiva y roles validos;
- overlays y crops permiten reconstruir la decision.

La aprobacion sigue siendo humana y se limita al artefacto, revision y huella
registrados. Este contrato no autoriza `/api/pages/boxes`, staging canonico,
entrenamiento ni promocion.
