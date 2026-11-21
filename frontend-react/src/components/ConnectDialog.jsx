import { useEffect, useRef, useState } from "react";
import { Github, Loader2, X } from "lucide-react";
import { api } from "../lib/api";
import { normalizeGithubUrl } from "../lib/utils";

const EXAMPLES = ["pallets/flask", "psf/requests", "fastapi/fastapi"];

export default function ConnectDialog({ open, onClose, onConnected }) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setTimeout(() => inputRef.current?.focus(), 30);
    const onKey = (e) => e.key === "Escape" && !busy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  const submit = async (e) => {
    e.preventDefault();
    const url = normalizeGithubUrl(value);
    if (!url) {
      setError("Enter a GitHub repository like owner/repo or https://github.com/owner/repo.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const repo = await api.connectRepository(url);
      setValue("");
      onConnected(repo);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <form className="dialog" onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="connect-title">
        <div className="dialog-head">
          <div className="dialog-icon"><Github size={20} /></div>
          <div>
            <h2 id="connect-title">Connect a repository</h2>
            <p className="muted">
              CodeSense clones the repo, parses every Python file and builds a searchable index.
            </p>
          </div>
          <button type="button" className="icon-btn" onClick={onClose} disabled={busy} aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <label className="field-label" htmlFor="repo-url">GitHub repository</label>
        <div className={`input-group ${error ? "invalid" : ""}`}>
          <span className="input-prefix mono">github.com/</span>
          <input
            id="repo-url"
            ref={inputRef}
            className="mono"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="owner/repository"
            autoComplete="off"
            spellCheck="false"
            aria-invalid={!!error}
            aria-describedby={error ? "repo-url-error" : undefined}
          />
        </div>
        {error && <p className="field-error" id="repo-url-error" role="alert">{error}</p>}

        <div className="examples">
          <span className="muted">Try:</span>
          {EXAMPLES.map((ex) => (
            <button type="button" key={ex} className="chip" onClick={() => setValue(ex)}>
              {ex}
            </button>
          ))}
        </div>

        <div className="dialog-actions">
          <button type="button" className="btn ghost" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn primary" disabled={busy || !value.trim()}>
            {busy ? <><Loader2 size={16} className="spin" /> Connecting…</> : "Connect & index"}
          </button>
        </div>
      </form>
    </div>
  );
}
