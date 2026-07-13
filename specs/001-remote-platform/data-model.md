# Data Model And Storage Design: Remote Platform

This document defines the production database, storage, path rewrite, backup,
and local mirror behavior for `001-remote-platform`.

## Production PostgreSQL Roles

PostgreSQL must run on the server or inside the private server network. It must
not be exposed directly to the public Internet.

Recommended production database names:

| Purpose | Database |
|---|---|
| Production source of truth | `mathcontentstudio_prod` |
| Restore validation / rehearsal | `mathcontentstudio_stage` |
| Local development mirror | `mathcontentstudio_local_mirror` |

Recommended roles:

| Role | Purpose | Permissions |
|---|---|---|
| `mcs_owner` | Schema owner and migration role | Owns schema, runs DDL only during controlled maintenance |
| `mcs_app` | Public API and Studio runtime | Read/write only through application tables and functions |
| `mcs_worker` | Background jobs: Word generation, imports, validation | Read/write to job, artifact, and content tables |
| `mcs_readonly` | Reports and diagnostics | Read-only access |
| `mcs_backup` | Backup automation | Read-only plus backup permissions |

Rules:

- Runtime services must not use `postgres` or `mcs_owner`.
- Passwords and connection strings must live in server secrets or environment
  variables.
- Server-side jobs must use a role with only the permissions required for their
  queue and artifact writes.

## Server Storage Root

All migrated assets must live under one stable server root:

```text
/srv/mathcontentstudio/
|-- library/
|   |-- books/
|   |   `-- <book_code>/
|   |       |-- covers/
|   |       |-- source/
|   |       |-- instances/
|   |       |   `-- <instance_code>/
|   |       |       |-- pdf/
|   |       |       |-- pages/
|   |       |       |-- crops/
|   |       |       |-- segments/
|   |       |       |-- staging/
|   |       |       `-- word/
|   |       `-- full_word/
|-- training/
|   |-- problem_detector/
|   |-- raw_ocr/
|   |-- figure_segmentation/
|   `-- normalizer/
|-- exports/
|-- imports/
|-- jobs/
|-- backups/
`-- logs/
```

Public URLs must not expose this filesystem structure directly. The API should
serve assets through controlled endpoints or signed/static routes.

## Stored Path Policy

Database records should prefer portable asset references over raw absolute
paths.

Recommended stored values:

| Data | Stored value |
|---|---|
| Book cover | `library/books/<book_code>/covers/cover.png` |
| Source PDF | `library/books/<book_code>/instances/<instance_code>/pdf/source.pdf` |
| Crop image | `library/books/<book_code>/instances/<instance_code>/crops/<crop_id>.png` |
| Segment image | `library/books/<book_code>/instances/<instance_code>/segments/<segment_id>.png` |
| Generated Word | `library/books/<book_code>/instances/<instance_code>/word/<instance_code>.docx` |
| Full book Word | `library/books/<book_code>/full_word/<book_code>__complete.docx` |

Absolute server paths may be used internally by workers after resolving them
against `/srv/mathcontentstudio`, but API responses should return portable
references or URLs.

## Windows-To-Server Rewrite Rules

The pre-migration audit found Windows/UNC path families that need explicit
mapping. The rewrite process must be deterministic and report every unmapped
path before any production cutover.

Initial rewrite families:

| Current family | Server family |
|---|---|
| `E:\Banco de Preguntas\...` | `/srv/mathcontentstudio/library/books/...` |
| `D:\Banco de Preguntas\...` | `/srv/mathcontentstudio/library/books/...` |
| `K:\Banco de Preguntas\...` | `/srv/mathcontentstudio/library/books/...` |
| `E:\Github\Auditor-IA\.cache\instance_factory\library_covers\...` | `/srv/mathcontentstudio/library/books/<book_code>/covers/...` |
| `E:\Github\MathContentStudio\scan-math-db\storage\...` | `/srv/mathcontentstudio/library/...` |

Rewrite requirements:

1. Normalize slashes and encoding before matching.
2. Resolve book and instance identity from existing library metadata when
   possible, not from path text alone.
3. Copy or verify the target file before replacing any stored reference.
4. Write a mapping manifest:
   `tmp/remote_migration_audit/path_rewrite_manifest.jsonl`.
5. Reject cutover if any required path remains unmapped.

## Backup And Rollback Procedure

Before production migration:

1. Stop or freeze writes from local tools.
2. Run the full audit without `--skip-file-checks`.
3. Create a local database dump.
4. Restore the dump into `mathcontentstudio_stage`.
5. Run row-count and path validation against stage.
6. Copy assets to `/srv/mathcontentstudio`.
7. Validate asset existence from server paths.
8. Only then promote `mathcontentstudio_prod`.

Minimum backup artifacts:

| Artifact | Location |
|---|---|
| PostgreSQL dump | `/srv/mathcontentstudio/backups/db/YYYY-MM-DD/` |
| Storage snapshot or archive | `/srv/mathcontentstudio/backups/storage/YYYY-MM-DD/` |
| Path rewrite manifest | `/srv/mathcontentstudio/backups/manifests/YYYY-MM-DD/` |
| Migration report | `/srv/mathcontentstudio/backups/reports/YYYY-MM-DD/` |

Rollback rule: if validation fails after cutover, point the API back to the last
known-good database and storage snapshot. Do not continue partial writes.

## Local Mirror Behavior

After the server becomes official:

- `mathcontentstudio_prod` is the source of truth.
- Local databases become mirrors, development copies, or offline workspaces.
- Local work that creates new content must either:
  - use server APIs/jobs directly; or
  - create an explicit import bundle that is reviewed and applied server-side.
- Local and server databases must not both accept independent writes for the
  same book/instance without a sync contract.

The local Factory can remain available as a heavy worker, but it must write
through controlled server jobs or import bundles once production cutover is
complete.
