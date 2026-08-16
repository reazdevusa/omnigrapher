# AI Knowledge Base — Next.js Frontend

A production-grade, Next.js-based frontend for the AI Knowledge Base backend. It replaces the Streamlit UI with a fast, responsive React app that handles chat, document management, and admin features without the full-page blur-and-wait effect.

## Features

- **Modern React + Next.js 15** App Router
- **Tailwind CSS** styling with custom primary color
- **JWT authentication** with automatic token refresh
- **Streaming chat** via Server-Sent Events (no page reload)
- **Document management** in the sidebar: upload, sync, rebuild, rename, delete, preview
- **Chat sessions** stored in localStorage
- **Admin panel** for users, health, widget config, and feedback
- **Profile page** with display name, email, and password change
- **Backend proxy** — Next.js rewrites `/api/*` to the FastAPI backend

## Prerequisites

- Node.js 18+
- The FastAPI backend running on `http://127.0.0.1:8001` (or update `.env`)
- Backend CORS configured to allow `http://localhost:3000` (or set `CORS_ORIGINS=*` in backend `.env`)

## Getting Started

1. Copy the environment file and edit if needed:
   ```bash
   cp .env.example .env.local
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Run the development server:
   ```bash
   npm run dev
   ```

4. Open [http://localhost:3000](http://localhost:3000) in your browser.

## Build for Production

```bash
npm run build
npm start
```

## Project Structure

```
app/                  # Next.js app routes
  page.tsx            # Main chat page
  library/page.tsx    # Document library
  projects/page.tsx   # Saved projects
  profile/page.tsx    # User profile
  admin/page.tsx      # Admin panel
  more/page.tsx       # Navigation hub
  documents/[filename]/page.tsx  # Document preview + chunks
components/
  auth-provider.tsx   # Authentication context
  sidebar.tsx         # Sidebar navigation, sessions, documents
  chat-interface.tsx  # Streaming chat UI
  login-dialog.tsx    # Login / register modal
  ui/                 # Reusable UI primitives
lib/
  api.ts              # Backend API client
  sessions.ts         # Chat session helpers
```

## Configuration

Set `INTERNAL_API_URL` in `.env.local` to point to your FastAPI backend. The default is `http://127.0.0.1:8001`.

## Notes

- The frontend proxies `/api/*` requests to the backend, so the browser only needs to talk to the Next.js dev server.
- Chat sessions are saved to the browser's localStorage and can be renamed or deleted from the sidebar.
