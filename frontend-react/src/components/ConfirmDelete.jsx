import { useEffect, useState } from "react";
import { Loader2, Trash2, X } from "lucide-react";

// Asks before a repository is removed from CodeSense and the backend.
export default function ConfirmDelete({ repo, onCancel, onConfirm }) {
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!repo) return;
    setBusy(false);
    const onKey = (e) => e.key === "Escape" && !busy && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [repo, busy, onCancel]);

  if (!repo) return null;

  const confirm = async () => {
    setBusy(true);
    const ok = await onConfirm(repo);
    if (!ok) setBusy(false);
  };

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && !busy && onCancel()}>
      <div className="dialog sm" role="alertdialog" aria-modal="true" aria-labelledby="del-title" aria-describedby="del-desc">
        <div className="dialog-head">
          <div className="dialog-icon danger"><Trash2 size={19} /></div>
          <div>
            <h2 id="del-title">Delete {repo.name}?</h2>
            <p className="muted" id="del-desc">
              This removes the repository, its index and its chat history from CodeSense.
              The GitHub repository itself is not touched. You can connect it again later.
            </p>
          </div>
          <button type="button" className="icon-btn" onClick={onCancel} disabled={busy} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="dialog-actions">
          <button type="button" className="btn ghost" onClick={onCancel} disabled={busy} autoFocus>Cancel</button>
          <button type="button" className="btn danger-solid" onClick={confirm} disabled={busy}>
            {busy ? <Loader2 size={15} className="spin" /> : <Trash2 size={15} />} Delete repository
          </button>
        </div>
      </div>
    </div>
  );
}
