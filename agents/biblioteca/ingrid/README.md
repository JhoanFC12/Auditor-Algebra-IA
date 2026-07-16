---
agent_id: ingrid_daubechies_v1
capability_ids:
  - problem_detector_training_dataset_reviewer_v1
  - problem_segmentation_reviewer_v1
status: pilot
runtime_authorized: dataset-review-staging-only
chat_created: true
updated: 2026-07-14
---

# Ingrid Daubechies - Estado de definicion

Ingrid sera el agente revisor de segmentacion de problemas. No sustituira al detector YOLO existente: recibira sus propuestas, auditara la pagina completa, propondra correcciones y conservara evidencia para que el humano apruebe.

## Estado actual

- Definicion y diseno: autorizados.
- Revision y correccion de una copia versionada del dataset `v7_401`: autorizada.
- Ejecucion sobre instancias productivas: no autorizada todavia.
- Chat dedicado: `Agente Segmentacion - Ingrid Daubechies`, creado el 2026-07-14.
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

La separacion `problem` frente a `solution`, los subboxes de graficos y la clasificacion matematica permanecen fuera del piloto actual.

## Fuente de definicion

Formulario en Obsidian:

```text
$env:USERPROFILE\Documents\Obsidian Vault\02 Proyectos\Auditor-IA\Formulario - Ingrid Daubechies Segmentacion de Problemas v1.md
```

Perfil operativo: [CHAT_PROMPT.md](CHAT_PROMPT.md).

La primera asignacion trabaja unicamente sobre una copia versionada del dataset entrenado; no habilita automaticamente la revision de instancias productivas.
