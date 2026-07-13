# Quickstart: Pre-Migration Audit

This quickstart runs the first safe validation slice for the remote platform
migration. It is read-only: it counts database rows, detects Windows-only paths,
and writes reports without changing PostgreSQL or files.

## Prerequisites

- Run from `E:\Github\Auditor-IA`.
- Keep the local PostgreSQL source database running.
- Keep the Python virtual environment available at `.venv`.
- Confirm `.env.local` or the selected database profile can connect to the
  intended source database.

## Fast Report Without File Checks

Use this when you need a quick inventory and path rewrite estimate:

```powershell
.\.venv\Scripts\python.exe tools\audit_remote_migration_readiness.py `
  --profile local_mirror `
  --sample-limit 5 `
  --skip-file-checks
```

This mode does not verify whether local PDFs, covers, or generated files exist.
The report will mark file existence checks as skipped.

## Full Sampled Report

Use this before any real migration/export work:

```powershell
.\.venv\Scripts\python.exe tools\audit_remote_migration_readiness.py `
  --profile local_mirror `
  --sample-limit 50
```

## Outputs

- Human report:
  `docs/reporte_pre_migracion_servidor.md`
- Machine-readable report:
  `tmp/remote_migration_audit/audit.json`

## Required Review Before Migration

Do not export, restore, or rewrite paths until these items are reviewed:

1. The audited profile and database are the intended migration source.
2. All required core tables exist or their absence is expected.
3. Windows/UNC path families have server rewrite rules.
4. Missing required PDFs, covers, images, or generated Word files are resolved.
5. A backup and restore validation path exists for the server database.

## Current Baseline

The first quick audit against `local_mirror` found:

- `9769` rows in `problemas`.
- `52` rows in `libros_escaneo`.
- `518` rows in `libro_instancias_escaneo`.
- `1774` rows in `problema_origen`.
- `11566` Windows/UNC paths that must be rewritten before server cutover.

This baseline was generated with `--skip-file-checks`, so it is not evidence
that all referenced files exist. Run the full sampled report before migration.
