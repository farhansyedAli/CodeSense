import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight, Box, ChevronRight, FileCode2, FlaskConical, FunctionSquare, Info, Loader2, Search, Workflow,
} from "lucide-react";
import { getGraph } from "../lib/api";
import { fileName, isTestPath } from "../lib/utils";
import { EmptyState } from "../components/States";

const VALID_NAME = /^[A-Za-z_][\w]*$/;
const MAX_DEPTH = 3;

// Builds caller/callee lookups from the function-level dependency graph.
function buildIndex(graph) {
  // skip nodes whose name the regex parser could not read cleanly
  const byId = new Map(graph.nodes.filter((n) => VALID_NAME.test(n.label)).map((n) => [n.id, n]));
  const callers = new Map();
  const callees = new Map();
  for (const e of graph.edges) {
    if (e.source === e.target || !byId.has(e.source) || !byId.has(e.target)) continue;
    if (!callers.has(e.target)) callers.set(e.target, new Set());
    if (!callees.has(e.source)) callees.set(e.source, new Set());
    callers.get(e.target).add(e.source);
    callees.get(e.source).add(e.target);
  }
  const symbols = [...byId.values()];
  return { byId, callers, callees, symbols };
}

function analyse(index, targetId) {
  // breadth-first walk over callers, up to MAX_DEPTH levels
  const depthOf = new Map([[targetId, 0]]);
  let frontier = [targetId];
  for (let d = 1; d <= MAX_DEPTH && frontier.length; d++) {
    const next = [];
    for (const id of frontier) {
      for (const c of index.callers.get(id) || []) {
        if (!depthOf.has(c)) { depthOf.set(c, d); next.push(c); }
      }
    }
    frontier = next;
  }
  depthOf.delete(targetId);
  const affected = [...depthOf.keys()].map((id) => index.byId.get(id)).filter(Boolean);
  const direct = [...(index.callers.get(targetId) || [])].map((id) => index.byId.get(id)).filter(Boolean);
  const calls = [...(index.callees.get(targetId) || [])].map((id) => index.byId.get(id)).filter(Boolean);
  const files = new Set(affected.map((n) => n.file));
  const tests = affected.filter((n) => isTestPath(n.file));
  const total = affected.length;
  const risk = total === 0 ? "none" : total < 6 ? "low" : total < 25 ? "medium" : "high";
  return { direct, calls, affected, files, tests, total, risk, depthOf };
}

function NodeRow({ node, onOpen, children, sub }) {
  const test = isTestPath(node.file);
  const Icon = test ? FlaskConical : node.type === "class" ? Box : FunctionSquare;
  return (
    <div className={`impact-row ${sub ? "sub" : ""}`}>
      <button className="impact-node" onClick={() => onOpen({ path: node.file, line: 1, name: node.label })}>
        <Icon size={15} className={test ? "violet-text" : "accent"} aria-hidden />
        <span className="mono strong">{node.label}</span>
        {test && <span className="tag violet">test</span>}
        <span className="grow" />
        <span className="mono small muted ellipsis">{node.file}</span>
        <FileCode2 size={14} className="muted" aria-hidden />
      </button>
      {children}
    </div>
  );
}

function CallerTree({ node, index, onOpen }) {
  const [open, setOpen] = useState(false);
  const parents = [...(index.callers.get(node.id) || [])].map((id) => index.byId.get(id)).filter(Boolean);
  return (
    <div className="tree-item">
      <div className="tree-line">
        <button
          className={`tree-toggle ${parents.length ? "" : "hidden"}`}
          onClick={() => setOpen((o) => !o)}
          aria-label={open ? "Hide callers" : "Show callers"}
          aria-expanded={open}
        >
          <ChevronRight size={14} className={open ? "rot" : ""} />
        </button>
        <NodeRow node={node} onOpen={onOpen} />
        {parents.length > 0 && <span className="tree-count mono small muted">{parents.length} ↑</span>}
      </div>
      {open && (
        <div className="tree-children">
          {parents.slice(0, 30).map((p) => (
            <NodeRow key={p.id} node={p} onOpen={onOpen} sub />
          ))}
          {parents.length > 30 && <div className="muted small pad">+ {parents.length - 30} more</div>}
        </div>
      )}
    </div>
  );
}

export default function ImpactView({ repo, onOpenSource, initialSymbol }) {
  const [state, setState] = useState({ loading: true, error: null, index: null });
  const [query, setQuery] = useState("");
  const [targetId, setTargetId] = useState(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, error: null, index: null });
    setTargetId(null);
    getGraph(repo.id, "function")
      .then((g) => !cancelled && setState({ loading: false, error: null, index: buildIndex(g) }))
      .catch((e) => !cancelled && setState({ loading: false, error: e.message, index: null }));
    return () => { cancelled = true; };
  }, [repo.id]);

  useEffect(() => {
    if (initialSymbol?.id && state.index?.byId.has(initialSymbol.id)) {
      setTargetId(initialSymbol.id);
      setQuery("");
    }
  }, [initialSymbol, state.index]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || !state.index) return [];
    const scored = state.index.symbols
      .filter((n) => n.label.toLowerCase().includes(q) || n.id.toLowerCase().includes(q))
      .map((n) => ({ n, s: (n.label.toLowerCase() === q ? 0 : n.label.toLowerCase().startsWith(q) ? 1 : 2) - n.pagerank }));
    return scored.sort((a, b) => a.s - b.s).slice(0, 10).map((x) => x.n);
  }, [query, state.index]);

  const popular = useMemo(() => {
    if (!state.index) return [];
    return [...state.index.symbols]
      .filter((n) => !isTestPath(n.file))
      .sort((a, b) => (state.index.callers.get(b.id)?.size || 0) - (state.index.callers.get(a.id)?.size || 0))
      .slice(0, 6);
  }, [state.index]);

  const result = useMemo(
    () => (state.index && targetId ? analyse(state.index, targetId) : null),
    [state.index, targetId]
  );
  const target = targetId ? state.index?.byId.get(targetId) : null;

  const pick = (id) => { setTargetId(id); setQuery(""); setShowAll(false); };

  if (state.loading)
    return <div className="center-msg pad-xl"><Loader2 className="spin" size={18} /> Loading the call graph…</div>;
  if (state.error)
    return <EmptyState title="Call graph unavailable">{state.error}</EmptyState>;

  const directShown = showAll ? result?.direct : result?.direct.slice(0, 12);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>Impact analysis</h2>
          <p className="muted">
            Pick a function or class to see everything that calls it — directly and up to {MAX_DEPTH} levels
            up — before you change it.
          </p>
        </div>
      </div>

      <div className="impact-search">
        <Search size={18} aria-hidden />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && matches[0] && pick(matches[0].id)}
          placeholder="Search a function or class, e.g. create_app"
          aria-label="Search a function or class"
          className="mono"
        />
        {matches.length > 0 && (
          <div className="search-pop" role="listbox">
            {matches.map((m) => (
              <button key={m.id} className="search-opt" onClick={() => pick(m.id)} role="option">
                {m.type === "class" ? <Box size={14} className="violet-text" /> : <FunctionSquare size={14} className="accent" />}
                <span className="mono">{m.label}</span>
                <span className="muted mono small ellipsis">{m.file}</span>
                <span className="muted mono small">{state.index.callers.get(m.id)?.size || 0} callers</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {!target && (
        <div className="popular">
          <div className="side-label"><span>Most-called symbols</span></div>
          <div className="popular-grid">
            {popular.map((n) => (
              <button key={n.id} className="popular-card" onClick={() => pick(n.id)}>
                <span className="mono strong">{n.label}</span>
                <span className="mono small muted ellipsis">{fileName(n.file)}</span>
                <span className="tag green">{state.index.callers.get(n.id)?.size || 0} callers</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {target && result && (
        <>
          <div className="impact-target">
            <div className="impact-target-main">
              <div className="mono small muted">{target.type === "class" ? "class" : "function"}</div>
              <div className="impact-target-name mono">{target.label}{target.type !== "class" && "()"}</div>
              <button className="link-btn mono small" onClick={() => onOpenSource({ path: target.file, line: 1, name: target.label })}>
                {target.file} <ArrowRight size={12} />
              </button>
            </div>
            <div className={`risk risk-${result.risk}`}>
              <span className="risk-label">Blast radius</span>
              <span className="risk-value">{result.risk === "none" ? "None found" : result.risk}</span>
            </div>
          </div>

          <div className="stat-row">
            <div className="stat"><div className="stat-num">{result.direct.length}</div><div className="stat-label">Direct callers</div></div>
            <div className="stat"><div className="stat-num">{result.total}</div><div className="stat-label">Affected (≤{MAX_DEPTH} levels)</div></div>
            <div className="stat"><div className="stat-num">{result.files.size}</div><div className="stat-label">Files touched</div></div>
            <div className="stat"><div className="stat-num">{result.tests.length}</div><div className="stat-label">Tests that reach it</div></div>
          </div>

          <div className="impact-cols">
            <section className="panel">
              <header className="panel-head">
                <Workflow size={16} className="accent" /> Who calls it
                <span className="muted small">click ▸ to walk further up</span>
              </header>
              {result.direct.length === 0 ? (
                <p className="muted pad">No static callers found. It may be an entry point, or called dynamically.</p>
              ) : (
                <div className="tree">
                  {directShown.map((n) => (
                    <CallerTree key={n.id} node={n} index={state.index} onOpen={onOpenSource} />
                  ))}
                  {result.direct.length > 12 && (
                    <button className="link-btn pad" onClick={() => setShowAll((s) => !s)}>
                      {showAll ? "Show fewer" : `Show all ${result.direct.length} callers`}
                    </button>
                  )}
                </div>
              )}
            </section>

            <section className="panel">
              <header className="panel-head">
                <ArrowRight size={16} className="violet-text" /> What it calls
                <span className="muted small">{result.calls.length}</span>
              </header>
              {result.calls.length === 0 ? (
                <p className="muted pad">No outgoing calls recorded.</p>
              ) : (
                <div className="tree">
                  {result.calls.slice(0, 20).map((n) => (
                    <button key={n.id} className="callee" onClick={() => pick(n.id)} title="Analyse this symbol">
                      <FunctionSquare size={14} className="violet-text" />
                      <span className="mono">{n.label}</span>
                      <span className="mono small muted ellipsis">{fileName(n.file)}</span>
                    </button>
                  ))}
                  {result.calls.length > 20 && <div className="muted small pad">+ {result.calls.length - 20} more</div>}
                </div>
              )}
            </section>
          </div>

          <div className="alert info">
            <Info size={16} />
            <span>
              Based on the static call graph. Calls made through <code className="inline-code">getattr()</code>,
              signals or dependency injection are not captured, and functions that share a name may be linked.
            </span>
          </div>
        </>
      )}
    </div>
  );
}
