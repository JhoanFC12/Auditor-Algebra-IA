---
agent_id_proposed: ingrid_daubechies_v1
capability_id_proposed: problem_segmentation_reviewer_v1
status: definition
runtime_authorized: false
chat_created: false
updated: 2026-07-14
---

# Ingrid Daubechies - Estado de definicion

Ingrid sera el agente revisor de segmentacion de problemas. No sustituira al detector YOLO existente: recibira sus propuestas, auditara la pagina completa, propondra correcciones y conservara evidencia para que el humano apruebe.

## Estado actual

- Definicion y diseno: autorizados.
- Ejecucion sobre instancias: no autorizada todavia.
- Chat dedicado: se creara despues de aprobar el contrato V1.
- Escritura canonica: prohibida.
- Activacion de OCR o entrenamiento: fuera de su autoridad.

## Frontera propuesta

```text
Euler asigna
-> Gottfried entrega instancia y rangos aprobados
-> detector multiclass propone boxes
-> Ingrid audita y propone correcciones
-> humano aprueba o rechaza
-> servicio aplica lo aprobado y regenera staging
-> Euler verifica el cierre
```

## Clases V1 recomendadas

| Clase | Funcion |
|---|---|
| `problem` | Envolvente completa que producira el crop del problema |
| `problem_number` | Subbox auxiliar de numeracion |
| `answer_block` | Subbox auxiliar de alternativas o respuestas propuestas |

La separacion `problem` frente a `solution`, los subboxes de graficos y la clasificacion matematica permanecen fuera de V1 hasta decision humana.

## Fuente de definicion

Formulario en Obsidian:

```text
$env:USERPROFILE\Documents\Obsidian Vault\02 Proyectos\Auditor-IA\Formulario - Ingrid Daubechies Segmentacion de Problemas v1.md
```

No se debe crear `CHAT_PROMPT.md` hasta resolver las decisiones marcadas como bloqueantes en ese formulario.
