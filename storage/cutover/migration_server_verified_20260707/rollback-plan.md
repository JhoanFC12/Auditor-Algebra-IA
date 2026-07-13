# Rollback Plan: migration_server_verified_20260707

## Preconditions

- Freeze Studio writes before cutover.
- Keep the previous deployment package available.
- Keep the verified PostgreSQL backup available until remote smoke passes.
- Keep `/srv/mathcontentstudio` untouched during rollback unless restoring from a known snapshot or bundle.

## Backup Reference

`/srv/mathcontentstudio/backups/mathcontentstudio_cutover_ready_20260707_133940.dump`

## Restore Test Evidence

The backup was restored into a temporary PostgreSQL database on `3.225.19.0`.

Validated restored counts:

- `libros_escaneo`: 70
- `libro_instancias_escaneo`: 430
- `problemas`: 6415
- `libro_artifacts_locales`: 62
- `instancia_artifacts_locales`: 470

Validated server assets:

- PDFs under `/srv/mathcontentstudio/library`: 70
- covers under `/srv/mathcontentstudio/library`: 41

## Rollback Steps

1. Freeze public Studio writes.
2. Stop the service:

   ```bash
   sudo systemctl stop nexumathjf-aula.service
   ```

3. Restore the database from the backup reference into `mathcontentstudio`.
4. If storage was modified during the cutover window, restore `/srv/mathcontentstudio` from the verified storage snapshot or the server asset bundle.
5. Restore the previous application release under `/home/ubuntu/scan-math-db` or switch Caddy back to the previous deployment target.
6. Start the service:

   ```bash
   sudo systemctl start nexumathjf-aula.service
   ```

7. Run the remote smoke in read-only mode.
8. Keep local PC writes frozen until the restored server state is verified.

## Writes To Freeze Or Replay

- Promotion jobs started during the cutover window.
- Word generation jobs started during the cutover window.
- Manual library or instance edits made during the cutover window.
- OCR/model queue jobs started during the cutover window.

## Validation Before Official Source

- Backup restore tested.
- Migration counts verified.
- Assets present under server storage.
- Remote Studio smoke passes.
- Word download smoke passes.
- Public Factory payloads do not expose local Windows paths.
