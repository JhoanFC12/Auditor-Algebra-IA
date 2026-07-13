# Pre-Migration Readiness Report

Generated at: `2026-07-06T07:50:02.638979+00:00`
Profile: `local_mirror`
Database: `postgres@127.0.0.1:5432/mathcontentstudio_local_mirror`

## Executive Summary

- Core tables checked: `8`
- Missing core tables: `0`
- Path columns with values: `14`
- Windows/UNC paths needing rewrite: `11566`
- Sampled local files missing: `not checked`

## Core Table Counts

| Table | Rows |
|---|---:|
| `problemas` | 9769 |
| `libros_escaneo` | 52 |
| `libro_instancias_escaneo` | 518 |
| `libro_archivos_escaneo` | 0 |
| `libro_archivos_avance` | 0 |
| `libro_secciones_escaneo` | 0 |
| `origenes` | 43 |
| `problema_origen` | 1774 |

## Path Column Summary

| Table | Column | Non-empty | Windows/UNC | Server `/srv` | URL | Sampled |
|---|---|---:|---:|---:|---:|---:|
| `instancia_artifacts_locales` | `session_path_local` | 40 | 40 | 0 | 0 | 5 |
| `instancia_artifacts_locales` | `pdf_path_local` | 1 | 1 | 0 | 0 | 1 |
| `libro_artifacts_locales` | `session_path_local` | 1 | 1 | 0 | 0 | 1 |
| `libro_artifacts_locales` | `cover_path_local` | 1 | 1 | 0 | 0 | 1 |
| `libro_instancias_escaneo` | `pdf_path` | 110 | 110 | 0 | 0 | 5 |
| `libro_instancias_escaneo` | `session_path` | 518 | 518 | 0 | 0 | 5 |
| `libros_escaneo` | `pdf_path` | 52 | 52 | 0 | 0 | 5 |
| `libros_escaneo` | `session_path` | 1 | 1 | 0 | 0 | 1 |
| `libros_escaneo` | `cover_path` | 48 | 48 | 0 | 0 | 5 |
| `origenes` | `pdf_path` | 43 | 43 | 0 | 0 | 5 |
| `origenes` | `session_path` | 43 | 43 | 0 | 0 | 5 |
| `problema_pending_changes` | `archivo_origen` | 9786 | 5304 | 0 | 0 | 5 |
| `problemas` | `archivo_origen` | 9765 | 5304 | 0 | 0 | 5 |
| `problemas` | `ruta_imagen_solucion` | 100 | 100 | 0 | 0 | 5 |

## Missing Local File Samples

Local file existence checks were skipped for this run.

## Windows/UNC Path Samples

| Table | Column | Row | Exists locally | Path |
|---|---|---:|---|---|
| `instancia_artifacts_locales` | `session_path_local` | `1` | not checked | `K:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s1-angulo-trigonometrico-sistemas-de-medicion-angular\sessions\S1-Angulo_trigonometrico_y_Sistemas_de_medidas_Angulares-Resueltos.json` |
| `instancia_artifacts_locales` | `session_path_local` | `10` | not checked | `K:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s5-circunferencia-trigonometrica\sessions\propuestos.session.json` |
| `instancia_artifacts_locales` | `session_path_local` | `11` | not checked | `K:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s6-identidades-trigonometricas\sessions\resueltos.session.json` |
| `instancia_artifacts_locales` | `session_path_local` | `12` | not checked | `K:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s6-identidades-trigonometricas\sessions\propuestos.session.json` |
| `instancia_artifacts_locales` | `session_path_local` | `13` | not checked | `K:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s7-arco-compuesto\sessions\resueltos.session.json` |
| `instancia_artifacts_locales` | `pdf_path_local` | `1` | not checked | `K:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\S1-Ángulo_trigonométrico-Sistemas_de_medición_angular.pdf` |
| `libro_artifacts_locales` | `session_path_local` | `1` | not checked | `K:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\S1-Angulo_trigonometrico_y_Sistemas_de_medidas_Angulares.json` |
| `libro_artifacts_locales` | `cover_path_local` | `18` | not checked | `E:\Github\Auditor-IA\.cache\instance_factory\library_covers\mathcontentstudio-local-mirror\book-22-algebra-matematica-preuniversitaria\cover.png` |
| `libro_instancias_escaneo` | `pdf_path` | `1` | not checked | `E:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\S1-Ángulo_trigonométrico-Sistemas_de_medición_angular.pdf` |
| `libro_instancias_escaneo` | `pdf_path` | `4719` | not checked | `E:\Banco de Preguntas\1. ALGEBRA\19. Vesalius\01_ALGEBRA\01_LEYES DE EXPONENTES I-POTENCIACIÓN\ALG. (01)TEORIA-DE-EXPONENTES 75-------80.pdf` |
| `libro_instancias_escaneo` | `pdf_path` | `4720` | not checked | `E:\Banco de Preguntas\1. ALGEBRA\19. Vesalius\01_ALGEBRA\02_LEYES DE EXPONENTES II-RADICACIÓN\ALG. (02) RADICALES_81-------86.pdf` |
| `libro_instancias_escaneo` | `pdf_path` | `4721` | not checked | `E:\Banco de Preguntas\1. ALGEBRA\19. Vesalius\01_ALGEBRA\03_LEYES DE EXPONENTES III_ECUACIONES EXPONENCIALES Y TRASCENDENTES\ALG. (03)ECUAC.-EXPONENCIALES 87------92 (1).pdf` |
| `libro_instancias_escaneo` | `pdf_path` | `4722` | not checked | `E:\Banco de Preguntas\1. ALGEBRA\19. Vesalius\01_ALGEBRA\04_EXPRESIONES ALGEBRAICAS I-CLASIFICACIÓN Y GRADOS\ALG. (04) EXPRESIONES-ALGEBRAICAS_93-------98.pdf` |
| `libro_instancias_escaneo` | `session_path` | `1` | not checked | `E:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s1-angulo-trigonometrico-sistemas-de-medicion-angular\sessions\S1-Angulo_trigonometrico_y_Sistemas_de_medidas_Angulares-Resueltos.json` |
| `libro_instancias_escaneo` | `session_path` | `1207` | not checked | `E:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s5-circunferencia-trigonometrica\sessions\resueltos.session.json` |
| `libro_instancias_escaneo` | `session_path` | `1208` | not checked | `E:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s5-circunferencia-trigonometrica\sessions\propuestos.session.json` |
| `libro_instancias_escaneo` | `session_path` | `1303` | not checked | `E:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s6-identidades-trigonometricas\sessions\resueltos.session.json` |
| `libro_instancias_escaneo` | `session_path` | `1304` | not checked | `E:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s6-identidades-trigonometricas\sessions\propuestos.session.json` |
| `libros_escaneo` | `pdf_path` | `10` | not checked | `E:/Github/MathContentStudio/scan-math-db/storage/math_bank_local_mirror/library/s6-identidades-trigonometricas/source.pdf` |
| `libros_escaneo` | `pdf_path` | `11` | not checked | `E:/Github/MathContentStudio/scan-math-db/storage/math_bank_local_mirror/library/s7-arco-compuesto/source.pdf` |
| `libros_escaneo` | `pdf_path` | `12` | not checked | `E:/Github/MathContentStudio/scan-math-db/storage/math_bank_local_mirror/library/s8-arcos-multiples/source.pdf` |
| `libros_escaneo` | `pdf_path` | `13` | not checked | `E:/Github/MathContentStudio/scan-math-db/storage/math_bank_local_mirror/library/s9-transformaciones-trigonometricas/source.pdf` |
| `libros_escaneo` | `pdf_path` | `14` | not checked | `E:/Github/MathContentStudio/scan-math-db/storage/math_bank_local_mirror/library/s10-funciones-trigonometricas/source.pdf` |
| `libros_escaneo` | `session_path` | `5` | not checked | `E:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\S1-Angulo_trigonometrico_y_Sistemas_de_medidas_Angulares.json` |
| `libros_escaneo` | `cover_path` | `10` | not checked | `E:\Github\Auditor-IA\.cache\instance_factory\library_covers\mathcontentstudio-local-mirror\book-10-s6-identidades-trigonometricas\cover.png` |
| `libros_escaneo` | `cover_path` | `11` | not checked | `E:\Github\Auditor-IA\.cache\instance_factory\library_covers\mathcontentstudio-local-mirror\book-11-s7-arco-compuesto\cover.png` |
| `libros_escaneo` | `cover_path` | `12` | not checked | `E:\Github\Auditor-IA\.cache\instance_factory\library_covers\mathcontentstudio-local-mirror\book-12-s8-arcos-multiples\cover.png` |
| `libros_escaneo` | `cover_path` | `13` | not checked | `E:\Github\Auditor-IA\.cache\instance_factory\library_covers\mathcontentstudio-local-mirror\book-13-s9-transformaciones-trigonometricas\cover.png` |
| `libros_escaneo` | `cover_path` | `14` | not checked | `E:\Github\Auditor-IA\.cache\instance_factory\library_covers\mathcontentstudio-local-mirror\book-14-s10-funciones-trigonometricas\cover.png` |
| `origenes` | `pdf_path` | `102` | not checked | `E:\Banco de Preguntas\2. GEOMETRIA\19. IMPECUS\José Meza Barcena\CONSTRUCCIONES EN TRIÁNGULOS.pdf` |
| `origenes` | `pdf_path` | `1020` | not checked | `E:\Banco de Preguntas\2. GEOMETRIA\17. Otras Academias\ACADEMIA NOSTRADAMUS SEMESTRAL 2022 - I\ACADEMIA NOSTRADAMUS SEMESTRAL 2022 - I.pdf` |
| `origenes` | `pdf_path` | `1050` | not checked | `E:\Banco de Preguntas\2. GEOMETRIA\17. Otras Academias\ACADEMIA NOSTRADAMUS SEMESTRAL 2022 - I\ACADEMIA NOSTRADAMUS SEMESTRAL 2022 - I.pdf` |
| `origenes` | `pdf_path` | `1080` | not checked | `E:\Banco de Preguntas\2. GEOMETRIA\17. Otras Academias\ACADEMIA NOSTRADAMUS SEMESTRAL 2022 - I\ACADEMIA NOSTRADAMUS SEMESTRAL 2022 - I.pdf` |
| `origenes` | `pdf_path` | `1110` | not checked | `E:\Banco de Preguntas\2. GEOMETRIA\17. Otras Academias\ACADEMIA NOSTRADAMUS SEMESTRAL 2022 - I\ACADEMIA NOSTRADAMUS SEMESTRAL 2022 - I.pdf` |
| `origenes` | `session_path` | `102` | not checked | `E:\Banco de Preguntas\2. GEOMETRIA\19. IMPECUS\José Meza Barcena\CONSTRUCCIONES EN TRIÁNGULOS\construcciones-en-triangulos\sessions\problemas_propuestos.session.json` |
| `origenes` | `session_path` | `1020` | not checked | `D:\Banco de Preguntas\2. GEOMETRIA\17. Otras Academias\ACADEMIA NOSTRADAMUS SEMESTRAL 2022 - I\sessions\semana_n_09_-_circunferencia_ii.session.json` |
| `origenes` | `session_path` | `1050` | not checked | `D:\Banco de Preguntas\2. GEOMETRIA\17. Otras Academias\ACADEMIA NOSTRADAMUS SEMESTRAL 2022 - I\sessions\semana_n_10_-_puntos_notables.session.json` |
| `origenes` | `session_path` | `1080` | not checked | `D:\Banco de Preguntas\2. GEOMETRIA\17. Otras Academias\ACADEMIA NOSTRADAMUS SEMESTRAL 2022 - I\sessions\semana_n_11_-_proporcionalidad_de_segmentos.session.json` |
| `origenes` | `session_path` | `1110` | not checked | `E:\Banco de Preguntas\2. GEOMETRIA\17. Otras Academias\ACADEMIA NOSTRADAMUS SEMESTRAL 2022 - I\sessions\semana_n_12_-_semejanza_de_triangulos.session.json` |
| `problema_pending_changes` | `archivo_origen` | `(0,1)` | not checked | `E:\Banco de Preguntas\1. ALGEBRA\19. Vesalius\01_ALGEBRA\ALGEBRA_OTROS_PDFS_UNIDOS.pdf` |
| `problema_pending_changes` | `archivo_origen` | `(0,2)` | not checked | `E:\Banco de Preguntas\1. ALGEBRA\19. Vesalius\01_ALGEBRA\ALGEBRA_OTROS_PDFS_UNIDOS.pdf` |
| `problema_pending_changes` | `archivo_origen` | `(0,3)` | not checked | `E:\Banco de Preguntas\1. ALGEBRA\19. Vesalius\01_ALGEBRA\ALGEBRA_OTROS_PDFS_UNIDOS.pdf` |
| `problema_pending_changes` | `archivo_origen` | `(0,4)` | not checked | `E:\Banco de Preguntas\1. ALGEBRA\19. Vesalius\01_ALGEBRA\ALGEBRA_OTROS_PDFS_UNIDOS.pdf` |
| `problema_pending_changes` | `archivo_origen` | `(0,5)` | not checked | `E:\Banco de Preguntas\1. ALGEBRA\19. Vesalius\01_ALGEBRA\ALGEBRA_OTROS_PDFS_UNIDOS.pdf` |
| `problemas` | `archivo_origen` | `2817` | not checked | `K:\Banco de Preguntas\1. ALGEBRA\10. Espinoza_Ramos\ALGEBRA 6 PREUN VOLUMEN 1.pdf` |
| `problemas` | `archivo_origen` | `2818` | not checked | `K:\Banco de Preguntas\1. ALGEBRA\10. Espinoza_Ramos\ALGEBRA 6 PREUN VOLUMEN 1.pdf` |
| `problemas` | `archivo_origen` | `2819` | not checked | `K:\Banco de Preguntas\1. ALGEBRA\10. Espinoza_Ramos\ALGEBRA 6 PREUN VOLUMEN 1.pdf` |
| `problemas` | `archivo_origen` | `2820` | not checked | `K:\Banco de Preguntas\1. ALGEBRA\10. Espinoza_Ramos\ALGEBRA 6 PREUN VOLUMEN 1.pdf` |
| `problemas` | `archivo_origen` | `2821` | not checked | `K:\Banco de Preguntas\1. ALGEBRA\10. Espinoza_Ramos\ALGEBRA 6 PREUN VOLUMEN 1.pdf` |
| `problemas` | `ruta_imagen_solucion` | `204` | not checked | `K:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s1-angulo-trigonometrico-sistemas-de-medicion-angular\solutions\resueltos\s1-angulo-trigonometrico-sistemas-de-medicion-angular_resueltos_item1_20260302_235617.png` |
| `problemas` | `ruta_imagen_solucion` | `205` | not checked | `K:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s1-angulo-trigonometrico-sistemas-de-medicion-angular\solutions\resueltos\s1-angulo-trigonometrico-sistemas-de-medicion-angular_resueltos_item2_20260302_235642.png` |
| `problemas` | `ruta_imagen_solucion` | `206` | not checked | `K:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s1-angulo-trigonometrico-sistemas-de-medicion-angular\solutions\resueltos\s1-angulo-trigonometrico-sistemas-de-medicion-angular_resueltos_item3_20260302_235658.png` |
| `problemas` | `ruta_imagen_solucion` | `207` | not checked | `K:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s1-angulo-trigonometrico-sistemas-de-medicion-angular\solutions\resueltos\s1-angulo-trigonometrico-sistemas-de-medicion-angular_resueltos_item4_20260303_003757.png` |
| `problemas` | `ruta_imagen_solucion` | `208` | not checked | `K:\Banco de Preguntas\4. TRIGONOMETRIA\12. Editorial_RODO\1. Walter_Mori_Valverde\s1-angulo-trigonometrico-sistemas-de-medicion-angular\solutions\resueltos\s1-angulo-trigonometrico-sistemas-de-medicion-angular_resueltos_item5_20260303_003816.png` |

## Required Next Actions

1. Confirm the audited database is the intended migration source.
2. Resolve missing required PDFs/covers before export.
3. Define server rewrite rules for every Windows/UNC path family.
4. Run bundle export only after this report is reviewed.
5. Restore to a test PostgreSQL database before production cutover.
