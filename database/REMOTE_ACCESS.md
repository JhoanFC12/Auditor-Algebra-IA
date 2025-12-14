# Acceso remoto a PostgreSQL (fuera de tu red local)

Estos pasos permiten que la base de datos pueda consumirse desde otros dispositivos (ej. tu celular). Aplícalos con cuidado y siempre usando contraseñas fuertes.

1. **Configura PostgreSQL para escuchar conexiones externas**
   - Edita `postgresql.conf` y cambia:
     ```
     listen_addresses = '*'
     ```
   - Reinicia PostgreSQL para aplicar el cambio.

2. **Autoriza clientes en `pg_hba.conf`**
   - Añade una línea para tu rango de IPs o para pruebas abiertas (menos seguro):
     ```
     # Solo una IP concreta
     host    all    all    203.0.113.42/32    scram-sha-256

     # Ejemplo más abierto (solo para redes controladas)
     # host    all    all    0.0.0.0/0    scram-sha-256
     ```
   - Prefiere `scram-sha-256` o `md5` si tu versión no soporta SCRAM.

3. **Abre el puerto en el firewall/router**
   - Asegúrate de publicar el puerto donde corre PostgreSQL (en este proyecto usamos 5433 por defecto).
   - En entornos domésticos necesitarás reglas de NAT/Port Forwarding hacia el host que ejecuta la BD.
   - Si usas una nube (AWS, GCP, etc.), crea una regla de seguridad que permita TCP en ese puerto solo a las IPs que necesites.

4. **Opcional: túneles seguros en lugar de exponer el puerto**
   - Para mayor seguridad usa un túnel (VPN, WireGuard o SSH):
     ```bash
     ssh -N -L 5433:localhost:5433 usuario@tu-servidor
     ```
   - Luego conecta la app a `localhost:5433` mientras el túnel esté activo.

5. **Configura la app con variables de entorno**
   - En el archivo `.env` (cargado por el proyecto) define:
     ```
     DB_HOST=<IP_publica_o_DNS>
     DB_PORT=5433
     DB_NAME=<tu_bd>
     DB_USER=<usuario>
     DB_PASSWORD=<contraseña>
     DB_SSLMODE=require   # recomendado si hay TLS en el servidor
     ```
   - En el celular o cliente remoto usa los mismos datos de conexión.

6. **Pruebas rápidas desde la app**
   - Ejecuta `python ver_estructura.py` para validar que la app se conecta y lista columnas.
   - Si quieres probar desde otra máquina o celular:
     1. Instala un cliente de PostgreSQL (por ejemplo, `psql` en escritorio o una app como "PG Client" en Android/Termux).
     2. Conéctate usando los mismos parámetros, cambiando host/puerto según corresponda. Ejemplo en otra PC:
        ```bash
        psql "host=<IP_publica_o_DNS> port=5433 dbname=<tu_bd> user=<tu_usuario> sslmode=require"
        ```
     3. Ejecuta un comando simple como `\dt` o `SELECT 1;` para confirmar conectividad y permisos.

7. **Checklist de verificación**
   - `pg_hba.conf` incluye tu IP/rango con método de autenticación correcto.
   - El firewall/NAT expone el puerto 5433 al host donde está PostgreSQL.
   - Puedes conectar con `psql` desde fuera de la red y ves tablas con `\dt`.
   - `python ver_estructura.py` desde la app muestra las columnas de `problemas` sin errores.

> **Nota de seguridad**: Abrir PostgreSQL a Internet sin TLS ni filtrado de IPs es riesgoso. Usa contraseñas robustas, activa TLS cuando sea posible y limita las IPs permitidas.
