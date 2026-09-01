# OmniGrapher Multi-Layer Backup System

## Files

| Script | Purpose |
|---|---|
| `run-backup.ps1` | Runs local, external, and Git backup layers |
| `consistency-check.ps1` | Compares source and backup file hashes |
| `corruption-check.ps1` | Scans a backup for zero-byte or hash-mismatched files |
| `restore.ps1` | Restores a backup over the live workspace |
| `safe-location-validator.ps1` | Ensures backups are not in forbidden directories |
| `register-scheduled-task.ps1` | Registers the backup with Windows Task Scheduler |

## Configuration

- Human-readable: `omnigrapher/config/backup.yaml`
- Machine-readable (active): `omnigrapher/config/backup.json`

## Run a backup

```powershell
omnigrapher\scripts\backup\run-backup.ps1
```

To skip the external HDD layer:

```powershell
omnigrapher\scripts\backup\run-backup.ps1 -NoExternal
```

## Register automatic backups

```powershell
omnigrapher\scripts\backup\register-scheduled-task.ps1 -IntervalHours 6
```

## Validate a backup

```powershell
omnigrapher\scripts\backup\consistency-check.ps1 -Backup "omnigrapher\backups\local\<timestamp>"
omnigrapher\scripts\backup\corruption-check.ps1 -Backup "omnigrapher\backups\local\<timestamp>"
```

## Restore

```powershell
omnigrapher\scripts\backup\restore.ps1 -Backup "omnigrapher\backups\local\<timestamp>" -Confirm
```

## Recovery guarantees

Every local run creates and validates:

- A transaction-consistent SQLite snapshot
- A PostgreSQL custom-format logical dump
- A PostgreSQL physical base backup
- A copy of continuously archived PostgreSQL WAL files
- A Redis RDB snapshot
- A Chroma data archive
- Uploaded source documents and extracted files
- SHA-256 manifests for copied files

PostgreSQL archives completed WAL segments continuously with a five-second archive timeout. This supports recovery to a chosen transaction time after the latest physical base backup. Guaranteed zero-data-loss recovery still requires synchronous replication to storage on another machine.

## Automatic backups

Run PowerShell as the intended service user:

```powershell
omnigrapher\scripts\backup\register-scheduled-task.ps1 -IntervalHours 1
```

WAL archiving runs continuously while PostgreSQL is running; the scheduled task creates hourly recovery baselines and logical dumps.

## Notes

- `.ollama/models` and `chroma_db` are backed up but excluded from Git.
- Never place backups inside `G:\DO_NOT_DELETE\ALL_SOFTWARE_INSTALLATION_SOURCES`.
- The scheduled task runs as the current user. Adjust the trigger if needed.
- Keep at least one verified backup on another physical device; local backups do not protect against host-drive failure.
