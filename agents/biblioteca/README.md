# Agentes de Biblioteca

Este directorio contiene los perfiles reutilizables de los chats dedicados a la Biblioteca de Auditor-IA. No contiene agentes autonomos ni concede permisos sobre los PDF o la base de datos.

## Agentes activos

| Agente | Identificador | Funcion | Perfil del chat |
|---|---|---|---|
| Euler | `euler_library_factory_coordinator_v1` | Coordinar, priorizar, asignar, auditar y cerrar el lote | [euler/CHAT_PROMPT.md](euler/CHAT_PROMPT.md) |
| Gottfried Leibniz | `gottfried_leibniz_v1` | Organizar unidades, analizar libros y construir mapas estructurales problema-solucion | [gottfried/CHAT_PROMPT.md](gottfried/CHAT_PROMPT.md) |
| Ingrid Daubechies | `ingrid_daubechies_v1` | Revisar el dataset o segmentar problemas/soluciones de una instancia en staging, segun la capacidad asignada | [ingrid/CHAT_PROMPT.md](ingrid/CHAT_PROMPT.md) |

Ingrid conserva dos modos estrictamente separados. La revision del dataset no modifica su fuente; la segmentacion de instancias solo se habilita mediante asignacion explicita, mapa confirmado y gate humano, y nunca autoriza entrenamiento ni escritura directa en BD.

Perfil y piloto: [ingrid/README.md](ingrid/README.md).

## Chats Codex creados

| Titulo del chat | Perfil cargado | Estado inicial |
|---|---|---|
| `Agente Biblioteca - Euler` | `euler/CHAT_PROMPT.md` | Espera una orden humana |
| `Agente Biblioteca - Gottfried Leibniz` | `gottfried/CHAT_PROMPT.md` | Espera una asignacion de Euler o del humano |
| `Agente Segmentacion - Ingrid Daubechies` | `ingrid/CHAT_PROMPT.md` | Espera una asignacion inequivoca de modo dataset o instancia |

Los identificadores internos de los chats son locales a Codex y no se guardan como configuracion portable del repositorio. Para comunicarlos, debe localizarse el titulo exacto y confirmarse el envio mediante la herramienta de Codex.

## Fuentes de verdad

Los perfiles no reemplazan los contratos de Obsidian. Cada chat debe leer, al comenzar y cuando cambie su contrato, las fuentes indicadas en su propio `CHAT_PROMPT.md`.

Orden de autoridad:

1. instruccion humana explicita mas reciente;
2. contratos y decisiones confirmadas en Obsidian;
3. PDF, hashes, inventarios y registros aprobados;
4. resultados del otro agente;
5. inferencias de Codex.

Las contradicciones se muestran al humano; no se resuelven silenciosamente.

Para el flujo problema-solucion tambien es obligatorio [CONTRATO_PROBLEMA_SOLUCION.md](CONTRATO_PROBLEMA_SOLUCION.md), que prevalece sobre declaraciones antiguas de diferimiento solo dentro de ese flujo.

## Alcance activo

```text
Humano
-> Euler prepara lote y asignaciones
-> Gottfried organiza y analiza
-> H-PS1 confirma estructura, paginas y relacion documental
-> Ingrid propone boxes y unidades visuales dentro de la instancia asignada
-> H-PS2 aprueba o corrige la segmentacion
-> aplicador humano de boxes actualiza y regenera problemas staging
-> escritor controlado registra unidades de solucion con la nueva revision
-> Enlazador propone correspondencias
-> H-PS3 confirma, reasigna, rechaza o marca huerfanos
-> Euler verifica gates, metricas y vista previa
-> H-PS4 autoriza o rechaza la promocion controlada
```

El piloto comienza con hasta 10 unidades documentales en modo `dry_run`. No se mueve, renombra, fusiona ni sobrescribe ningun PDF durante la simulacion.

## Uso de los chats

- El chat de Euler recibe objetivos, prioridades y rutas de origen.
- Euler entrega una asignacion estructurada para Gottfried.
- El chat de Gottfried solo trabaja sobre asignaciones identificables y conserva evidencia.
- Euler solo asigna a Ingrid el modo instancia despues de aprobarse el mapa de Gottfried.
- Ingrid exige `capability_id`; nunca mezcla revision de dataset y segmentacion de instancia.
- Los chats no deben asumir que estan conectados. Una entrega cuenta como enviada unicamente cuando la herramienta de Codex lo confirma o el humano la traslada.
- Toda operacion fisica queda fuera del rol razonador y requiere aprobacion humana y un Ejecutor controlado.

Contexto comun: [CONTEXTO_COMPARTIDO.md](CONTEXTO_COMPARTIDO.md).
