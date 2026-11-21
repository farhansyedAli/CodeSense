import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, FileCode2, Loader2, X } from "lucide-react";
import { api } from "../lib/api";
import { dirName, fileName } from "../lib/utils";
import { highlightLines, languageForPath } from "../lib/highlight";

// Right-hand drawer that shows the source lines behind a citation or graph node.
export default function SourcePanel({ repoId, source, onClose }) {
  const [state, setState] = useState({ loading: false, data: null, error: null });
  const [copied, setCopied] = useState(false);
  const bodyRef = useRef(null);

  const path = source?.path;
  const start = source?.line || 1;
  const end = Math.max(source?.endLine || start, start) ;
  const hlEnd = source?.endLine ? end : start; // range to highlight

  useEffect(() => {
    if (!path || !repoId) return;
    let cancelled = false;
    setState({ loading: true, data: null, error: null });
    api
      .file(repoId, path, start, Math.min(end, start + 150) + 25)
      .then((data) => !cancelled && setState({ loading: false, data, error: null }))
      .catch((e) => !cancelled && setState({ loading: false, data: null, error: e.message }));
    return () => { cancelled = true; };
  }, [repoId, path, start, end]);

  const lines = state.data?.lines || [];
  const html = useMemo(
    () => highlightLines(lines.map((l) => l.text), languageForPath(path)),
    [lines, path]
  );

  useEffect(() => {
    const el = bodyRef.current?.querySelector(".code-line.hl");
    el?.scrollIntoView({ block: "center" });
  }, [html]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!source) return null;

  const copyPath = async () => {
    try {
      await navigator.clipboard.writeText(`${path}:${start}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch { /* ignore */ }
  };

  return (
    <aside className="source-panel" aria-label="Source preview">
      <header className="source-head">
        <FileCode2 size={18} className="accent" aria-hidden />
        <div className="source-title">
          <div className="source-file mono">{fileName(path)}</div>
          <div className="source-dir mono" title={path}>{dirName(path) || "/"}</div>
        </div>
        <button className="icon-btn" onClick={copyPath} aria-label="Copy file path and line">
          {copied ? <Check size={16} /> : <Copy size={16} />}
        </button>
        <button className="icon-btn" onClick={onClose} aria-label="Close source preview">
          <X size={18} />
        </button>
      </header>

      <div className="source-meta">
        {source.name && <span className="tag violet mono">{source.name}</span>}
        <span className="muted mono">
          {source.endLine ? `lines ${start}–${end}` : `from line ${start}`}
        </span>
      </div>

      <div className="source-body" ref={bodyRef}>
        {state.loading && (
          <div className="center-msg"><Loader2 className="spin" size={18} /> Loading source…</div>
        )}
        {state.error && <div className="center-msg danger">{state.error}</div>}
        {!state.loading && !state.error && lines.length > 0 && (
          <pre className="code-view hljs">
            {lines.map((l, i) => (
              <div key={l.n} className={`code-line ${l.n >= start && l.n <= hlEnd ? "hl" : ""}`}>
                <span className="ln">{l.n}</span>
                <code dangerouslySetInnerHTML={{ __html: html[i] }} />
              </div>
            ))}
          </pre>
        )}
        {!state.loading && !state.error && state.data && lines.length === 0 && (
          <div className="center-msg muted">No lines in this range.</div>
        )}
      </div>
    </aside>
  );
}
