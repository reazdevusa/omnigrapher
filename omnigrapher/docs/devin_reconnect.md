# Devin + Git Provider Reconnection Steps

## What was found
- `C:\Users\alfa_\.devin` exists but only contains VS Code / Windsurf extension data, not Devin chat sessions.
- The `D:\Upwork\ai_knowledge_base_suite\.devin` folder had only `agents.md`; the `sessions` subfolder was missing.
- Git is installed but the workspace had no `.git` repository or remote.

## What was rebuilt
1. Local `.devin/sessions` landing zone.
2. `.devin/workspace.json` pinning `OmniGrapher` as the canonical project.
3. `omnigrapher/.devin/metadata/workspace.json` as a backup copy.
4. Git repository initialised with `origin` pointing to `https://github.com/reazdevusa/omnigrapher`.

## What cannot be auto-recovered
Devin chat history is stored in **Devin Cloud**, not on the local file system. The local session metadata cannot be recreated from the cloud without re-linking the Git provider in the Devin UI.

## Manual steps to reconnect
1. Open the Devin desktop / Windsurf application.
2. Go to **Settings → Git / Repository / Cloud**.
3. Choose **GitHub** and authorize the `reazdevusa` account.
4. Select the repository `https://github.com/reazdevusa/omnigrapher`.
5. Set the **Workspace Path** to `D:\Upwork\ai_knowledge_base_suite`.
6. Save the configuration and restart the Devin extension/window.

## Avoid in the future
- Do not move the entire `C:\Users\<user>\AppData\Local\Programs\` or `.devin` directories without updating the workspace path in Devin first.
- Keep the `omnigrapher/scripts/backup/run-backup.ps1` schedule active so session metadata exports are captured.
