# Catalogo Visual de Libros

## Objetivo

Esta fase crea un catalogo visual bootstrap para PDFs escaneados sin depender de OCR embebido ni tocar la base de datos. La prioridad es dejar:

- inventario trazable por PDF;
- evidencia visual por pagina;
- rangos por etiqueta;
- una nota Markdown util para Obsidian.

## Etiquetas de pagina

- `portada`
- `indice`
- `teoria`
- `ejemplos`
- `problemas_propuestos`
- `problemas_resueltos`
- `solucionario`
- `mixta`
- `dudosa`

## Arbol de salida

```text
.cache/book_catalog/
  inventory.jsonl
  pdf_listing.jsonl
  Listado PDF.md
  Duplicados.md
  Partes rechazadas.md
  duplicates.jsonl
  parts_rejected.jsonl
  tessdata/
    spa.traineddata
  Cursos/
    <curso>/
      00 Listado PDF.md
      <book_id>.md
  books/<book_id>/
    book.json
    pages/
      page-0001.png
    thumbnails/
      page-0001.jpg
    pages.jsonl
    ocr/
      page-0001.txt
      pages_ocr.jsonl
    ranges.json
    contact_sheets/
      contact_sheet_001.png
    obsidian.md
```

## `inventory.jsonl`

Un registro por PDF descubierto.

Campos base:

- `schema_version`
- `book_id`
- `source_root`
- `pdf_path`
- `pdf_relpath`
- `pdf_hash_sha256`
- `file_size_bytes`
- `modified_at`
- `discovered_at`
- `page_count`
- `metadata_title`
- `metadata_author`
- `inventory_status`
- `notes`

Uso:

- detectar lotes nuevos o cambiados;
- enlazar despues con Biblioteca/Fabrica;
- mantener trazabilidad aunque el nombre del archivo cambie.

## `pdf_listing.jsonl`

Un registro liviano por PDF encontrado, sin rasterizar paginas ni calcular hash completo.

Campos adicionales:

- `course`
- `source_top_folder`
- `bibliographic_title`
- `bibliographic_author`
- `bibliographic_editorial`
- `bibliographic_collection`
- `material_type`
- `bibliographic_status`
- `part_candidate`
- `part_reason`
- `general_candidate_path`

Uso:

- revisar todos los PDFs por curso antes de procesar;
- detectar fragmentos que apuntan a un PDF general;
- priorizar libros completos sobre recortes o PDFs por problema.

## `pages.jsonl`

Un registro por pagina procesada.

Campos base:

- `schema_version`
- `book_id`
- `pdf_path`
- `pdf_hash_sha256`
- `page_count`
- `page_number`
- `page_label`
- `label_source`
- `label_confidence`
- `review_status`
- `notes`
- `render_dpi`
- `image_width`
- `image_height`
- `image_path`
- `thumbnail_path`
- `ocr_text_path`
- `ocr_chars`
- `analyzed_at`

Nota:

- En esta primera iteracion el `label_source` es `bootstrap_visual_stub` y la etiqueta inicial es `dudosa` para toda pagina aun no revisada.

Cuando se ejecuta OCR local, `label_source` pasa a `local_tesseract_heuristic` y la etiqueta queda como sugerencia automatica pendiente de revision humana.

## `ocr/pages_ocr.jsonl`

Un registro por pagina OCR procesada.

Campos base:

- `schema_version`
- `book_id`
- `pdf_path`
- `pdf_hash_sha256`
- `page_number`
- `ocr_engine`
- `ocr_lang`
- `ocr_text_path`
- `ocr_chars`
- `label`
- `confidence`
- `reason`
- `scores`
- `analyzed_at`

Uso:

- cachear OCR local para no repetir lecturas costosas;
- clasificar paginas por heuristicas de texto;
- auditar manualmente errores desde Obsidian.

## `ranges.json`

Agrupa paginas contiguas por etiqueta dentro del subconjunto procesado.

Campos base:

- `schema_version`
- `book_id`
- `pdf_path`
- `pdf_hash_sha256`
- `processed_pages_total`
- `total_pdf_pages`
- `generated_at`
- `ranges`
- `label_counts`

## `obsidian.md`

Nota Markdown por libro para revision humana.

Incluye:

- metadatos del PDF;
- resumen de etiquetas;
- rangos procesados;
- embeds de contact sheets;
- enlaces a PNG y miniaturas por pagina.

## Duplicados y partes

El comando `process` rechaza por defecto:

- PDFs duplicados por hash exacto o firma visual inicial;
- PDFs detectados como parte de un PDF general, por ejemplo `Problema12.3.pdf`, carpetas `_IMG`, recortes o nombres con rangos de paginas.

Cuando se rechaza un fragmento se registra en:

- `parts_rejected.jsonl`
- `Partes rechazadas.md`

El criterio practico es: si existe un PDF general cercano, se cataloga el general y no el fragmento. Para auditar una excepcion se puede usar `--allow-part`, pero no debe ser el flujo normal.

## CLI inicial

Inventario:

```powershell
python tools/catalog_books_visual.py inventory --source-root "E:\Banco de Preguntas"
```

Prueba con un PDF:

```powershell
python tools/catalog_books_visual.py process "E:\Banco de Preguntas\1. ALGEBRA\1. Cuzcano\TEORÍA DE LAS ECUACIONES (1).pdf" --pages 1-12
```

Listado completo por curso y fragmentos:

```powershell
python tools/catalog_books_visual.py list-pdfs --source-root "E:\Banco de Preguntas" --output-root ".cache\book_catalog"
```

Actualizar vault Obsidian:

```powershell
python tools/catalog_books_visual.py vault --output-root ".cache\book_catalog"
```

OCR local y clasificacion:

```powershell
python tools/catalog_books_visual.py ocr-classify --first-index 15 --output-root ".cache\book_catalog" --dpi 140 --workers 4
```

Notas operativas:

- Usa Tesseract local, no Hugging Face.
- El idioma `spa` se guarda en `.cache/book_catalog/tessdata/spa.traineddata`.
- Las etiquetas son sugerencias, no veredicto final.

## Siguiente iteracion natural

1. Anotar manualmente `pages.jsonl` desde las contact sheets.
2. Agregar heuristicas visuales baratas para `portada` e `indice`.
3. Derivar rangos candidatos por densidad visual antes de cualquier OCR.
