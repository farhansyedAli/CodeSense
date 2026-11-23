// All calls to the CodeSense FastAPI backend live here.
// In development Vite proxies /api to http://127.0.0.1:8000 (see vite.config.js).
// Set VITE_API_BASE to point at a different server when building for production.
const API_BASE = import.meta.env.VITE_API_BASE || "";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
  } catch {
    throw new ApiError(
      "Could not reach the CodeSense server. Is the backend running on port 8000?",
      0
    );
  }

  let body = null;
  try {
    body = await res.json();
  } catch {
    /* response was not JSON */
  }

  if (!res.ok) {
    const detail =
      body && typeof body.detail === "string"
        ? body.detail
        : `Request failed (HTTP ${res.status}).`;
    throw new ApiError(detail, res.status);
  }
  return body;
}

export const api = {
  listRepositories: async () => (await request("/api/repositories"))?.data ?? [],

  connectRepository: (url) =>
    request("/api/repositories", { method: "POST", body: JSON.stringify({ url }) }),

  // re-index an existing repository (updates the same row in the database)
  reindexRepository: (id) =>
    request(`/api/repositories/${encodeURIComponent(id)}/reindex`, { method: "POST" }),

  // delete a repository plus its clone and index files on the server
  deleteRepository: (id) =>
    request(`/api/repositories/${encodeURIComponent(id)}`, { method: "DELETE" }),

  repositoryStatus: (id) => request(`/api/repositories/${encodeURIComponent(id)}/status`),

  query: (repoId, question) =>
    request("/api/query", {
      method: "POST",
      body: JSON.stringify({ repo_id: repoId, question }),
    }),

  file: (repoId, path, start, end) =>
    request(
      `/api/file?repo_id=${encodeURIComponent(repoId)}&path=${encodeURIComponent(
        path
      )}&start=${start}&end=${end}`
    ),

  graph: (repoId, level) =>
    request(`/api/graph/${encodeURIComponent(repoId)}?level=${encodeURIComponent(level)}`),

  security: (repoId) =>
    request("/api/security/analyze", {
      method: "POST",
      body: JSON.stringify({ repo_id: repoId }),
    }),
};

// Graphs are large and do not change until a repo is re-indexed,
// so each (repo, level) pair is fetched once per page load.
const graphCache = new Map();
export function getGraph(repoId, level, { force = false } = {}) {
  const key = `${repoId}:${level}`;
  if (force) graphCache.delete(key);
  if (!graphCache.has(key)) {
    const p = api.graph(repoId, level).catch((e) => {
      graphCache.delete(key);
      throw e;
    });
    graphCache.set(key, p);
  }
  return graphCache.get(key);
}
