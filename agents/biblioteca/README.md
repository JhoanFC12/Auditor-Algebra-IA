# Agentes de Biblioteca

Este directorio contiene los perfiles reutilizables de los chats dedicados a la Biblioteca de Auditor-IA. No contiene agentes autonomos ni concede permisos sobre los PDF o la base de datos.

## Agentes activos

| Agente | Identificador | Funcion | Perfil del chat |
|---|---|---|---|
| Euler | `euler_library_factory_coordinator_v1` | Coordinar, priorizar, asignar, auditar y cerrar el lote | [euler/CHAT_PROMPT.md](euler/CHAT_PROMPT.md) |
| Gottfried Leibniz | `gottfried_leibniz_v1` | Organizar unidades documentales y analizar estructuralmente los libros | [gottfried/CHAT_PROMPT.md](gottfried/CHAT_PROMPT.md) |

Ingrid Daubechies tiene un piloto autorizado sobre una copia versionada de la base de entrenamiento del detector. La ejecucion sobre instancias productivas sigue bloqueada. Los demas agentes o modelos permanecen diferidos.

Perfil y piloto: [ingrid/README.md](ingrid/README.md).

## Chats Codex creados

| Titulo del chat | Perfil cargado | Estado inicial |
|---|---|---|
| `Agente Biblioteca - Euler` | `euler/CHAT_PROMPT.md` | Espera una orden humana |
| `Agente Biblioteca - Gottfried Leibniz` | `gottfried/CHAT_PROMPT.md` | Espera una asignacion de Euler o del humano |
| `Agente Segmentacion - Ingrid Daubechies` | `ingrid/CHAT_PROMPT.md` | Revisa una copia versionada del dataset `v7_401` |

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

## Alcance inicial

```text
Humano
-> Euler prepara lote y asignaciones
-> Gottfried organiza y analiza
-> Euler verifica gates y metricas
-> Humano aprueba, corrige o rechaza
```

El piloto comienza con hasta 10 unidades documentales en modo `dry_run`. No se mueve, renombra, fusiona ni sobrescribe ningun PDF durante la simulacion.

## Uso de los chats

- El chat de Euler recibe objetivos, prioridades y rutas de origen.
- Euler entrega una asignacion estructurada para Gottfried.
- El chat de Gottfried solo trabaja sobre asignaciones identificables y conserva evidencia.
- Los chats no deben asumir que estan conectados. Una entrega cuenta como enviada unicamente cuando la herramienta de Codex lo confirma o el humano la traslada.
- Toda operacion fisica queda fuera del rol razonador y requiere aprobacion humana y un Ejecutor controlado.

Contexto comun: [CONTEXTO_COMPARTIDO.md](CONTEXTO_COMPARTIDO.md).
