import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, ExternalLink, Github, Loader2, Menu, Moon, RefreshCw, Sun, WifiOff } from "lucide-react";
import Sidebar, { VIEWS } from "./components/Sidebar";
import ConnectDialog from "./components/ConnectDialog";
import ConfirmDelete from "./components/ConfirmDelete";
import Resizer from "./components/Resizer";
import SourcePanel from "./components/SourcePanel";
import { FailedState, IndexingState, Welcome } from "./components/States";
import QAView from "./views/QAView";
import SecurityView from "./views/SecurityView";
import ImpactView from "./views/ImpactView";
import { api } from "./lib/api";
import { usePersistentState, useRepositories } from "./lib/hooks";
import { formatNumber } from "./lib/utils";

// The graph library is large, so the Architecture view loads only when opened.
const ArchitectureView = lazy(() => import("./views/ArchitectureView"));

export default function App() {
  const { repos, loaded, error: repoError, refresh } = useRepositories();
  const [selectedId, setSelectedId] = usePersistentState("cs.selectedRepo", null);
  const [view, setView] = usePersistentState("cs.view", "qa");
  const [chats, setChats] = usePersistentState("cs.chats", {});
  const [recent, setRecent] = usePersistentState("cs.recent", []);
  const [theme, setTheme] = usePersistentState("cs.theme", "dark"); // dark by default

  // apply the theme to <html> so the CSS variables in tokens.css switch
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#fafaf9" : "#0A0A0C");
  }, [theme]);
  const toggleTheme = () => setTheme((t) => (t === "light" ? "dark" : "light"));
  const [source, setSource] = useState(null);
  const [connectOpen, setConnectOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [prefill, setPrefill] = useState(null);
  const [impactSymbol, setImpactSymbol] = useState(null);
  const [security, setSecurity] = useState({}); // repoId -> { data, error, scanning }
  const [toast, setToast] = useState(null);
  const [toDelete, setToDelete] = useState(null);
  // panel widths chosen by dragging (null = default width from tokens.css)
  const [sidebarW, setSidebarW] = usePersistentState("cs.sidebarW", null);
  const [sourceW, setSourceW] = usePersistentState("cs.sourceW", null);
  const panelWidth = (sel) => document.querySelector(sel)?.getBoundingClientRect().width || 0;
  const MAIN_MIN = 380; // the middle panel never gets narrower than this

  const repo = useMemo(() => repos.find((r) => r.id === selectedId) || null, [repos, selectedId]);

  // pick a sensible default repository once the list arrives
  useEffect(() => {
    if (!loaded || repos.length === 0) return;
    if (!repo) {
      const ready = repos.find((r) => r.status === "ready");
      setSelectedId((ready || repos[0]).id);
    }
  }, [loaded, repos, repo, setSelectedId]);

  useEffect(() => { setSource(null); }, [selectedId]);

  const showToast = useCallback((msg, kind = "info") => setToast({ msg, kind, id: Date.now() }), []);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  const setMessages = useCallback(
    (repoId, updater) =>
      setChats((c) => ({ ...c, [repoId]: updater(c[repoId] || []).slice(-60) })),
    [setChats]
  );

  const addRecent = useCallback(
    (q) => setRecent((r) => [q, ...r.filter((x) => x !== q)].slice(0, 8)),
    [setRecent]
  );

  const runScan = useCallback(async (repoId) => {
    setSecurity((s) => ({ ...s, [repoId]: { ...s[repoId], scanning: true, error: null } }));
    try {
      const data = await api.security(repoId);
      setSecurity((s) => ({ ...s, [repoId]: { data, error: null, scanning: false } }));
    } catch (e) {
      setSecurity((s) => ({ ...s, [repoId]: { ...s[repoId], error: e.message, scanning: false } }));
    }
  }, []);

  // re-index a repository in place (same row, no duplicate in the list)
  const reindex = async (target = repo) => {
    if (!target) return;
    try {
      await api.reindexRepository(target.id);
      await refresh();
      setSelectedId(target.id);
      showToast(`Re-indexing ${target.name}…`);
    } catch (e) {
      showToast(e.message, "error");
    }
  };

  // delete a repository on the backend and forget everything kept for it here
  const deleteRepo = async (target) => {
    try {
      await api.deleteRepository(target.id);
    } catch (e) {
      showToast(e.message, "error");
      return false;
    }
    setChats((c) => { const { [target.id]: _, ...rest } = c; return rest; });
    setSecurity((s) => { const { [target.id]: _, ...rest } = s; return rest; });
    if (selectedId === target.id) {
      const next = repos.find((r) => r.id !== target.id && r.status === "ready") || repos.find((r) => r.id !== target.id);
      setSelectedId(next ? next.id : null);
      setSource(null);
    }
    setToDelete(null);
    await refresh();
    showToast(`${target.name} was deleted.`);
    return true;
  };

  const onConnected = async (r) => {
    setConnectOpen(false);
    await refresh();
    setSelectedId(r.id);
    setView("qa");
    showToast(`Indexing ${r.name} started. This can take a few minutes.`);
  };

  const goView = (v) => { setView(v); setNavOpen(false); };

  const secCount = repo ? security[repo.id]?.data?.summary?.vulnerabilities_total || 0 : 0;
  const currentView = VIEWS.find((v) => v.id === view) || VIEWS[0];

  let content;
  if (!loaded) {
    content = <div className="center-msg pad-xl"><Loader2 className="spin" size={18} /> Loading…</div>;
  } else if (repoError && repos.length === 0) {
    content = (
      <div className="empty">
        <div className="empty-icon danger"><WifiOff size={22} /></div>
        <h3>Can’t reach the backend</h3>
        <p className="muted">{repoError}</p>
        <pre className="cmd mono">cd backend{"\n"}uvicorn app.main:app --reload --port 8000</pre>
        <button className="btn" onClick={refresh}><RefreshCw size={15} /> Try again</button>
      </div>
    );
  } else if (!repo) {
    content = <Welcome onConnect={() => setConnectOpen(true)} />;
  } else if (repo.status === "failed") {
    content = <FailedState repo={repo} onRetry={() => reindex(repo)} />;
  } else if (repo.status !== "ready") {
    content = <IndexingState repo={repo} />;
  } else if (view === "qa") {
    content = (
      <QAView
        key={repo.id}
        repo={repo}
        messages={chats[repo.id] || []}
        setMessages={setMessages}
        onCite={setSource}
        prefill={prefill}
        onAsked={addRecent}
      />
    );
  } else if (view === "arch") {
    content = (
      <ArchitectureView
        key={theme}
        repo={repo}
        onOpenSource={setSource}
        onImpact={(id) => { setImpactSymbol({ id, n: Date.now() }); setView("impact"); }}
      />
    );
  } else if (view === "sec") {
    content = <SecurityView repo={repo} sec={security[repo.id]} onScan={runScan} />;
  } else {
    content = <ImpactView repo={repo} onOpenSource={setSource} initialSymbol={impactSymbol} />;
  }

  return (
    <div
      className={`app ${source ? "with-source" : ""}`}
      style={{
        ...(sidebarW ? { "--sidebar-w": `${sidebarW}px` } : {}),
        ...(sourceW ? { "--source-w": `${sourceW}px` } : {}),
      }}
    >
      <a href="#main" className="skip-link">Skip to content</a>
      <Sidebar
        repos={repos}
        loaded={loaded}
        selectedId={selectedId}
        onSelect={(id) => { setSelectedId(id); setNavOpen(false); }}
        view={view}
        onView={goView}
        onConnect={() => { setConnectOpen(true); setNavOpen(false); }}
        securityCount={secCount}
        recent={recent}
        onRecent={(q) => { setView("qa"); setPrefill({ text: q, n: Date.now() }); setNavOpen(false); }}
        onClearRecent={() => setRecent([])}
        open={navOpen}
        onClose={() => setNavOpen(false)}
        onReindex={(r) => reindex(r)}
        onDelete={(r) => setToDelete(r)}
      />
      <Resizer
        side="left"
        label="Resize sidebar"
        min={220}
        max={() => Math.min(520, window.innerWidth - MAIN_MIN - (source ? panelWidth(".source-panel") : 0))}
        getWidth={() => panelWidth(".sidebar")}
        onChange={setSidebarW}
      />
      {navOpen && <div className="scrim" onClick={() => setNavOpen(false)} />}

      <main className="main" id="main">
        <header className="topbar">
          <button className="icon-btn only-mobile" onClick={() => setNavOpen(true)} aria-label="Open menu">
            <Menu size={20} />
          </button>
          {repo ? (
            <div className="repo-head">
              <Github size={18} className="muted" aria-hidden />
              <div className="repo-head-text">
                <div className="repo-head-name">
                  <span className="mono">{repo.name}</span>
                  {repo.status === "ready" && <span className="tag green"><span className="dot ok" /> Indexed</span>}
                  {(repo.status === "indexing" || repo.status === "pending") && (
                    <span className="tag amber"><Loader2 size={11} className="spin" /> {repo.progress ?? 0}%</span>
                  )}
                  {repo.status === "failed" && <span className="tag red"><AlertCircle size={11} /> Failed</span>}
                </div>
                <div className="repo-head-sub muted small">
                  {currentView.label}
                  {repo.status === "ready" && ` · ${formatNumber(repo.chunk_count)} code chunks`}
                </div>
              </div>
            </div>
          ) : (
            <div className="repo-head-name">CodeSense</div>
          )}
          <span className="grow" />
          {repo && (
            <>
              <a className="icon-btn" href={repo.url} target="_blank" rel="noopener noreferrer" aria-label="Open on GitHub" title="Open on GitHub">
                <ExternalLink size={16} />
              </a>
              <button
                className="btn ghost sm"
                onClick={() => reindex(repo)}
                disabled={repo.status === "indexing" || repo.status === "pending"}
                title="Clone and index the latest version again"
              >
                <RefreshCw size={14} /> Re-index
              </button>
            </>
          )}
          <button
            className="icon-btn theme-toggle"
            onClick={toggleTheme}
            aria-label={theme === "light" ? "Switch to dark theme" : "Switch to light theme"}
            title={theme === "light" ? "Dark theme" : "Light theme"}
          >
            {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
          </button>
        </header>

        {repo && repo.status === "ready" && (
          <nav className="tabs only-mobile-flex" aria-label="Views">
            {VIEWS.map((v) => (
              <button key={v.id} className={view === v.id ? "on" : ""} onClick={() => goView(v.id)}>
                {v.short}
              </button>
            ))}
          </nav>
        )}

        <div className="content">
          <Suspense fallback={<div className="center-msg pad-xl"><Loader2 className="spin" size={18} /> Loading…</div>}>
            {content}
          </Suspense>
        </div>
      </main>

      {source && repo && (
        <>
          <Resizer
            side="right"
            label="Resize code panel"
            min={320}
            max={() => window.innerWidth - panelWidth(".sidebar") - MAIN_MIN}
            getWidth={() => panelWidth(".source-panel")}
            onChange={setSourceW}
          />
          <SourcePanel repoId={repo.id} source={source} onClose={() => setSource(null)} />
        </>
      )}

      <ConfirmDelete repo={toDelete} onCancel={() => setToDelete(null)} onConfirm={deleteRepo} />
      <ConnectDialog open={connectOpen} onClose={() => setConnectOpen(false)} onConnected={onConnected} />

      {toast && (
        <div className={`toast ${toast.kind}`} role="status" key={toast.id}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}
