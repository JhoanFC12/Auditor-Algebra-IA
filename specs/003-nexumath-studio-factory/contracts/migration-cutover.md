# Contract: Migration And Cutover

## Migration Batch Requirements

Every official migration batch must include:

- source database identifier;
- target database identifier;
- backup reference;
- asset manifest;
- path rewrite report;
- counts before and after;
- validation summary;
- rollback instructions.

## Migration Manifest Shape

```json
{
  "batch_id": "migration_2026_07_06",
  "source": {
    "database": "mathcontentstudio_local_mirror",
    "storage_root": "local"
  },
  "target": {
    "database": "mathcontentstudio",
    "storage_root": "/srv/mathcontentstudio"
  },
  "counts": {
    "books": {"source": 52, "target": 52},
    "instances": {"source": 518, "target": 518},
    "problems": {"source": 0, "target": 0},
    "assets": {"source": 0, "target": 0}
  },
  "path_rewrites": {
    "rewritten": 0,
    "local_only_remaining": 0,
    "missing_files": 0
  },
  "validation": {
    "status": "pending",
    "errors": []
  },
  "rollback": {
    "available": true,
    "backup_ref": "/srv/mathcontentstudio/backups/..."
  }
}
```

## Cutover Gates

The server can become official only when:

1. backup exists and restore has been tested;
2. database counts match expected values;
3. required PDFs and covers are present in server storage;
4. public API responses do not expose private local paths;
5. Studio library opens from the domain;
6. one instance smoke test succeeds through review-ready staging;
7. one Word generation smoke test succeeds;
8. rollback instructions are documented.

## Rollback Contract

Rollback must specify:

- previous deployment version or route switch;
- database backup to restore;
- storage snapshot or asset bundle to restore;
- local mirror state;
- known writes to freeze or replay.

## Local PC Contract

After cutover:

- local PC may sync from server;
- local PC may run backups or emergency tooling;
- local PC must not silently write official records unless an explicit sync or recovery mode is enabled.
