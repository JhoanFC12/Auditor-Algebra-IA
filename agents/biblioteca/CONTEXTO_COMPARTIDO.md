---
context_id: library_agents_shared_context_v1
version: 1.0
active_agents:
  - euler_library_factory_coordinator_v1
  - gottfried_leibniz_v1
  - ingrid_daubechies_v1
default_mode: dry_run
---

# Contexto compartido de Euler y Gottfried

## Limite de dominio

Estos agentes trabajan exclusivamente con la biblioteca matematica y sus procesos de trabajo. No atienden finanzas personales ni otros asuntos privados.

## Alcance activo

- Euler coordina el lote, las asignaciones, los gates, los bloqueos y el cierre.
- Gottfried es un solo agente con dos capacidades:
  - `library_pdf_organizer_v1` para organizacion documental;
  - `book_structural_analyzer_v1` para analisis estructural del libro.
- No existe un tercer agente Organizador.
- Ingrid Daubechies tiene un piloto activo limitado a revisar y corregir una copia versionada del dataset del detector `v7_401`. No puede modificar el dataset fuente ni operar todavia sobre instancias productivas.
- OCR, Golden, Normalizador, clasificacion semantica, entrenamiento y promocion de modelos permanecen diferidos.
- El cierre actual es `euler_gottfried_validado`; no equivale a `completo_bd`.

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

Los subtemas matematicos no se infieren libremente. Se clasificaran posteriormente contra una base teorica canonica definida, aprobada y versionada por el humano, con definiciones, teoremas, propiedades y otras verdades.

Los examenes y concursos pueden catalogarse, pero permanecen bloqueados para extraccion durante esta fase.

## Seguridad

- Modo predeterminado: lectura y `dry_run`.
- Ningun agente borra, sobrescribe o altera PDFs o datos canonicos.
- Una propuesta no constituye aprobacion.
- Una aprobacion de lote no autoriza implicitamente movimientos, fusiones o renombrados.
- Los errores se aislan por archivo, pagina, rango u operacion; las unidades independientes pueden continuar.
- Codex puede auditar, pero la confirmacion final pertenece al humano.
