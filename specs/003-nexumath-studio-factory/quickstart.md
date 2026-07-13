# Quickstart: NexumathJF Studio Factory Validation

This guide validates the plan before implementation and later becomes the smoke checklist for the first remote replacement.

## Prerequisites

- `E:/Github/MathContentStudio/scan-math-db` exists and can run locally.
- `E:/Github/Auditor-IA` contains the current Biblioteca/Fabrica services.
- A test PostgreSQL database or safe local test database is available.
- Server storage root is configured for test mode.
- Hugging Face OCR token and endpoint settings are available only in environment variables.
- No production cutover is performed during this quickstart.

## Local Inventory

From `E:/Github/Auditor-IA`:

```powershell
Get-ChildItem E:\Github\MathContentStudio\scan-math-db\app\web
Get-ChildItem E:\Github\MathContentStudio\scan-math-db\app\api
Get-ChildItem E:\Github\Auditor-IA\modulos\instance_factory
```

Expected:

- Studio web files are present.
- Studio API is present.
- Biblioteca/Fabrica modules are present.

## Start Current Studio Locally

From `E:/Github/MathContentStudio/scan-math-db`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_scan_math_db_web.ps1 -OpenPath /studio
```

Expected:

- Local Studio opens.
- Login works for a Studio admin or collaborator.
- `/health` returns OK or a useful diagnostic.

## Validate Existing Math Bank Access

Open or request:

```text
/studio/books
/studio/books/{book_id}/instances
```

Expected:

- Books load from the configured math bank.
- Instances load for a selected book.
- Missing math-bank configuration returns a user-safe 503 message.

## Validate Future Factory Bootstrap Contract

After implementation, request:

```text
/studio/factory/bootstrap
```

Expected:

- User role is returned.
- Database, storage, OCR endpoint, model readiness, and active job status are visible.
- No private Windows paths, tokens, tracebacks, or server secret paths appear.

## Validate Remote Workflow Smoke

After implementation, use one safe test book/instance:

1. Open Studio from the local or domain route.
2. Open Biblioteca.
3. Open one instance.
4. Select a small page range.
5. Start page segmentation as a job.
6. Refresh the browser.
7. Confirm job status is recoverable.
8. Review boxes.
9. Materialize staging.
10. Start OCR queue.
11. Confirm OCR status and partial errors are visible.
12. Save one final review.
13. Promote only reviewed records.
14. Generate one Word document.
15. Download or open the generated Word artifact from Studio.

Expected:

- Each long action produces a `job_id`.
- Refresh does not cancel jobs.
- Stale records are blocked after source edits.
- Final Word is stored as a server asset.
- Final Word download returns a `.docx` artifact and no public payload exposes a
  Windows path, token, traceback, or private server path.

## Remote Cutover Smoke

Run this only after server deployment and migration staging are ready:

1. Open the public Studio domain or subdomain.
2. Log in with a Studio operator account.
3. Confirm Biblioteca/Fabrica is the first working surface.
4. Open a book with migrated PDF and cover assets.
5. Open one instance and recover its current stage.
6. Start or inspect one job and refresh the browser.
7. Confirm job state is still available after reconnect.
8. Save a review/promotion action on safe test data.
9. Generate a Word document from server records.
10. Download the Word document and confirm it opens.
11. Confirm backup and rollback commands are documented before cutover.

Expected:

- The flow does not require the local desktop launcher.
- The local PC is not silently writing official data.
- Public responses contain server-safe references only.
- Rollback remains possible if the domain cutover fails.

### Public Domain Routing Gate

Run this before authenticated Studio smoke. The current known state on
2026-07-06 is:

```text
https://api.nexumathjf.com/health                 -> 200 OK
https://nexumathjf.com/studio                     -> 404
https://nexumathjf.com/studio/factory/bootstrap   -> 404
```

Expected after deployment:

- API health returns 200.
- `/studio` is not 404.
- `/studio/factory/bootstrap` is not 404.
- `studio.nexumathjf.com` is not 404.
- `aula.nexumathjf.com` remains available or intentionally protected.
- Public response samples do not expose Windows paths, tokens, tracebacks, or
  private server paths.

Interpretation:

- 200, 302, 401 or 403 can be acceptable depending on authentication.
- 404 is not acceptable for Studio Factory after the domain is routed.
- 5xx is not acceptable for cutover.

If this gate fails, do not run database cutover. Fix proxy/tunnel/routing first.

Automated command from `E:/Github/MathContentStudio/scan-math-db`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_domain_routing.ps1
```

The script writes a JSON report under `storage/diagnostics` and exits with an
error when any required route still returns 404 or 5xx.

### Apply Public Routing

After the release is deployed and the server readiness gate passes, apply the
public Caddy routing before running the domain gate:

```powershell
cd E:/Github/MathContentStudio/scan-math-db

powershell -NoProfile -ExecutionPolicy Bypass -File scripts/apply_nexumath_routing.ps1 `
  -DeployEnvFile .env.deploy.local `
  -RemoteDeployDir "/opt/nexumath/scan-math-db" `
  -DryRun

powershell -NoProfile -ExecutionPolicy Bypass -File scripts/apply_nexumath_routing.ps1 `
  -DeployEnvFile .env.deploy.local `
  -RemoteDeployDir "/opt/nexumath/scan-math-db"
```

This renders `deploy/caddy/Caddyfile.nexumathjf.example` on the server,
validates it with Caddy and reloads the Caddy service. The server helper uses
`sudo` automatically when the SSH user is not root, so that user must be allowed
to update `/etc/caddy/Caddyfile` and reload Caddy.

### Production Deploy Preflight

Before deploying to the server, validate the production deployment template from
`E:/Github/MathContentStudio/scan-math-db`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_production_deploy_config.ps1
```

Expected:

- `docker-compose.production.yml` exists.
- `.env.production.example` exists.
- PostgreSQL has no public `5432` port.
- API is bound to `127.0.0.1:${NEXUMATH_API_PORT}` for reverse proxy/tunnel use.
- Demo passwords are absent.
- Factory official source remains `false` by default.

On a server or machine with Docker, also run:

```bash
docker compose --env-file .env.production -f docker-compose.production.yml config
```

Optional deploy helper dry-run from `E:/Github/MathContentStudio/scan-math-db`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/deploy_nexumath_studio_release.ps1 `
  -SshHost "<ssh-host-or-ip>" `
  -ServerUser "<ssh-user>" `
  -ReleaseZip "storage/releases/nexumath_studio_smoke.zip" `
  -RemoteDeployDir "/opt/nexumath/scan-math-db" `
  -RemoteReleaseDir "/opt/nexumath/releases" `
  -NoStart `
  -DryRun
```

Expected:

- The script resolves the clean release ZIP.
- The printed remote commands upload/extract into the deployment directory.
- `.env.production` is preserved from the previous deployment when present.
- Docker Compose config and `/health` are checked when `-NoStart` is omitted.

Before touching the server, generate the handoff packet that groups release
metadata, checksums, required production environment variables and the exact
ordered server commands:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_server_deploy_handoff.ps1 `
  -ReleaseName "nexumath_studio_smoke" `
  -DeployEnvFile .env.deploy.local `
  -Force
```

Expected:

- `storage/deploy_handoff/nexumath_studio_smoke_latest/deploy-handoff.json`
  exists.
- `SERVER_STEPS.md` contains the dry-run, deploy, readiness, routing, smoke and
  strict cutover commands.
- `required-server-env.md` lists production variables without values.
- `checksums.txt` records release and cutover evidence hashes.

On the server, after extracting the release, prepare the deployment directory
and storage roots without starting the stack:

```bash
cd /opt/nexumath/scan-math-db
bash deploy/server/prepare_nexumath_studio_server.sh --deploy-dir /opt/nexumath/scan-math-db
```

After editing `.env.production`, validate required values and Docker Compose:

```bash
bash deploy/server/prepare_nexumath_studio_server.sh \
  --deploy-dir /opt/nexumath/scan-math-db \
  --require-env-ready \
  --check-compose
```

Create a local deploy env file on the Windows operator machine:

```powershell
cd E:\Github\MathContentStudio\scan-math-db
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/init_nexumath_deploy_env.ps1 -OpenEditor
```

`NEXUMATH_SSH_HOST` must be the real SSH host/IP of the Linux server. Do not use
`nexumathjf.com` here when the public domain is behind Cloudflare. The public
domain remains in `SCAN_MATH_DB_REMOTE_BASE_URL` for HTTP smoke validation.

If the workstation has no SSH deploy key yet, create a dedicated key and copy
the printed public key into the server user's `~/.ssh/authorized_keys`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup_nexumath_ssh_key.ps1 -SkipValidate
```

When the provider gives the real SSH host/IP and Linux user, update
`.env.deploy.local` through the safe target helper:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/set_nexumath_deploy_target.ps1 `
  -SshHost "<ssh-host-or-ip>" `
  -ServerUser "<ssh-user>" `
  -IdentityFile "C:\Users\Danny Fabián\.ssh\nexumathjf_deploy_ed25519" `
  -CheckNetwork
```

The helper preserves existing Studio smoke credentials, clears the legacy
`NEXUMATH_SERVER_HOST` alias and fails if the SSH host is actually a public
Cloudflare domain.

Then verify local deployment prerequisites without printing secrets:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_nexumath_deploy_prereqs.ps1 `
  -DeployEnvFile .env.deploy.local `
  -RunBundleReadiness
```

If the SSH host/user is still unknown, generate the handoff and use
`SSH_ACCESS_REQUEST.md` to request the exact server access:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_server_deploy_handoff.ps1 `
  -ReleaseName "nexumath_studio_smoke" `
  -DeployEnvFile .env.deploy.local `
  -Force
notepad storage\deploy_handoff\nexumath_studio_smoke_latest\SSH_ACCESS_REQUEST.md
```

To confirm that the public domains are not valid SSH hosts, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_nexumath_ssh_host_candidates.ps1
```

Expected current result until the provider gives the real SSH host/IP:
`nexumathjf.com`, `www.nexumathjf.com`, `api.nexumathjf.com`,
`studio.nexumathjf.com` and `aula.nexumathjf.com` classify as
`public_cloudflare_http_not_ssh`.

Create the portable handoff ZIP when the provider or a second machine needs the
same deployment context:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_deploy_handoff_archive.ps1 `
  -ReleaseName "nexumath_studio_smoke" `
  -Force

powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_deploy_handoff_archive.ps1 `
  -ReleaseName "nexumath_studio_smoke"
```

Expected outputs:

- `storage/deploy_handoff/nexumath_studio_smoke_handoff.zip`
- `storage/deploy_handoff/nexumath_studio_smoke_handoff.json`

The archive builder fails if it detects `.env` files, private keys or common
token patterns in the handoff folder. The verifier reopens the generated ZIP,
checks required handoff entries and validates the archive SHA against the JSON
summary.

This preflight inspects the release ZIP before SSH: it verifies the SHA against
the release summary, checks that the deploy/routing helpers are packaged and
fails if real `.env` files are inside the ZIP.

For the full ordered cutover flow, run the orchestrator in dry-run first:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_nexumath_studio_cutover.ps1 `
  -DeployEnvFile .env.deploy.local `
  -ReleaseName "nexumath_studio_smoke" `
  -ApplyDomainRouting `
  -SkipRemoteSmoke `
  -SkipCutoverPacket `
  -NoStart `
  -DryRun
```

To include the data migration path in the same dry-run, add:

```powershell
  -RunBundleDeploy `
  -RunServerRestoreDryRun
```

Those flags call the bundle upload and remote restore helpers in dry-run mode,
so the command validates local bundle readiness and prints the SSH/SCP/restore
commands without modifying the server.

Expected:

- Build, deploy, optional bundle upload, optional server restore, remote smoke,
  cutover packet and strict readiness are ordered.
- With `-ApplyDomainRouting`, the runner applies Caddy routing before the
  public domain gate.
- The public domain routing gate runs after deploy/start and before the
  authenticated remote smoke.
- Missing server/user/credentials fail with explicit messages.
- `-DryRun` prints commands without modifying the server.
- `-RunStrictReadiness` remains reserved for the final post-migration evidence.

For an already deployed server where build/deploy are intentionally skipped,
force the public gate explicitly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_nexumath_studio_cutover.ps1 `
  -SkipBuild `
  -SkipDeploy `
  -SkipRemoteSmoke `
  -SkipCutoverPacket `
  -RunDomainRouting
```

After `.env.production` exists on the server, validate server readiness through
SSH:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_server_deploy_readiness.ps1 `
  -SshHost "<ssh-host-or-ip>" `
  -ServerUser "<ssh-user>" `
  -RequireRunningStack
```

Expected:

- deployed release files exist;
- `.env.production` exists and does not contain placeholders;
- Linux deploy helpers exist and pass `bash -n` syntax validation;
- Caddy availability is reported before applying public routing;
- Docker and Docker Compose config work;
- server storage, factory storage and job directories exist;
- local health works when `-RequireRunningStack` is used;
- no secrets are written to the diagnostics report.

### Automated Remote Smoke

From `E:/Github/MathContentStudio/scan-math-db`, configure credentials outside
git:

```powershell
$env:SCAN_MATH_DB_REMOTE_BASE_URL = "https://nexumathjf.com"
$env:SCAN_MATH_DB_REMOTE_STUDIO_IDENTIFIER = "studio.admin"
$env:SCAN_MATH_DB_REMOTE_STUDIO_PASSWORD = "<secret>"
```

Run a read-only smoke first:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_remote_studio_factory.ps1
```

When a safe test instance exists on the server, run the full remote smoke:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_remote_studio_factory.ps1 -RequireOfficialReady -AllowJobWrite -AllowWordGeneration
```

Expected:

- `/health`, login, bootstrap, library, instance and snapshot are reachable.
- Optional job creation survives as a server-side job.
- Optional Word generation returns a valid `.docx` Office ZIP.
- The JSON report is written under `storage/diagnostics`.
- No response leaks tokens, local Windows paths, server private paths or Python
  tracebacks.

### Automated Backup And Rollback Readiness

Before declaring the server as source of truth, run a non-strict readiness check:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_factory_cutover_readiness.ps1
```

For a real cutover, run strict mode with the migration evidence:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_factory_cutover_packet.ps1 `
  -BundleManifestPath "storage/math_bank_bundle_release/manifests/manifest.json" `
  -ReleaseSummaryPath "storage/releases/nexumath_studio_smoke.json" `
  -BackupRef "<backup-file-or-server-ref>" `
  -TargetBooks <post-restore-books> `
  -TargetInstances <post-restore-instances> `
  -TargetProblems <post-restore-problems> `
  -TargetAssets <post-restore-assets> `
  -ValidationStatus passed

powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_factory_cutover_readiness.ps1 `
  -Strict `
  -BackupRef "<backup-file-or-server-ref>" `
  -MigrationManifestPath "storage/cutover/<batch-id>/migration-manifest.json" `
  -RollbackPlanPath "storage/cutover/<batch-id>/rollback-plan.md"
```

Expected:

- Backup, restore and export scripts exist.
- Migration manifest includes source, target, counts and rollback metadata.
- Strict mode requires target counts to exist and match source counts.
- Local Windows paths from bundle warnings are redacted before cutover evidence.
- Remote bootstrap cutover gates report backup, rollback and assets as verified.
- The report is written under `storage/diagnostics`.
- Strict mode fails if any required cutover evidence is missing.

## Migration Dry Run

Before server cutover:

```powershell
python E:\Github\MathContentStudio\scan-math-db\scripts\export_math_bank_bundle.py --output-dir E:\Github\MathContentStudio\scan-math-db\storage\math_bank_bundle_dry_run --force
```

Expected:

- Bundle manifest is generated.
- Counts are reported.
- Missing PDFs/covers are reported.
- No production database is modified.

Validate the generated migration bundle before upload/restore:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\Github\MathContentStudio\scan-math-db\scripts\test_math_bank_bundle_readiness.ps1 `
  -BundleRoot E:\Github\MathContentStudio\scan-math-db\storage\math_bank_bundle_release `
  -RequireAssets
```

Expected:

- Manifest parses successfully.
- `problemas`, `libros_escaneo` and `libro_instancias_escaneo` counts are present.
- PDFs and covers referenced by the manifest exist in the bundle.
- Server asset paths are portable and rooted under `/srv/mathcontentstudio`.
- A diagnostics report is written under `storage\diagnostics`.

Upload the bundle through the SSH helper. First run dry-run so the local bundle
is validated and the remote commands are printed without modifying the server:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\Github\MathContentStudio\scan-math-db\scripts\deploy_math_bank_bundle.ps1 `
  -DeployEnvFile E:\Github\MathContentStudio\scan-math-db\.env.deploy.local `
  -BundleRoot E:\Github\MathContentStudio\scan-math-db\storage\math_bank_bundle_release `
  -RemoteDeployDir "/opt/nexumath/scan-math-db" `
  -RemoteReleaseDir "/opt/nexumath/releases" `
  -DryRun
```

Then upload and extract the real bundle:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\Github\MathContentStudio\scan-math-db\scripts\deploy_math_bank_bundle.ps1 `
  -DeployEnvFile E:\Github\MathContentStudio\scan-math-db\.env.deploy.local `
  -BundleRoot E:\Github\MathContentStudio\scan-math-db\storage\math_bank_bundle_release `
  -RemoteDeployDir "/opt/nexumath/scan-math-db" `
  -RemoteReleaseDir "/opt/nexumath/releases"
```

After the release and `storage/math_bank_bundle_release` exist on the server,
run the restore helper in dry-run mode first:

```bash
cd /opt/nexumath/scan-math-db
bash deploy/server/restore_math_bank_bundle_server.sh \
  --deploy-dir /opt/nexumath/scan-math-db \
  --bundle-dir /opt/nexumath/scan-math-db/storage/math_bank_bundle_release \
  --dry-run
```

The same dry-run can be invoked from Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\Github\MathContentStudio\scan-math-db\scripts\invoke_math_bank_server_restore.ps1 `
  -SshHost "<ssh-host-or-ip>" `
  -ServerUser "<ssh-user>" `
  -RemoteDeployDir "/opt/nexumath/scan-math-db" `
  -StorageRoot "/srv/mathcontentstudio" `
  -RestoreDryRun
```

Expected:

- Docker Compose, `.env.production`, bundle manifest and restore script are
  found.
- Commands are printed without exposing database passwords.
- No database or asset directory is modified.

When the dry-run is correct, execute the real restore:

```bash
cd /opt/nexumath/scan-math-db
bash deploy/server/restore_math_bank_bundle_server.sh \
  --deploy-dir /opt/nexumath/scan-math-db \
  --bundle-dir /opt/nexumath/scan-math-db/storage/math_bank_bundle_release
```

Or run the same restore from Windows:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File E:\Github\MathContentStudio\scan-math-db\scripts\invoke_math_bank_server_restore.ps1 `
  -SshHost "<ssh-host-or-ip>" `
  -ServerUser "<ssh-user>" `
  -RemoteDeployDir "/opt/nexumath/scan-math-db" `
  -StorageRoot "/srv/mathcontentstudio"
```

Expected:

- A pre-restore PostgreSQL backup is written under
  `/srv/mathcontentstudio/backups`.
- Bundle tables are restored into PostgreSQL through the Compose network.
- PDFs and covers are copied under `/srv/mathcontentstudio/library`.
- `libros_escaneo` asset paths are rewritten to server-safe paths.
- A restore report is written under `/srv/mathcontentstudio/migration_reports`.

## Safety Checklist

- PostgreSQL is not public on the Internet.
- Secrets are environment variables only.
- Public responses hide local paths and server secrets.
- Backup exists before restore/cutover.
- Rollback plan is documented.
- Local PC write mode is disabled or clearly marked after cutover.

## Local Fallback And Cutover Gate Smoke

Run this before declaring the server as source of truth:

1. Start Studio Factory with default settings.
2. Open `/studio/factory/bootstrap`.
3. Confirm `cutover.mode` is `validation`.
4. Confirm `cutover.server_is_source_of_truth` is `false`.
5. Confirm `cutover.local_pc_role` is `backup_only` or `read_only`.
6. Confirm the UI shows the local PC as backup/fallback, not as an official writer.
7. Set only after real verification:
   - `SCAN_MATH_DB_FACTORY_BACKUP_VERIFIED=true`;
   - `SCAN_MATH_DB_FACTORY_ROLLBACK_VERIFIED=true`;
   - `SCAN_MATH_DB_FACTORY_ASSETS_MIGRATED=true`.
8. Confirm `cutover.can_declare_official` becomes `true`.
9. Run the remote smoke test and only then set:
   - `SCAN_MATH_DB_FACTORY_OFFICIAL_SOURCE=true`.
10. If `SCAN_MATH_DB_FACTORY_LOCAL_WRITE_MODE` is `sync` or
    `emergency_write`, confirm the UI shows the double-write warning.

Expected:

- The server cannot be treated as official by accident.
- The local PC role is visible in the UI.
- A sync/emergency local write mode is explicit and reportable.
- Backup and rollback checks are visible before cutover.

## Completion Criteria

The feature is ready for task generation when:

- Spec, plan, research, data model, contracts, and quickstart are complete.
- Current Studio and Biblioteca/Fabrica boundaries are understood.
- Migration and rollback gates are explicit.
- Initial implementation can be split into independent tasks.
