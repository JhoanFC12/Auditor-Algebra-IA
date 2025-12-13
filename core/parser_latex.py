import re
import os

class LatexParser:
    def __init__(self):
        # Regex tolerante: Acepta espacios extra en \item [ \textbf { 1. } ]
        self.patron_captura = re.compile(
            r"(\\item\s*\[\s*\\textbf\s*\{\s*(\d+)\s*\.\s*\}\s*\].*?)(?=\s*\\item\s*\[\s*\\textbf|\Z)", 
            re.DOTALL | re.IGNORECASE
        )

    def leer_archivo_seguro(self, ruta):
        if not os.path.exists(ruta): return None
        
        with open(ruta, 'rb') as f:
            raw = f.read()
        
        # Tu lógica original de 'modulo_conversor.py' para la codificación
        for encoding in ['utf-8', 'cp1252', 'latin-1']:
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode('latin-1', errors='replace')

    def procesar_archivo(self, ruta_archivo):
        contenido = self.leer_archivo_seguro(ruta_archivo)
        if not contenido: return []

        # DEBUG: Para ver si está leyendo bien
        print(f"--- LEYENDO: {os.path.basename(ruta_archivo)} ---")
        print(f"Inicio del contenido: {contenido[:50]!r}...") 

        nombre_archivo = os.path.basename(ruta_archivo)
        ruta_carpeta = os.path.dirname(ruta_archivo)

        coincidencias = self.patron_captura.findall(contenido)
        print(f"--- ENCONTRADOS: {len(coincidencias)} problemas ---")

        lista_problemas = []
        for bloque_completo, numero_str in coincidencias:
            lista_problemas.append({
                "numero_original": int(numero_str) if numero_str.isdigit() else 0,
                "archivo_origen": nombre_archivo,
                "enunciado_latex": bloque_completo.strip(),
                "ruta_carpeta": ruta_carpeta
            })

        return lista_problemas