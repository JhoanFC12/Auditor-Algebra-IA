# Módulos de la interfaz

Esta carpeta contiene los módulos llamados desde `main.py`. Ahora la interfaz es 100% de
terminal, sin depender de Tkinter, y los módulos incluyen implementaciones mínimas que
puedes extender con tu flujo real.

## Estructura
- `modulo1_cargador/gui_cargador.py`: interfaz de terminal para cargar archivos `.tex`.
- `modulo2_auditor/gui_auditor.py`: ventana de auditoría híbrida.
- `modulo2_auditor/gui_teoria.py`: carga de teoría/base de conocimiento.

Asegúrate de mantener los `__init__.py` si usas imports relativos.

- `modulo8_svg_editor/svg_editor_v2_copy.py`: editor SVG avanzado para diagramas matematicos.
