# DEV_SETUP (Windows)

## 1) Clonar y preparar entorno
```powershell
git clone https://github.com/JhoanFC12/Auditor-Algebra-IA.git
cd Auditor-IA
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-lock.txt
```

Instalacion determinista: usa `requirements-lock.txt` (no `pip install -U` sin control).

## Bootstrap en 1 comando (recomendado para cambio de PC)
```powershell
.\bootstrap.cmd -Provider hf
```

Nota:
- Si aun no cargaste token, primero edita `.env.local` y define `HF_TOKEN`.
- Si quieres preparar entorno sin validar HF aun, usa `-Provider ocr` temporalmente.

Alternativa directa:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap-windows.ps1 -Provider hf
```

Flags utiles:
- `-SkipDoctor`
- `-SkipTests`
- `-PythonVersion 3.11`

## 2) Configurar variables de entorno
```powershell
Copy-Item .env.example .env.local
```

Edita `.env.local` con tus valores locales (no subir secretos a git).

Ubicacion recomendada del env local: raiz del repo (`Auditor-IA/.env.local`).

## 3) Normalizar encoding/EOL del env
```powershell
.\scripts\normalize-env.ps1
```

Por defecto usa `.env.local` si existe; si no, `.env`.

## 4) Diagnostico de maquina
```powershell
.\scripts\doctor.ps1 -Provider hf
```

Si doctor falla, corrige primero (env faltante, BOM, lockfile, etc.).

## 5) Ejecutar aplicacion / CLI
Launcher GUI:
```powershell
python main.py
```

CLI de escaneo:
```powershell
.\scanproblems.cmd --input .\imagenes --start 1 --curso Algebra --tema Ecuaciones --out .\problemas.tex
```

Tambien puedes usar:
```powershell
python .\tools\scanproblems.py --input .\imagenes --start 1 --curso Algebra --tema Ecuaciones --out .\problemas.tex
```

Modo determinista estricto (recomendado):
```powershell
python .\tools\scanproblems.py --input .\imagenes --start 1 --curso Algebra --tema Ecuaciones --out .\problemas.tex --provider hf --strict-json --temperature 0 --top-p 1 --max-tokens 3200 --seed 42
```

## 6) Tests
```powershell
python -m unittest discover -s tests -p "test_*.py"
```

## 7) Dataset y evaluacion
Construccion de dataset:
```powershell
python .\tools\build_scan_dataset.py --images .\dataset\images --labels .\dataset\labels --out .\dataset\build
```

Evaluacion:
```powershell
python .\tools\eval_scan_dataset.py --gold-dir .\dataset\gold --pred-dir .\dataset\pred --out .\dataset\eval.json
```

## core.autocrlf / core.eol
Repositorio usa `.gitattributes` con `eol=lf`.

Verificar configuracion:
```powershell
git config --global core.autocrlf
git config --global core.eol
git config --local core.autocrlf
git config --local core.eol
```

Recomendado en Windows:
- `core.autocrlf=false`
- `core.eol=lf`

## Cambio de PC (checklist de 5 pasos)
1. `git pull`
2. `pip install -r requirements-lock.txt`
3. `.\scripts\normalize-env.ps1`
4. `.\scripts\doctor.ps1 -Provider hf`
5. `python main.py` o `.\scanproblems.cmd ...`

## Sesiones portables entre PCs
- Al guardar sesion desde GUI, ahora se crea automaticamente una carpeta junto al JSON:
  - `<nombre_sesion>.json`
  - `<nombre_sesion>_tmp\` (sources/crops/segments)
- Para continuar en otra PC, copia ambos (`.json` y `_tmp`) manteniendo la misma carpeta relativa.
- Al cargar sesion, el sistema resuelve rutas relativas contra la ubicacion del JSON.
