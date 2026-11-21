import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, ExternalLink, Github, Loader2, Menu, Moon, RefreshCw, Sun, WifiOff } from "lucide-react";
import Sidebar, { VIEWS } from "./components/Sidebar";
import ConnectDialog from "./components/ConnectDialog";
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

  const reindex = async () => {
    if (!repo) return;
    try {
      const r = await api.connectRepository(repo.url);
      await refresh();
      setSelectedId(r.id);
      showToast(`Re-indexing ${r.name}…`);
    } catch (e) {
      showToast(e.message, "error");
    }
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
    content = <FailedState repo={repo} onRetry={reindex} />;
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
    <div className={`app ${source ? "with-source" : ""}`}>
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
                onClick={reindex}
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
        <SourcePanel repoId={repo.id} source={source} onClose={() => setSource(null)} />
      )}

      <ConnectDialog open={connectOpen} onClose={() => setConnectOpen(false)} onConnected={onConnected} />

      {toast && (
        <div className={`toast ${toast.kind}`} role="status" key={toast.id}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}
