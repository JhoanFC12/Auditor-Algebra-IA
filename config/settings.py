"""
CONFIGURACIÓN GLOBAL DEL SISTEMA
Centraliza credenciales y constantes.
"""
import os

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# --- PARÁMETROS DE BASE DE DATOS ---
# Ajusta estos valores en tu .env para apuntar a un host público o un túnel seguro
# si necesitas conectarte desde fuera de la red local.
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")
# Permite forzar SSL/TLS cuando la base está expuesta públicamente.
DB_SSLMODE = os.getenv("DB_SSLMODE", "prefer")
