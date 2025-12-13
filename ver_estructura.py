from database.connection import DatabaseManager

def ver_columnas():
    print("--- INSPECCIONANDO BASE DE DATOS 'ALGEBRA' ---")
    
    # 1. Conectar
    try:
        db = DatabaseManager()
        conn = db.get_connection("matematica") # Asegúrate que tu BD se llama 'Algebra' o 'matematicas'
        cur = conn.cursor()
        
        # 2. Preguntar a PostgreSQL las columnas de la tabla 'problemas'
        sql = """
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns
            WHERE table_name = 'problemas'
            ORDER BY ordinal_position;
        """
        cur.execute(sql)
        filas = cur.fetchall()
        
        print(f"\nTABLA: problemas ({len(filas)} columnas found)\n")
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