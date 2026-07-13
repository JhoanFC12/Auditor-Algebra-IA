# Politica De Credenciales Admin Para Studio Remoto

Fecha: 2026-07-07

## Decision

Las credenciales permanentes del administrador de Studio se gestionan en la
base de datos del servidor, no como variables bootstrap persistentes.

Motivo:

- evita dejar una password productiva viva en archivos `.env`;
- evita que un reinicio o migracion rote accidentalmente al admin existente;
- mantiene el login por password separado de proveedores OAuth;
- no requiere tocar Google Sign-In ni Apple Sign-In.

## Estado esperado en produccion

En `/etc/nexumathjf/aula.env`:

- `SCAN_MATH_DB_DATABASE_URL`: configurado;
- `SCAN_MATH_DB_STORAGE_ROOT`: configurado;
- `SCAN_MATH_DB_FACTORY_STORAGE_ROOT`: configurado;
- `SCAN_MATH_DB_FACTORY_JOB_ROOT`: configurado;
- `SCAN_MATH_DB_FACTORY_MODEL_ROOT`: configurado;
- `SCAN_MATH_DB_FACTORY_HF_OCR_ENDPOINT_NAME`: configurado;
- `SCAN_MATH_DB_FACTORY_HF_OCR_BASE_URL`: configurado;
- `HF_TOKEN`: configurado;
- `SCAN_MATH_DB_CORS_ORIGINS_RAW`: configurado;
- `SCAN_MATH_DB_FACTORY_OFFICIAL_SOURCE=true`;
- `SCAN_MATH_DB_FACTORY_LOCAL_WRITE_MODE=backup_only`.

Las variables `SCAN_MATH_DB_BOOTSTRAP_ADMIN_*` pueden quedar vacias despues de
confirmar que existe un admin activo en la DB.

## Verificacion segura

No imprimir passwords ni tokens.

```bash
sudo bash -lc '
set -a
source /etc/nexumathjf/aula.env
set +a
cd /home/ubuntu/scan-math-db
/home/ubuntu/scan-math-db/.venv/bin/python3 - <<PY
from app.db import get_session_factory
from app.models import User
Session = get_session_factory()
with Session() as db:
    admins = db.query(User).filter(User.role == "admin").all()
    print(f"admins={len(admins)}")
    for admin in admins:
        print(
            f"id={admin.id}; username={admin.username}; "
            f"active={admin.is_active}; password_login={admin.password_login_enabled}; "
            f"email_verified={bool(admin.email_verified_at)}"
        )
PY
'
```

## Reset manual de password admin

Usar solo cuando el operador haya olvidado la password o quiera rotarla.

```bash
sudo bash -lc '
set -a
source /etc/nexumathjf/aula.env
set +a
cd /home/ubuntu/scan-math-db
read -rp "Admin username: " ADMIN_USERNAME
read -rsp "Nueva password: " ADMIN_PASSWORD
printf "\n"
export ADMIN_USERNAME ADMIN_PASSWORD
/home/ubuntu/scan-math-db/.venv/bin/python3 - <<PY
import os
from app.db import get_session_factory
from app.models import User
from app.security import hash_password
Session = get_session_factory()
username = os.environ["ADMIN_USERNAME"].strip()
password = os.environ["ADMIN_PASSWORD"]
with Session() as db:
    user = db.query(User).filter(User.username == username, User.role == "admin").first()
    if user is None:
        raise SystemExit("Admin no encontrado")
    user.password_hash = hash_password(password)
    user.password_login_enabled = True
    user.is_active = True
    db.add(user)
    db.commit()
print("Password admin actualizada")
PY
'
```

## Regla de seguridad

- No guardar passwords admin en git.
- No imprimir passwords admin en logs o reportes.
- No usar Google/Apple Sign-In para resolver esta tarea.
- Si se crea un admin temporal para smoke tests, eliminarlo al terminar.
