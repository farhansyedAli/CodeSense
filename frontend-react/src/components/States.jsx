import { AlertTriangle, Boxes, Code2, FileSearch, GitBranch, Loader2, Plus, Sparkles } from "lucide-react";

// Stage names reported by backend/indexing/pipeline.py
const STAGES = ["cloning", "parsing", "embedding", "graph"];

export function EmptyState({ icon: Icon = FileSearch, title, children, action }) {
  return (
    <div className="empty">
      <div className="empty-icon"><Icon size={22} /></div>
      <h3>{title}</h3>
      {children && <p className="muted">{children}</p>}
      {action}
    </div>
  );
}

export function Welcome({ onConnect }) {
  const steps = [
    { icon: GitBranch, title: "Connect", text: "Paste any public GitHub Python repository." },
    { icon: Boxes, title: "Index", text: "We parse, chunk, embed and graph every file." },
    { icon: Sparkles, title: "Ask", text: "Get answers with exact file and line citations." },
  ];
  return (
    <div className="welcome">
      <div className="welcome-badge"><Code2 size={14} /> Semantic code understanding</div>
      <h1>
        Understand any Python codebase <span className="grad">in minutes</span>, not weeks.
      </h1>
      <p className="lead">
        CodeSense answers questions about a repository in plain English, maps its architecture and
        checks its dependencies — every answer grounded in real source lines.
      </p>
      <button className="btn primary lg" onClick={onConnect}>
        <Plus size={18} /> Connect your first repository
      </button>
      <div className="steps">
        {steps.map((s, i) => (
          <div className="step" key={s.title}>
            <div className="step-num mono">0{i + 1}</div>
            <s.icon size={18} className="accent" />
            <div className="step-title">{s.title}</div>
            <div className="muted small">{s.text}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function IndexingState({ repo }) {
  const stage = (repo.stage || "").toLowerCase();
  const activeIdx = Math.max(0, STAGES.findIndex((s) => stage.includes(s)));
  return (
    <div className="empty">
      <div className="empty-icon pulse"><Loader2 size={22} className="spin" /></div>
      <h3>Indexing {repo.name}</h3>
      <p className="muted">This can take a few minutes for large repositories. You can keep using other repos meanwhile.</p>
      <div className="progress lg" role="progressbar" aria-valuenow={repo.progress ?? 0} aria-valuemin={0} aria-valuemax={100}>
        <div style={{ width: `${repo.progress ?? 0}%` }} />
      </div>
      <div className="stage-row">
        {STAGES.map((s, i) => (
          <span key={s} className={`stage ${i < activeIdx ? "done" : i === activeIdx ? "now" : ""}`}>{s}</span>
        ))}
      </div>
      <p className="mono small muted">{repo.stage || "queued"} · {repo.progress ?? 0}%</p>
    </div>
  );
}

export function FailedState({ repo, onRetry }) {
  return (
    <EmptyState
      icon={AlertTriangle}
      title={`Indexing ${repo.name} failed`}
      action={<button className="btn" onClick={onRetry}>Try indexing again</button>}
    >
      {repo.error ? (
        <>
          <span className="fail-reason mono">{repo.error}</span>
          <br />
        </>
      ) : null}
      Check the backend terminal for details, then retry. Private repositories are not supported yet.
    </EmptyState>
  );
}
