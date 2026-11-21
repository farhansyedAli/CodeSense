import { useState } from "react";
import {
  AlertCircle, GitBranch, History, Loader2, MessagesSquare, Network,
  Plus, Search, ShieldCheck, Workflow, X,
} from "lucide-react";
import { formatNumber } from "../lib/utils";

export const VIEWS = [
  { id: "qa", label: "Ask the codebase", short: "Q&A", icon: MessagesSquare },
  { id: "arch", label: "Architecture", short: "Architecture", icon: Network },
  { id: "sec", label: "Security", short: "Security", icon: ShieldCheck },
  { id: "impact", label: "Impact analysis", short: "Impact", icon: Workflow },
];

function RepoStatus({ repo }) {
  if (repo.status === "ready")
    return (
      <span className="repo-meta">
        <span className="dot ok" aria-hidden /> {formatNumber(repo.chunk_count)} chunks
      </span>
    );
  if (repo.status === "failed")
    return (
      <span className="repo-meta danger">
        <AlertCircle size={12} aria-hidden /> Indexing failed
      </span>
    );
  return (
    <span className="repo-meta warn">
      <Loader2 size={12} className="spin" aria-hidden />
      {repo.stage || "queued"} · {repo.progress ?? 0}%
    </span>
  );
}

export default function Sidebar({
  repos, loaded, selectedId, onSelect, view, onView, onConnect,
  securityCount, recent, onRecent, onClearRecent, open, onClose,
}) {
  const [filter, setFilter] = useState("");
  const shown = repos.filter((r) => r.name.toLowerCase().includes(filter.toLowerCase()));

  return (
    <aside className={`sidebar ${open ? "open" : ""}`} aria-label="Navigation">
      <div className="brand">
        <img src="/favicon.svg" alt="" width="28" height="28" />
        <div>
          <div className="brand-name">CodeSense</div>
          <div className="brand-sub">Python codebase intelligence</div>
        </div>
        <button className="icon-btn only-mobile" onClick={onClose} aria-label="Close menu">
          <X size={18} />
        </button>
      </div>

      <button className="btn primary block" onClick={onConnect}>
        <Plus size={16} /> Connect repository
      </button>

      <nav className="nav" aria-label="Views">
        {VIEWS.map((v) => {
          const Icon = v.icon;
          return (
            <button
              key={v.id}
              className={`nav-item ${view === v.id ? "active" : ""}`}
              onClick={() => onView(v.id)}
              aria-current={view === v.id ? "page" : undefined}
            >
              <Icon size={17} aria-hidden />
              <span>{v.label}</span>
              {v.id === "sec" && securityCount > 0 && (
                <span className="count danger" aria-label={`${securityCount} vulnerabilities`}>
                  {securityCount}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      <div className="side-section grow">
        <div className="side-label">
          <span>Repositories</span>
          <span className="muted">{repos.length || ""}</span>
        </div>
        {repos.length > 4 && (
          <label className="side-search">
            <Search size={14} aria-hidden />
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter repositories"
              aria-label="Filter repositories"
            />
          </label>
        )}
        <div className="repo-list">
          {!loaded && [0, 1, 2].map((i) => <div key={i} className="skeleton repo-skel" />)}
          {loaded && repos.length === 0 && (
            <p className="side-empty">No repositories yet. Connect a GitHub repo to start.</p>
          )}
          {shown.map((r) => (
            <button
              key={r.id}
              className={`repo-item ${r.id === selectedId ? "active" : ""}`}
              onClick={() => onSelect(r.id)}
              title={r.url}
            >
              <GitBranch size={15} className="repo-icon" aria-hidden />
              <span className="repo-text">
                <span className="repo-name">{r.name}</span>
                <RepoStatus repo={r} />
              </span>
              {(r.status === "indexing" || r.status === "pending") && (
                <span className="repo-progress" style={{ width: `${r.progress ?? 0}%` }} aria-hidden />
              )}
            </button>
          ))}
        </div>
      </div>

      {recent.length > 0 && (
        <div className="side-section">
          <div className="side-label">
            <span>Recent questions</span>
            <button className="link-btn" onClick={onClearRecent}>Clear</button>
          </div>
          {recent.slice(0, 5).map((q) => (
            <button key={q} className="recent-item" onClick={() => onRecent(q)} title={q}>
              <History size={13} aria-hidden />
              <span>{q}</span>
            </button>
          ))}
        </div>
      )}

      <div className="side-foot">
        <span className="pill">MVP</span>
        <span className="muted">Python repositories</span>
      </div>
    </aside>
  );
}
