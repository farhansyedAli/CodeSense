import { useEffect, useRef, useState } from "react";
import {
  AlertCircle, ExternalLink, GitBranch, History, Loader2, MessagesSquare, MoreHorizontal,
  Network, Plus, RefreshCw, Search, ShieldCheck, Trash2, Workflow, X,
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

const isBusy = (r) => r.status === "indexing" || r.status === "pending";

// "⋯" menu on each repository: re-index, open on GitHub, delete.
// Rendered with position: fixed so the scrolling repo list cannot clip it.
function RepoMenu({ repo, onReindex, onDelete }) {
  const [pos, setPos] = useState(null);
  const btnRef = useRef(null);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!pos) return;
    const close = (e) => {
      if (e.type === "keydown" && e.key !== "Escape") return;
      if (e.type === "mousedown" && (menuRef.current?.contains(e.target) || btnRef.current?.contains(e.target))) return;
      setPos(null);
    };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", close);
    window.addEventListener("resize", close);
    document.querySelector(".repo-list")?.addEventListener("scroll", close);
    menuRef.current?.querySelector("button:not(:disabled)")?.focus();
    return () => {
      window.removeEventListener("mousedown", close);
      window.removeEventListener("keydown", close);
      window.removeEventListener("resize", close);
      document.querySelector(".repo-list")?.removeEventListener("scroll", close);
    };
  }, [pos]);

  const toggle = (e) => {
    e.stopPropagation();
    if (pos) return setPos(null);
    const r = btnRef.current.getBoundingClientRect();
    const top = Math.min(r.bottom + 4, window.innerHeight - 150);
    setPos({ top, left: r.right - 200 > 8 ? r.right - 200 : 8 });
  };
  const run = (fn) => (e) => { e.stopPropagation(); setPos(null); fn(repo); };

  return (
    <>
      <button
        ref={btnRef}
        className={`repo-more ${pos ? "open" : ""}`}
        onClick={toggle}
        aria-label={`Options for ${repo.name}`}
        aria-haspopup="menu"
        aria-expanded={!!pos}
      >
        <MoreHorizontal size={16} />
      </button>
      {pos && (
        <div className="menu" role="menu" ref={menuRef} style={{ top: pos.top, left: pos.left }}>
          <button role="menuitem" onClick={run(onReindex)} disabled={isBusy(repo)}>
            <RefreshCw size={14} /> {isBusy(repo) ? "Indexing…" : "Re-index"}
          </button>
          <a role="menuitem" href={repo.url} target="_blank" rel="noopener noreferrer" onClick={() => setPos(null)}>
            <ExternalLink size={14} /> Open on GitHub
          </a>
          <div className="menu-sep" />
          <button role="menuitem" className="danger" onClick={run(onDelete)} disabled={isBusy(repo)}
            title={isBusy(repo) ? "Wait for indexing to finish" : undefined}>
            <Trash2 size={14} /> Delete repository
          </button>
        </div>
      )}
    </>
  );
}

export default function Sidebar({
  repos, loaded, selectedId, onSelect, view, onView, onConnect,
  securityCount, recent, onRecent, onClearRecent, open, onClose, onReindex, onDelete,
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
            <div key={r.id} className={`repo-row ${r.id === selectedId ? "active" : ""}`}>
              <button
                className={`repo-item ${r.id === selectedId ? "active" : ""}`}
                onClick={() => onSelect(r.id)}
                title={r.url}
              >
                <GitBranch size={15} className="repo-icon" aria-hidden />
                <span className="repo-text">
                  <span className="repo-name">{r.name}</span>
                  <RepoStatus repo={r} />
                </span>
                {isBusy(r) && (
                  <span className="repo-progress" style={{ width: `${r.progress ?? 0}%` }} aria-hidden />
                )}
              </button>
              <RepoMenu repo={r} onReindex={onReindex} onDelete={onDelete} />
            </div>
          ))}
          {loaded && repos.length > 0 && shown.length === 0 && (
            <p className="side-empty">No repository matches “{filter}”.</p>
          )}
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
