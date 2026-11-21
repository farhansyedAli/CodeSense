# CodeSense — React frontend

React + Vite rewrite of the original `frontend/` (HTML/CSS/JS). It talks to the same
FastAPI backend and needs **no backend changes**.

## Run it

Two terminals:

```powershell
# 1) backend (from the repo root)
cd backend
venv\Scripts\activate
uvicorn app.main:app --reload --port 8000

# 2) frontend
cd frontend-react
npm install        # first time only
npm run dev
```

Open **http://localhost:5173**. Vite forwards every `/api/...` request to
`http://127.0.0.1:8000` (see `vite.config.js`), so there are no CORS issues.

`npm run build` produces a static build in `dist/`.

## Features

| View | What it does | Backend route |
| --- | --- | --- |
| Repositories (sidebar) | List, live indexing progress, connect, re-index | `GET/POST /api/repositories` |
| Ask the codebase | Chat with Markdown answers, clickable source citations, per-repo history | `POST /api/query` |
| Source panel | Highlighted source with the cited lines marked | `GET /api/file` |
| Architecture | Package / module / function graph, search, node details | `GET /api/graph/{id}` |
| Security | OSV.dev dependency scan with severity summary | `POST /api/security/analyze` |
| Impact analysis | Callers of a function/class up to 3 levels, built from the function graph | `GET /api/graph/{id}?level=function` |

Chat history, recent questions and the selected repo are kept in `localStorage`.

## Design

Chosen with the UI/UX Pro Max skill (`--design-system` for a developer tool):

- **Style:** Dark Mode (OLED) — graphite neutrals, no blue tint
- **Colours:** emerald `#34D399` for actions/focus, violet `#A78BFA` for AI, red/orange/amber/sky for severity
- **Type:** IBM Plex Sans (UI) + JetBrains Mono (code), bundled locally via `@fontsource`
- **Icons:** Lucide (no emoji icons)
- Visible keyboard focus, `prefers-reduced-motion` respected, responsive down to 375 px

All tokens are in `src/styles/tokens.css`.

## Structure

```
src/
  App.jsx                 layout, state, routing between views
  lib/api.js              every backend call
  lib/hooks.js            repository polling, localStorage state
  components/             Sidebar, ConnectDialog, SourcePanel, Markdown, States
  views/                  QAView, ArchitectureView, SecurityView, ImpactView
  styles/                 tokens.css, app.css, views.css
```
