# CodeSense — Sprint 1 README

A complete guide to the project structure, architecture, and testing procedure for Sprint 1.

---

# CodeSense

**A Semantic Understanding and Onboarding Platform for Python Codebases**

Final Year Project · Department of Data Science · University of the Punjab

**Team:** Syed Farhan Ali Shah · Muhammad Rizwan · Muqnit-ur-Rehman · Muhammad Shahzad Shafiq
**Advisor:** Dr. Muhammad Ali

---

## Table of Contents

1. [What Is CodeSense?](#1-what-is-codesense)
2. [Sprint 1 Scope](#2-sprint-1-scope)
3. [Project Structure](#3-project-structure)
4. [How the Pipeline Works](#4-how-the-pipeline-works)
5. [How the Query Flow Works](#5-how-the-query-flow-works)
6. [Prerequisites](#6-prerequisites)
7. [Installation](#7-installation)
8. [Configuration](#8-configuration)
9. [Running the Application](#9-running-the-application)
10. [Testing Sprint 1 End-to-End](#10-testing-sprint-1-end-to-end)
11. [API Reference](#11-api-reference)
12. [What Works and What Does Not](#12-what-works-and-what-does-not)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. What Is CodeSense?

CodeSense is a web-based tool that lets a developer connect a public GitHub repository and ask natural language questions about its code. Every answer is paired with a citation to a specific file and line range so the answer can be verified.

The problem it solves:

- Understanding an unfamiliar codebase takes weeks.
- Traditional search returns strings, not answers.
- Existing AI code tools are built for writing code, not for understanding it.

CodeSense is built for the person whose task is **understanding** a codebase, not writing it.

---

## 2. Sprint 1 Scope

Sprint 1 delivers a **vertical slice** — the smallest version of the system that works end-to-end.

| Delivered in Sprint 1                           | Deferred to Later Sprints                    |
| :---------------------------------------------- | :------------------------------------------- |
| Connect a public GitHub repository              | Private repositories (Sprint 3)              |
| Clone, parse, chunk, embed, and graph the code  | Postgres-backed vector storage (Sprint 2)    |
| Store chunks, embeddings, and the graph on disk | Code-specialized embedding models (Sprint 2) |
| Ask a natural language question                 | LLM-generated answers (Sprint 4)             |
| Receive an answer with file:line citations      | Citation-pairing check (Sprint 4)            |
| Click a citation to view the source             | Streaming responses (Sprint 4)               |
| View all connected repositories                 | Authentication (Sprint 3)                    |
| Poll indexing progress                          | Background re-indexing (Sprint 6)            |

The goal of Sprint 1 is to prove that the entire pipeline can be wired together and demonstrated on real repositories. Everything else builds on this foundation.

**Prototype I acceptance criteria (from D2):**
The end-to-end flow works reliably on at least three sample Python repositories.

---

## 3. Project Structure

```
Sprint 1/
│
├── backend/                        FastAPI application and indexing logic
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                 FastAPI app factory, router registration,
│   │   │                           static file mount, CORS middleware
│   │   ├── config.py               Settings loaded from .env (DB URL, CORS
│   │   │                           origins, Groq API key)
│   │   ├── database.py             Async SQLAlchemy engine, session factory,
│   │   │                           and Base class for ORM models
│   │   │
│   │   ├── models/
│   │   │   └── repository.py       SQLAlchemy model for the repositories table
│   │   │
│   │   ├── routers/                HTTP endpoints grouped by concern
│   │   │   ├── health.py           GET /health
│   │   │   ├── repositories.py     GET/POST /api/repositories,
│   │   │   │                       GET /api/repositories/{id}/status
│   │   │   ├── query.py            POST /api/query — the semantic search endpoint
│   │   │   ├── file.py             GET /api/file — returns source lines for the
│   │   │   │                       citation preview panel
│   │   │   └── langgraph_test.py   GET /langgraph-test — Sprint 0 smoke test
│   │   │
│   │   └── services/
│   │       └── repository_service.py
│   │                               Business logic: create a repo record,
│   │                               run the indexing pipeline as a background
│   │                               task, update progress in the DB
│   │
│   ├── indexing/
│   │   ├── __init__.py
│   │   ├── pipeline.py             The core indexing library (refactored from
│   │   │                           indexing_spike_v3.py). Clones, parses,
│   │   │                           chunks, embeds, and builds the graph
│   │   └── graph_example.py        Sprint 0 LangGraph two-agent example
│   │
│   ├── requirements.txt            Python dependencies
│   ├── .env                        Environment variables (DB URL, API keys)
│   └── venv/                       Virtual environment (not committed)
│
├── frontend/                       The web UI (HTML/CSS/vanilla JS)
│   ├── index.html                  The main page: sidebar, tab bar, panels,
│   │                               chat input, source preview pane
│   ├── index.css                   All styling (dark theme, three-column layout)
│   └── script.js                   All JavaScript (fetch calls, rendering,
│                                   tab switching, polling, message handling)
│
├── data/                           All runtime data (not committed to git)
│   ├── repos/                      Cloned GitHub repositories, one folder per
│   │   └── {repo_id}/              repository, named by its UUID
│   │
│   └── indexes/                    Processed artifacts for each repository
│       └── {repo_id}/
│           ├── chunks.json         Chunk metadata only (no code text)
│           ├── chunks_full.json    Chunk metadata + full code text
│           ├── embeddings.npz      Compressed NumPy matrix of embeddings
│           ├── graph.graphml       NetworkX graph in GraphML format
│           └── metadata.json       Repo name, chunk count, model name, etc.
│
└── docs/                           Sprint plans, demo scripts, architecture docs
    └── demo_prototype_1.md         Script for the Prototype I demonstration
```

### Why the Structure Is Organized This Way

- **`backend/app/`** — everything the HTTP server needs. Routers stay thin; services hold business logic.
- **`backend/indexing/`** — the pipeline is decoupled from FastAPI. It can be called from a script, a test, or a background task. This is the same code you tested in the spike.
- **`frontend/`** — served by FastAPI as static files. Kept simple (no build step) so Sprint 1 focuses on the pipeline, not the tooling.
- **`data/`** — never touched by source code, only by the running pipeline. Deleting it forces a full re-index.
- **`docs/`** — anything that is read by a human (sprint plans, demo scripts) lives here.

---

## 4. How the Pipeline Works

When you connect a repository, the backend runs a **six-stage pipeline** in the background:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          INDEXING PIPELINE                                  │
│                                                                             │
│   GitHub URL ──► Clone ──► Parse ──► Chunk ──► Embed ──► Graph ──► Store    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Stage 1 — Clone

The URL is validated, and `git clone` downloads the repository to `data/repos/{repo_id}/`. For public repositories, no authentication is needed. Private repositories will use a Personal Access Token in Sprint 3.

### Stage 2 — Parse

Every `.py` file is parsed with **Tree-sitter** into an Abstract Syntax Tree. The parser extracts every function and class along with its exact start line and end line. Files in `site-packages`, `venv`, and `node_modules` are skipped.

### Stage 3 — Chunk

The code is split into **chunks** — one chunk per function, one chunk per class, and one "module overview" chunk per file. Each chunk carries:

- its file path
- its start line and end line
- its type (`func`, `class`, or `module`)
- the code text

Chunking at function/class level (not at arbitrary character boundaries) is what allows the answer to point at a real, meaningful code block.

### Stage 4 — Embed

Each chunk is passed through an embedding model (`all-MiniLM-L6-v2` in Sprint 1 — a general-purpose model, upgraded in Sprint 2). The model returns a vector: a list of 384 numbers that captures the semantic meaning of the chunk. Similar code produces similar vectors.

### Stage 5 — Graph

A directed graph is built from the chunks. Nodes are functions and classes. Edges are added when one chunk's code mentions the name of another chunk's function or class. This produces an approximate call graph. The graph is stored as GraphML.

### Stage 6 — Store

Everything is written to disk under `data/indexes/{repo_id}/`. The repository's status in PostgreSQL is updated from `indexing` to `ready`.

The pipeline reports progress at each stage by updating the `progress` column in the `repositories` table. The frontend polls this and shows a live progress bar in the sidebar.

---

## 5. How the Query Flow Works

When the user types a question, the following happens:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             QUERY FLOW                                      │
│                                                                             │
│   Question ──► Embed ──► Cosine Similarity ──► Top-5 ──► Groq LLM           │
│                                                    ──► Answer + Citations   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

1. The question is converted into a vector using the same embedding model.
2. Cosine similarity is computed between the question vector and every chunk vector in the index.
3. The top 5 chunks are selected (configurable with `LLM_TOP_K`).
4. The question and the full code of those chunks are sent to a Groq LLM, which writes a Markdown-formatted answer grounded in the chunks and cites file paths and line ranges.
5. If the LLM is unavailable (no `GROQ_API_KEY`, network error, or Groq error), the endpoint falls back to a template answer that names the top chunk. The response field `answer_source` is `"llm"` or `"fallback"`.
6. The response includes the answer text and a list of citations with file paths and line ranges.

### What Happens When You Click a Citation

The frontend calls `GET /api/file` with the file path and line number. The backend reads the file from `data/repos/{repo_id}/`, extracts the relevant lines, and returns them. The right-hand panel renders them with the cited lines highlighted.

---

## 6. Prerequisites

| Requirement | Version            | Notes                             |
| :---------- | :----------------- | :-------------------------------- |
| Python      | 3.10 or higher     | 3.11 recommended                  |
| PostgreSQL  | 14 or higher       | With pgvector extension installed |
| Git         | Any recent version | Must be on PATH                   |
| Node.js     | Not required       | The frontend has no build step    |

---

## 7. Installation

### 7.1 Clone the Project Repository

```bash
git clone <your-team-repo-url> codesense
cd codesense
```

### 7.2 Create the Python Virtual Environment

```bash
cd backend
python -m venv venv

# On Windows:
venv\Scripts\activate

# On macOS/Linux:
source venv/bin/activate
```

### 7.3 Install Dependencies

```bash
pip install -r requirements.txt
```

### 7.4 Create the Database

Open a terminal as the PostgreSQL superuser:

```bash
psql -U postgres
```

```sql
CREATE DATABASE codesense_dev;
\c codesense_dev
CREATE EXTENSION IF NOT EXISTS vector;
\q
```

---

## 8. Configuration

Create `backend/.env` with the following contents:

```env
DEBUG=true
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/codesense_dev
GROQ_API_KEY=your_groq_api_key
# Optional (default shown):
# GROQ_MODEL=openai/gpt-oss-120b
```

Replace `YOUR_PASSWORD` with the password you set during PostgreSQL installation.

`GROQ_API_KEY` is used to generate answers with an LLM. Create a key in the Groq console and keep it only in your local `.env` (the file is gitignored; never commit it). If the key is empty, `/api/query` still works but returns the template fallback answer.

`GROQ_MODEL` defaults to `openai/gpt-oss-120b`. Groq retires models from time to time, so if answers suddenly fall back, check Groq's deprecation page and set `GROQ_MODEL` to a supported model.

---

## 9. Running the Application

### 9.1 Start the Backend

```bash
cd backend
# Make sure the venv is active
uvicorn app.main:app --reload --port 8000
```

You should see:

```
[CodeSense] Database tables ready.
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

**Leave this terminal open.** The server must stay running.

### 9.2 Open the Frontend

**Open the browser and go to:**

```
http://localhost:8000
```

Do not use Live Server or open the HTML file directly. The FastAPI server serves both the API and the frontend on the same port, which avoids all CORS issues.

---

## 10. Testing Sprint 1 End-to-End

This is the exact procedure for verifying the Sprint 1 deliverable.

### 10.1 Connect a Repository

**Option A — From the UI**

1. Type a GitHub URL into the URL bar at the top of the page. Example:
   ```
   https://github.com/pallets/flask
   ```
2. Click **Connect / Re-index**.
3. Watch the sidebar. A new card appears with the repository name.
4. The card's status updates through: `⏳ cloning`, `⏳ parsing`, `⏳ embedding`, `⏳ graph`, then `✓ Indexed · N chunks`.

**Option B — From the Terminal**

```bash
curl -X POST http://localhost:8000/api/repositories \
  -H "Content-Type: application/json" \
  -d '{"url": "https://github.com/pallets/flask"}'
```

Response:

```json
{
  "id": "42dd7fd7-aecd-495a-aa6d-780217aca7df",
  "name": "pallets/flask",
  "status": "pending",
  "message": "Indexing started."
}
```

Copy the `id` value — you will use it in the next steps.

### 10.2 Wait for Indexing to Complete

Poll the status endpoint using the UUID from the previous step:

```bash
curl http://localhost:8000/api/repositories/42dd7fd7-aecd-495a-aa6d-780217aca7df/status
```

Expected response progression:

```json
{"status":"indexing","stage":"cloning","progress":5,"error":null}
{"status":"indexing","stage":"parsing","progress":30,"error":null}
{"status":"indexing","stage":"embedding","progress":75,"error":null}
{"status":"indexing","stage":"graph","progress":90,"error":null}
{"status":"ready","stage":"complete","progress":100,"error":null}
```

**Expected indexing time:**

| Repository           | Size     | Time on CPU  |
| :------------------- | :------- | :----------- |
| `psf/requests`     | ~10k LOC | 2-4 minutes  |
| `pallets/flask`    | ~38k LOC | 5-10 minutes |
| `tiangolo/fastapi` | ~50k LOC | 8-15 minutes |

### 10.3 Ask a Question

```bash
curl -X POST http://localhost:8000/api/query \
  -H "Content-Type: application/json" \
  -d '{"repo_id": "42dd7fd7-aecd-495a-aa6d-780217aca7df", "question": "How are URL routes registered?"}'
```

Expected response:

```json
{
  "question": "How are URL routes registered?",
  "answer": "<Markdown answer written by the LLM, citing file paths and line ranges>",
  "answer_source": "llm",
  "citations": [
    {"file":"src/flask/sansio/scaffold.py","start_line":344,"end_line":373,"name":"route","score":0.57},
    ...
  ],
  "top_results": [...]
}
```

### 10.4 Test in the Browser

1. Open `http://localhost:8000`.
2. Click the repository card in the sidebar.
3. Type a question in the chat box:
   ```
   How are URL routes registered?
   ```
4. Press Enter.
5. The answer appears in the chat with clickable citation chips.
6. Click a citation. The right-hand panel opens showing the source file with the cited lines highlighted.

### 10.5 Verify on Three Repositories

Per the Sprint 1 acceptance criteria, verify the flow on **at least three repositories**:

| # | Repository           | URL                                     | Purpose                              |
| :- | :------------------- | :-------------------------------------- | :----------------------------------- |
| 1 | `psf/requests`     | `https://github.com/psf/requests`     | Small, fast index — good first test |
| 2 | `pallets/flask`    | `https://github.com/pallets/flask`    | Medium — the standard test case     |
| 3 | `tiangolo/fastapi` | `https://github.com/tiangolo/fastapi` | Large — stresses the pipeline       |

For each, record in `docs/prototype_1_notes.md`:

- Indexing time
- Chunk count
- Whether the query returned a citation
- Whether the cited file was plausible

### 10.6 Useful Test Queries

For `psf/requests`:

- *"Where is the Session class defined?"*
- *"How are query parameters encoded into the URL?"*
- *"Where is Basic Authentication applied to a request?"*

For `pallets/flask`:

- *"How are URL routes registered?"*
- *"Where does the request context get pushed?"*
- *"Explain the error handling middleware."*

For `tiangolo/fastapi`:

- *"How are dependencies resolved?"*
- *"Where is the request validation logic?"*
- *"How are background tasks scheduled?"*

---

## 11. API Reference

All endpoints are served by FastAPI. Interactive documentation is available at `http://localhost:8000/docs`.

| Method   | Endpoint                               | Purpose                                     |
| :------- | :------------------------------------- | :------------------------------------------ |
| `GET`  | `/health`                            | Health check                                |
| `GET`  | `/api/repositories`                  | List all connected repositories             |
| `POST` | `/api/repositories`                  | Connect a new repository                    |
| `GET`  | `/api/repositories/{repo_id}/status` | Get indexing status and progress            |
| `POST` | `/api/query`                         | Ask a question about an indexed repository  |
| `GET`  | `/api/file`                          | Fetch source lines for the citation preview |
| `GET`  | `/langgraph-test`                    | Sprint 0 LangGraph smoke test               |

### Request and Response Shapes

**`POST /api/repositories`**

```json
// Request
{ "url": "https://github.com/pallets/flask" }

// Response (202 Accepted)
{ "id": "uuid", "name": "pallets/flask", "status": "pending", "message": "Indexing started." }
```

**`POST /api/query`**

```json
// Request
{ "repo_id": "uuid", "question": "How are URL routes registered?" }

// Response (200 OK)
{
  "question": "How are URL routes registered?",
  "answer": "<LLM answer; template text if answer_source is fallback>",
  "answer_source": "llm",
  "citations": [
    { "file": "...", "start_line": 344, "end_line": 373, "name": "route", "score": 0.57 }
  ],
  "top_results": [...]
}
```

**`GET /api/file`**

```
GET /api/file?repo_id=<uuid>&path=src/flask/app.py&start=344&end=373
```

```json
{
  "path": "src/flask/app.py",
  "start": 344,
  "end": 373,
  "lines": [
    { "n": 344, "text": "def route(self, rule, **options):" },
    ...
  ]
}
```

---

## 12. What Works and What Does Not

### ✅ Working in Sprint 1

- Connecting public GitHub repositories via URL
- Background indexing without blocking the API
- Live progress reporting in the sidebar
- Chunking at function/class boundaries with exact line ranges
- Persisting chunks, embeddings, and the graph to disk
- Semantic retrieval over the indexed chunks
- Answers with clickable citations
- LLM-generated, formatted answers grounded in the retrieved chunks (with a template fallback)
- Citation preview that opens the source file at the exact line range
- Listing all connected repositories

### ⚠️ Working but Limited

- **Answer generation** uses a Groq LLM over the top 5 retrieved chunks, so an answer is only as good as retrieval: if the right code is not in those chunks, the answer will be incomplete or say so. Citations are not yet verified automatically (the citation-pairing check is planned for Sprint 4).
- **The graph** uses identifier matching, not real call resolution. It is directionally useful but not precise. Sprint 5 upgrades it.
- **The embedding model** is `all-MiniLM-L6-v2` (general-purpose). It works but is not tuned for code. Sprint 2 replaces it.
- **Storage** is files on disk. Sprint 2 moves it to PostgreSQL + pgvector.
- **No authentication.** Sprint 3 adds it.

### ❌ Not in Sprint 1

- Private repositories
- User accounts and sessions
- LLM-generated answers with the citation-pairing check
- Impact analysis
- Architecture diagram view
- Security scan
- Role-based onboarding
- Streaming responses
- Repository deletion

---

## 13. Troubleshooting

| Symptom                                               | Cause                                                                      | Fix                                                                     |
| :---------------------------------------------------- | :------------------------------------------------------------------------- | :---------------------------------------------------------------------- |
| `NameError: name 'app' is not defined` at startup   | `include_router` was called at module level, not inside `create_app()` | Move all`app.include_router(...)` calls inside `create_app()`       |
| Browser shows a blank page or unstyled HTML           | Opening the HTML file directly, or via Live Server on a different port     | Open`http://localhost:8000`, not `file://` or port 5500             |
| `Failed to fetch` in the browser console            | The FastAPI server is not running                                          | Start it with`uvicorn app.main:app --reload --port 8000`              |
| CORS error in the browser console                     | Frontend and backend are on different origins                              | Use`http://localhost:8000` — same origin for both                    |
| `Could not establish connection` in the console     | A browser extension, not CodeSense                                         | Ignore it, or test in an incognito window                               |
| `Repository not found` on `/status` or `/query` | Wrong`repo_id`                                                           | Run`GET /api/repositories` and copy the correct `id`                |
| `Repository is not ready to be queried`             | Indexing still running                                                     | Poll`/status` until `status` is `ready`                           |
| Indexing appears stuck at a stage                     | The pipeline crashed silently                                              | Check the backend terminal for`[pipeline]` errors                     |
| `git: command not found`                            | Git not on PATH                                                            | Install Git and restart the terminal                                    |
| Out-of-memory during embedding of a large repo        | `all-MiniLM-L6-v2` batches 32 chunks at a time                           | Reduce`batch_size` in `pipeline.py` or test on a smaller repository |

### Checking the Database Directly

```bash
psql -U postgres -d codesense_dev
```

```sql
SELECT id, name, status, stage, progress, chunk_count
FROM repositories
ORDER BY created_at DESC;
```

### Cleaning Up a Repository

To force a fresh index, delete the DB row and the folders:

```sql
DELETE FROM repositories WHERE id = '<repo_id>';
```

```bash
rmdir /s "data\repos\<repo_id>"
rmdir /s "data\indexes\<repo_id>"
```

Then connect it again from the UI.

---

## Sprint 1 Status

**Deliverable:** Prototype I — an end-to-end vertical slice that connects a public GitHub repository, indexes it, and answers natural language questions with file:line citations.

**Acceptance criteria:** The flow works on at least three Python repositories.

**Next sprint:** Sprint 2 — Indexing Deep-Dive and Quality Benchmarking. Sprint 2 will replace the current embedding model with a code-specialized one, add sparse BM25 retrieval, tune fusion weights, and produce Recall@k measurements against a hand-labelled evaluation set.

---

*End of README — Sprint 1*
