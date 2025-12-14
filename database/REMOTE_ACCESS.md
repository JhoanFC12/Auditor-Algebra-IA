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
   - Asegúrate de publicar el puerto donde corre PostgreSQL (5432 por defecto).
   - En entornos domésticos necesitarás reglas de NAT/Port Forwarding hacia el host que ejecuta la BD.
   - Si usas una nube (AWS, GCP, etc.), crea una regla de seguridad que permita TCP en ese puerto solo a las IPs que necesites.

4. **Opcional: túneles seguros en lugar de exponer el puerto**
   - Para mayor seguridad usa un túnel (VPN, WireGuard o SSH):
     ```bash
     ssh -N -L 5432:localhost:5432 usuario@tu-servidor
     ```
   - Luego conecta la app a `localhost:5432` mientras el túnel esté activo.

5. **Configura la app con variables de entorno**
   - En el archivo `.env` (cargado por el proyecto) define:
     ```
     DB_HOST=<IP_publica_o_DNS>
     DB_PORT=5432
     DB_NAME=<tu_bd>
     DB_USER=<usuario>
     DB_PASSWORD=<contraseña>
     DB_SSLMODE=require   # recomendado si hay TLS en el servidor
     ```
   - En el celular o cliente remoto usa los mismos datos de conexión.

6. **Pruebas rápidas desde la app**
   - Ejecuta `python ver_estructura.py` para validar que la app se conecta y lista columnas.

> **Nota de seguridad**: Abrir PostgreSQL a Internet sin TLS ni filtrado de IPs es riesgoso. Usa contraseñas robustas, activa TLS cuando sea posible y limita las IPs permitidas.
