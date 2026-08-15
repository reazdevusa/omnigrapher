# How to Run the AI Knowledge Base Suite

This guide explains how to start the entire AI Knowledge Base Suite — desktop app, web apps (Next.js and Streamlit), and all backend services.

---

## Prerequisites

- Windows 10/11
- Python 3.11 (with the project `.venv` already created)
- Node.js + npm (for the Next.js frontend)
- Ollama installed and available in your system `PATH`
- Docker Desktop (optional — only if you want to use PostgreSQL instead of SQLite)

The project is located at `D:\Upwork\ai_knowledge_base_suite`.

---

## Option 1: One-Click Start (Recommended)

The easiest way to run everything is to double-click the batch launchers in the project root:

| Action | File |
|--------|------|
| Start all services in one Windows Terminal window | `Start Knowledge Base Suite.cmd` |
| Stop all services | `Stop Knowledge Base Suite.cmd` |

`Start Knowledge Base Suite.cmd` opens a single Windows Terminal window with 4 tabs:

- **Ollama**
- **Backend** (`http://localhost:8001`)
- **Frontend** (`http://localhost:3002`)
- **Watchdog**

It then opens `http://localhost:3002` in your browser.

If Windows Terminal is not installed, the launcher falls back to opening separate PowerShell windows.

To use different ports:

```powershell
scripts\start-all-services-tabs.ps1 -BackendPort 8002 -FrontendPort 3003
```

To include the desktop app too:

```powershell
scripts\start-all-services-tabs.ps1 -Desktop
```

For the legacy separate-window launcher:

```powershell
scripts\start-all-services.ps1
```

---

## Option 2: Manual Start

If you prefer to start components individually, follow the sections below.

### 1. Start Ollama

Make sure Ollama is running. If it is not, open a PowerShell window and run:

```powershell
ollama serve
```

Then pull the models if you have not already:

```powershell
ollama pull nomic-embed-text
ollama pull llama3
```

### 2. Start the FastAPI Backend

Open a new PowerShell window (do not close the Ollama window):

```powershell
cd D:\Upwork\ai_knowledge_base_suite\knowledge_base_pilot
..\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8001 --reload
```

The backend will be available at `http://localhost:8001`.

**Database note:** The backend currently uses SQLite (`kb.db`) by default. If Docker Desktop is running and you want to use PostgreSQL, edit `knowledge_base_pilot\app\database.py` and `knowledge_base_pilot\.env` to uncomment the PostgreSQL `DATABASE_URL`.

### 3. Start the Next.js Web Frontend

Open another PowerShell window:

```powershell
cd D:\Upwork\ai_knowledge_base_suite\web_app_nextjs
npm install      # only needed the first time or after package changes
npm run dev -- --port 3002
```

Open `http://localhost:3002` in your browser.

### 4. Start the Streamlit Web Frontend

Open another PowerShell window:

```powershell
cd D:\Upwork\ai_knowledge_base_suite\web_app
..\.venv\Scripts\python.exe -m streamlit run app.py
```

Streamlit will print a local URL (usually `http://localhost:8501`). Open it in your browser.

### 5. Start the Desktop App

Open another PowerShell window:

```powershell
cd D:\Upwork\ai_knowledge_base_suite\desktop_app
..\.venv\Scripts\python.exe -m src.main
```

Or, if the project has a `main.py` directly in `desktop_app`:

```powershell
cd D:\Upwork\ai_knowledge_base_suite\desktop_app
..\.venv\Scripts\python.exe src/main.py
```

---

## Stopping Everything

### One-click stop

Double-click:

```
Stop Knowledge Base Suite.cmd
```

Or run in PowerShell:

```powershell
scripts\stop-all-services.ps1
```

### Manual stop

- Close each terminal window where you started a service.
- Or use `Ctrl + C` in each terminal to stop the process.

---

## Default URLs

| Service | URL |
|---------|-----|
| Next.js web app | `http://localhost:3002` |
| Streamlit web app | `http://localhost:8501` |
| FastAPI backend | `http://localhost:8001` |
| Backend health check | `http://localhost:8001/` |
| Ollama | `http://localhost:11434` |
| PostgreSQL (Docker) | `localhost:5433` (optional) |

---

## Common Issues

### "Backend is unreachable at http://localhost:8001"

- Make sure the backend is running.
- If you changed the backend port, update `web_app_nextjs\.env.local` and restart the Next.js dev server.
- Make sure no other app is using port `8001` or `3002`.

### "User not found" or 401 errors

- Register a new account in the web app if the SQLite database has been reset.
- The default database is SQLite, so users are stored in `knowledge_base_pilot\kb.db`.

### Docker / PostgreSQL errors

- Docker Desktop is not required to run the app. SQLite works out of the box.
- If you want PostgreSQL, start Docker Desktop first, then start the `awap-postgres` container.
- Then update `DATABASE_URL` in `knowledge_base_pilot\.env` or `app\database.py` to use PostgreSQL.

### Port already in use

- Backend port conflict: use `--port 8003` (or another free port).
- Next.js port conflict: use `--port 3003`.
- Streamlit port conflict: use `--server.port 8502`.

---

## Environment Variables

Key files:

- `knowledge_base_pilot\.env` — backend settings (database, Ollama, JWT secret, CORS)
- `web_app_nextjs\.env.local` — backend URL for the Next.js app
- `web_app\.env` — backend URL for the Streamlit app
- `desktop_app\.env` — backend URL for the desktop app

Copy `.env.example` to `.env` (or `.env.local` for Next.js) and adjust values as needed.
