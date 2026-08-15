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

## Notes

- `.ollama/models` and `chroma_db` are backed up but excluded from Git.
- Never place backups inside `G:\DO_NOT_DELETE\ALL_SOFTWARE_INSTALLATION_SOURCES`.
- The scheduled task runs as the current user. Adjust the trigger if needed.
