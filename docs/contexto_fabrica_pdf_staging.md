# Contexto operativo: Fabrica PDF -> Staging

Fecha de captura: 2026-06-08

Hilo madre de Codex: `019ea5ab-6368-7dc0-916a-b849b3d1d9e1`

## Objetivo actual

Convertir el flujo de Biblioteca por libro e instancia en una fabrica unica para procesar PDFs seleccionados:

`Biblioteca -> Libro -> Instancia -> PDF seleccionado -> paginas -> boxes -> crops -> OCR/segmentacion -> normalizacion -> staging revisable`

La meta es que el usuario revise y corrija resultados antes de cualquier promocion futura a la tabla principal.

## Reglas centrales

- Nada automatico entra directo a la tabla `problemas`.
- Todo resultado automatico entra primero a staging.
- Toda correccion humana debe persistirse como dato util para entrenamiento futuro.
- La revision final debe ser con formulario de campos, no JSON crudo como interfaz principal.
- El flujo nuevo `Fabrica` debe convivir con el flujo legacy `PDF IA`.

## Implementacion inicial ya creada

Modulo nuevo:

- `modulos/instance_factory/__init__.py`
- `modulos/instance_factory/models.py`
- `modulos/instance_factory/pipeline.py`
- `modulos/instance_factory/staging.py`
- `modulos/instance_factory/model_inventory.py`
- `modulos/instance_factory/page_selection.py`
- `modulos/instance_factory/gui_instance_factory.py`

Conexion desde Biblioteca:

- `modulos/modulo10_biblioteca_libros/gui_biblioteca_libros.py`
  - Se agrego acceso `Fabrica` por instancia.
  - Se conserva el flujo `PDF IA`.

Tests nuevos:

- `tests/test_instance_factory_staging.py`

## Arquitectura prevista

Contrato interno:

1. Instancia seleccionada desde Biblioteca.
2. PDF asociado o seleccionado.
3. Seleccion de paginas.
4. Deteccion de boxes de problemas.
5. Generacion de crops.
6. OCR y segmentacion.
7. Normalizacion.
8. Guardado en staging.
9. Revision humana.
10. Promocion futura a `problemas`, todavia no activada.

Estados esperados por etapa:

- `pendiente`
- `procesando`
- `listo`
- `requiere_revision`
- `error`

Metadata minima de staging:

- libro
- instancia
- PDF
- pagina
- box
- crop
- modelos usados
- version/provider/fallback del modelo cuando aplique
- confianza
- estado
- salida cruda
- correcciones humanas

## Agentes creados

Hilos iniciales creados antes de registrar el proyecto correcto:

- Agente 1 Arquitectura/Pipeline: `019ea5d0-995c-75d0-9dc7-8e4d818057f3`
- Agente 2 Interfaz de Instancia: `019ea5d0-a1ca-7102-89a5-5c6469dfab70`
- Agente 3 Modelos/Golden Bases: `019ea5d0-a9b3-79f3-90c2-a301b25dd7ec`
- Agente 4 Staging/BD/QA: `019ea5d0-b2b4-76a0-91c1-4949c9b7f835`

Luego se guardo el proyecto `E:\Github\Auditor-IA` en Codex y se recrearon agentes en worktrees del proyecto correcto. Uno visible en la lista:

- Agente 1 Arquitectura/Pipeline nuevo: `019ea5e3-03f2-7502-8694-c75f984c27d7`

Los otros nuevos fueron devueltos inicialmente como worktrees pendientes:

- `local:6ecf583f-e45b-41d6-a132-315fa12b871f`
- `local:fa8facf5-eea6-4a6e-b02f-1536a8f06a27`
- `local:7b871c01-04a0-471d-bb7d-8806238769bd`
- `local:0aa7c538-ecf1-466d-a0dd-25c67bd0c3fc`

## Responsabilidades de agentes

Agente 1: Arquitectura y Pipeline

- Auditar y fortalecer el contrato instancia -> paginas -> boxes -> crops -> OCR/segmentacion -> normalizacion -> staging.
- Reforzar `InstancePdfPipelineService`.
- Mantener logica fragil fuera de GUI.
- Mantener compatibilidad con Modulo 10 Biblioteca, Modulo 13 PDF IA y Modulo 0 Transcriptor.

Agente 2: Interfaz de Instancia

- Mejorar el panel unico por instancia.
- Mostrar pasos, estados, progreso y errores.
- Reemplazar revision basada en JSON por formularios de campos cuando sea razonable.

Agente 3: Modelos y Golden Bases

- Inventariar modelos actuales y definir defaults/fallbacks.
- Registrar version, provider, confianza y fallback.
- Guardar salidas crudas y correcciones con trazabilidad.
- Preparar Golden PDF, Golden OCR, Golden Segmentos y dataset normalizador.

Agente 4: Staging, BD y QA

- Fortalecer staging por instancia.
- Evitar duplicados.
- Preparar frontera futura de promocion a `problemas`, sin activarla.
- Agregar tests focalizados y correr regresion.

## Verificacion registrada

- Las pruebas focalizadas nuevas pasaron en el hilo madre.
- La suite completa fallo en Python global por dependencias faltantes como `psycopg2`.
- Al reintentar con `.venv`, quedaron fallas preexistentes en tests de Transcriptor/OCR no relacionadas con `instance_factory`.

## Proximos pasos recomendados

1. Leer el estado actual de `modulos/instance_factory`.
2. Ver si algun agente dejo cambios en worktrees y consolidarlos con cuidado.
3. Priorizar el contrato de pipeline y staging antes de ampliar UI.
4. Mantener `problemas` fuera del flujo automatico hasta que staging este probado.
5. Crear una instancia pequena de prueba con 2-3 paginas para validar el flujo completo.
