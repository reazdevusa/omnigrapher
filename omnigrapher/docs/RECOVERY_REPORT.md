# OmniGrapher Ecosystem Recovery Report

**Date:** 2026-08-15

**Workspace:** `D:\Upwork\ai_knowledge_base_suite`

**GitHub:** `https://github.com/reazdevusa/omnigrapher`

---

## 1. What Was Diagnosed

### Workspace
- Active workspace: `D:\Upwork\ai_knowledge_base_suite`
- `D:\Work\ai_knowledge_base_suite` did not exist
- The project has a FastAPI backend, Next.js web app, Streamlit app, PyQt5 desktop app, Docker, and Terraform infrastructure
- No `.git` repository existed in the workspace

### Ollama
- `ollama` command not found in PATH
- `C:\Users\alfa_\AppData\Local\Programs\Ollama` was missing
- `C:\Users\alfa_\.ollama` existed but `models` and `cache` were empty
- No Ollama executable or models were found on any searched drive

### Devin
- `C:\Users\alfa_\.devin` exists but only contains VS Code/Windsurf extension data
- `D:\Upwork\ai_knowledge_base_suite\.devin` existed only with `agents.md`
- `D:\Upwork\ai_knowledge_base_suite\.devin\sessions` was missing
- No local Devin CLI executable; Devin is an Electron app at `C:\Users\alfa_\AppData\Local\Programs\Devin`

### Git
- Git was installed and on PATH
- No `origin` remote configured
- Git provider in Devin Cloud was disconnected (cloud-side issue)

---

## 2. What Was Recovered

- **Ollama:** downloaded the Windows portable zip, extracted to `C:\Users\alfa_\AppData\Local\Programs\Ollama`, restored PATH, set `OLLAMA_MODELS` to `D:\.ollama\models`, and preserved `.ollama` identity keys
- **Models:** pulled `llama3.2` (2.0 GB) and `nomic-embed-text` (274 MB)
- **Server:** `ollama serve` is running on `localhost:11434` and responded to a generate test
- **Git repo:** initialised and linked to `https://github.com/reazdevusa/omnigrapher`
- **Devin local metadata:** `.devin/workspace.json`, `.devin/sessions` landing zone, and `omnigrapher/.devin/metadata/workspace.json` were created

---

## 3. What Was Rebuilt

### OmniGrapher Structure
Created under `D:\Upwork\ai_knowledge_base_suite\omnigrapher\`:

```text
omnigrapher/
├── .devin/metadata/
├── agents/personas/
├── assets/logo/
├── config/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── BRANDING.md
│   ├── devin_reconnect.md
│   ├── RECOVERY_REPORT.md
│   └── ui/
├── scripts/
│   ├── backup/
│   ├── devin/
│   ├── diagnostics/
│   └── ollama/
└── services/
```

### Documentation
- Root `README.md` rewritten as the OmniGrapher project hub
- `ARCHITECTURE.md` with high-level, data, app, and infra flows
- `BRANDING.md` with palette, typography, voice, and usage rules
- `UI_MOCKUPS.md` and `mockups.html` with five interactive screens
- `devin_reconnect.md` with manual reconnection steps

### Identity Assets
- `assets/logo/graph_pulse.svg`
- `assets/logo/omni_eye.svg`
- `assets/logo/infinite_graph.svg`
- `assets/logo/favicon.svg`

### Agent Personas
- `agents/personas/INDEXER.md`
- `agents/personas/REASONER.md`
- `agents/personas/SUMMARIZER.md`
- `agents/personas/ORCHESTRATOR.md`

### Backup System
- `config/backup.yaml` — human-readable policy
- `config/backup.json` — machine-readable policy
- `scripts/backup/run-backup.ps1` — multi-layer backup engine
- `scripts/backup/consistency-check.ps1`
- `scripts/backup/corruption-check.ps1`
- `scripts/backup/restore.ps1`
- `scripts/backup/safe-location-validator.ps1`
- `scripts/backup/register-scheduled-task.ps1`
- `scripts/backup/README.md`
- Windows scheduled task `OmniGrapher-Backup` registered for every 6 hours

### Ollama / Devin Scripts
- `scripts/ollama/repair-ollama.ps1`
- `scripts/devin/rebuild-devin-sessions.ps1`
- `scripts/diagnostics/run-diagnostic.ps1`

---

## 4. What Protections Were Added

| Protection | How |
|---|---|
| **Git remote pinned** | `origin` → `reazdevusa/omnigrapher` |
| **.gitignore** | Excludes `.env`, `.devin/sessions`, `.ollama/models`, `chroma_db`, `embeddings`, backups, venvs, node_modules |
| **Ollama safe storage** | `OLLAMA_MODELS` set to `D:\.ollama\models` (not `C:`) |
| **External HDD backups** | `G:\DO_NOT_DELETE\OmniGrapher_Backups` dedicated folder created |
| **Scheduled backups** | Windows Task Scheduler runs `run-backup.ps1` every 6 hours |
| **Integrity checks** | SHA256 manifests + consistency + corruption scripts |
| **Safe-location validator** | Prevents writing backups into installation or software source folders |
| **Workspace metadata** | `.devin/workspace.json` and `omnigrapher/.devin/metadata/workspace.json` pin OmniGrapher as the canonical project |

---

## 5. Validation Results

| Check | Status |
|---|---|
| `ollama --version` | 0.32.13 |
| `ollama list` | `llama3.2`, `nomic-embed-text` |
| `ollama serve` API | Running, generated a test response |
| Git repo + remote | Ready (`origin` set) |
| OmniGrapher folder structure | Created |
| README, architecture, branding | Generated and committed |
| Logos + UI mockups | Created |
| Agent personas | Created |
| Backup scripts | Created; scheduled task registered |
| Safe-location validator | Pass |

---

## 6. What I Should Avoid Doing in the Future

1. **Never move `C:\Users\<user>\.*` directories without updating the associated app configs first.** Ollama, Devin, and other tools rely on these paths.
2. **Do not put backups inside `G:\DO_NOT_DELETE\ALL_SOFTWARE_INSTALLATION_SOURCES`.** Use the dedicated `G:\DO_NOT_DELETE\OmniGrapher_Backups` folder.
3. **Do not copy Ollama model weights into the Git repo.** They are in `.ollama` and `.gitignore` excludes them, but never stage them manually.
4. **Do not move the workspace without updating `.devin/workspace.json`, `omnigrapher/.devin/metadata/workspace.json`, and the Devin Cloud workspace path.**
5. **Do not delete `D:\.ollama\models` without first running a backup.** That folder now contains all model weights.
6. **Do not run `restore.ps1` without `-Confirm`.** It overwrites live files.
7. **Avoid running `git push` until the GitHub account is authenticated.** The remote is configured but no push was performed.

---

## 7. Remaining Manual Steps

1. **Devin Cloud Git provider:** Open the Devin/Windsurf UI, authorize GitHub for `reazdevusa`, and select `reazdevusa/omnigrapher`.
2. **GitHub push:** When ready, run `git push -u origin main` after authenticating.
3. **First full backup:** Run `omnigrapher\scripts\backup\run-backup.ps1 -NoGit` to seed local and external layers (this will copy ~2.5 GB + chroma_db; run when you have time).
4. **GCP / Cloudflare credentials:** If deploying, fill in `infrastructure/terraform-gcp/terraform.tfvars` from the example.

---

*OmniGrapher is now the canonical home for all future AI, graph, agent, web, and infrastructure work.*
