from dotenv import load_dotenv

from config import settings
from database.connection import DatabaseManager

load_dotenv()


def ver_columnas(db_name: str | None = None):
    print("--- INSPECCIONANDO BASE DE DATOS ---")

    objetivo = db_name or settings.DB_NAME

    try:
        db = DatabaseManager(
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            sslmode=settings.DB_SSLMODE,
        )
        conn = db.get_connection(objetivo)
        cur = conn.cursor()

        sql = """
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns
            WHERE table_name = 'problemas'
            ORDER BY ordinal_position;
        """
        cur.execute(sql)
        filas = cur.fetchall()

        print(f"\nTABLA: problemas ({len(filas)} columnas encontradas)\n")
        print(f"{'NOMBRE COLUMNA':<25} | {'TIPO DATO':<15} | {'TIPO REAL'}")
        print("-" * 60)

        col_names = []
        for nombre, tipo, real in filas:
            print(f"{nombre:<25} | {tipo:<15} | {real}")
            col_names.append(nombre)

        conn.close()
        return col_names

    except Exception as e:
        print(f"Error conectando: {e}")
        return []


if __name__ == "__main__":
    ver_columnas()
