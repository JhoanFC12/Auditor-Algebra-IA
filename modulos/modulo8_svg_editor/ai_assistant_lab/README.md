# Laboratorio IA para SVG Editor

Este paquete es un entorno paralelo. No esta conectado al editor principal.

Objetivo:

- Leer un SVG y construir un inventario de puntos y segmentos.
- Convertir una instruccion del usuario en un plan JSON seguro.
- Aplicar ese plan sobre una copia del SVG.
- Probar el flujo antes de integrarlo al editor real.

Flujo propuesto:

1. `inventory.py` detecta puntos, etiquetas y segmentos.
2. `planner.py` convierte texto natural en operaciones.
3. `contracts.py` valida que las operaciones sean permitidas.
4. `executor.py` aplica cambios experimentales sobre el SVG.

Uso de prueba:

```powershell
python -m modulos.modulo8_svg_editor.ai_assistant_lab.cli `
  --svg entrada.svg `
  --instruction "proyecta A sobre BC y coloca grosor 3" `
  --out salida.svg `
  --plan-out plan.json
```

La pieza que luego se reemplaza por un modelo real es `LLMPlanner`.
El modelo no debe devolver SVG, sino un JSON de operaciones.

Interfaz grafica:

```powershell
python -m modulos.modulo8_svg_editor.ai_assistant_lab.gui
```

Tambien se puede abrir desde:

```powershell
E:\Github\Auditor-IA\abrir_laboratorio_svg_ia.cmd
```

Entrada por voz:

- La ventana incluye un boton `Hablar`.
- Es opcional y requiere instalar `SpeechRecognition` y `PyAudio`.
- Si no estan instalados, el laboratorio sigue funcionando con texto normal.

Visualizador:

- La ventana incluye un panel `Visualizador SVG`.
- Usa `matplotlib` y `Pillow`, que ya estan disponibles en el entorno.
- Renderiza los elementos principales del editor: rectangulos, lineas, puntos,
  poligonos, textos y etiquetas convertidas a `data-text`.
