"""
CONFIGURACIÓN GLOBAL DEL SISTEMA
Centraliza credenciales y constantes.
"""
import os

import os

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")
