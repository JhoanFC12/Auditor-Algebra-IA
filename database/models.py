"""
CAPA DE MODELOS: Define la estructura de datos que maneja Python.
"""
from typing import List, Dict, Any

class SolucionModel:
    """Representa una solución o método alternativo."""
    def __init__(self, metodo_nombre: str, nivel: int, solucion_latex: str, reglas_citadas: List[int], autor: str = "GPT-4o"):
        self.metodo_nombre = metodo_nombre
        self.nivel = nivel
        self.solucion_latex = solucion_latex
        self.reglas_citadas = reglas_citadas
        self.autor = autor

    def to_json(self) -> Dict[str, Any]:
        return {
            "metodo_nombre": self.metodo_nombre,
            "nivel": self.nivel,
            "solucion_latex": self.solucion_latex,
            "reglas_citadas": self.reglas_citadas,
            "autor_ia": self.autor
        }

class ProblemaModel:
    """Representa un problema matemático de la tabla 'problemas'."""
    def __init__(self, **kwargs):
        self.id: int = kwargs.get('id')
        self.enunciado_latex: str = kwargs.get('enunciado_latex')
        self.archivo_origen: str = kwargs.get('archivo_origen')
        self.estado_consistencia: str = kwargs.get('estado_consistencia')
        self.reglas_sugeridas_ia: List[int] = kwargs.get('reglas_sugeridas_ia', [])
        self.tema: str = kwargs.get('tema')
        self.nivel_dificultad: str = kwargs.get('nivel_dificultad')
        self.soluciones: List[Dict] = kwargs.get('soluciones', [])
        self.respuesta: str = kwargs.get('respuesta')