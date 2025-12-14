import psycopg2
from dotenv import load_dotenv

from config import settings

load_dotenv()


class DatabaseManager:
    def __init__(
        self,
        user: str | None = None,
        password: str | None = None,
        host: str | None = None,
        port: int | None = None,
        sslmode: str | None = None,
    ):
        self.user = user or settings.DB_USER
        self.password = password or settings.DB_PASSWORD
        self.host = host or settings.DB_HOST
        self.port = port or settings.DB_PORT
        self.sslmode = sslmode or settings.DB_SSLMODE
        self.connection = None

    # --- CAMBIO IMPORTANTE: Ahora se llama listar_bases_datos ---
    def listar_bases_datos(self):
        """
        Se conecta a la base de datos por defecto 'postgres' para consultar
        el catálogo y listar qué otras bases de datos existen.
        """
        try:
            conn = psycopg2.connect(
                dbname="postgres",
                user=self.user,
                password=self.password,
                host=self.host,
                port=self.port,
                sslmode=self.sslmode,
                options="-c client_encoding=utf8",
            )
            conn.autocommit = True
            cur = conn.cursor()

            cur.execute("SELECT datname FROM pg_database WHERE datistemplate = false;")
            dbs = [row[0] for row in cur.fetchall()]

            cur.close()
            conn.close()
            return dbs

        except Exception as e:
            print(f"⚠️ Error listando BDs: {repr(e)}")
            return []

    def get_connection(self, db_name):
        """
        Devuelve una conexión activa a una base de datos ESPECÍFICA.
        """
        try:
            conn = psycopg2.connect(
                dbname=db_name,
                user=self.user,
                password=self.password,
                host=self.host,
                port=self.port,
                sslmode=self.sslmode,
                options="-c client_encoding=utf8",
            )
            return conn
        except Exception as e:
            raise Exception(f"Error conectando a '{db_name}': {repr(e)}")
