import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import cytoscape from "cytoscape";
import fcose from "cytoscape-fcose";
import {
  ArrowDownLeft, ArrowUpRight, FileCode2, Loader2, Maximize2, RefreshCw, Search, Workflow, X,
} from "lucide-react";
import { getGraph } from "../lib/api";
import { colorForGroup, cssVar } from "../lib/utils";
import { EmptyState } from "../components/States";

cytoscape.use(fcose);

const LEVELS = [
  { id: "package", label: "Packages" },
  { id: "module", label: "Modules" },
  { id: "function", label: "Functions" },
];

const groupOf = (n) => {
  const parts = (n.file || n.id).split("/");
  return parts.length > 1 ? parts.slice(0, 2).join("/") : parts[0];
};

export default function ArchitectureView({ repo, onOpenSource, onImpact }) {
  const [level, setLevel] = useState("module");
  const [state, setState] = useState({ loading: true, error: null, data: null });
  const [selected, setSelected] = useState(null);
  const [query, setQuery] = useState("");
  const [reloadKey, setReloadKey] = useState(0);
  const containerRef = useRef(null);
  const cyRef = useRef(null);

  // fetch
  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, error: null, data: null });
    setSelected(null);
    getGraph(repo.id, level, { force: reloadKey > 0 })
      .then((data) => !cancelled && setState({ loading: false, error: null, data }))
      .catch((e) =>
        !cancelled &&
        setState({
          loading: false,
          error: e.status === 404 ? "No graph found. Make sure the repository finished indexing." : e.message,
          data: null,
        })
      );
    return () => { cancelled = true; };
  }, [repo.id, level, reloadKey]);

  const stats = useMemo(() => {
    const d = state.data;
    if (!d) return null;
    const groups = new Map();
    d.nodes.forEach((n) => {
      const g = groupOf(n);
      groups.set(g, (groups.get(g) || 0) + 1);
    });
    const top = [...groups.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
    const ranked = [...d.nodes].sort((a, b) => b.pagerank - a.pagerank);
    const rank = new Map(ranked.map((n, i) => [n.id, i + 1]));
    return { groups: top, rank, ranked };
  }, [state.data]);

  const highlight = useCallback((node) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("faded pinned");
    if (!node) return;
    const hood = node.closedNeighborhood();
    cy.elements().addClass("faded");
    hood.removeClass("faded").addClass("pinned");
  }, []);

  // render graph
  useEffect(() => {
    const d = state.data;
    if (!d || !containerRef.current || d.nodes.length === 0) return;
    const big = d.nodes.length > 600;
    // graph colours follow the active (dark / light) theme
    const C = { bg: cssVar("--code-bg"), text: cssVar("--text"), text2: cssVar("--text-2"), edge: cssVar("--border-strong"), accent: cssVar("--accent") };
    const maxPr = Math.max(...d.nodes.map((n) => n.pagerank), 0.0001);
    // only the most important nodes get a label at normal zoom; the rest appear when zooming in
    const topIds = new Set(
      [...d.nodes].sort((a, b) => b.pagerank - a.pagerank).slice(0, Math.min(14, Math.ceil(d.nodes.length * 0.15))).map((n) => n.id)
    );

    const cy = cytoscape({
      container: containerRef.current,
      elements: [
        ...d.nodes.map((n) => ({
          data: {
            id: n.id, label: n.label, file: n.file, type: n.type,
            pr: Math.sqrt(n.pagerank / maxPr), color: colorForGroup(groupOf(n)),
          },
          classes: topIds.has(n.id) ? "top" : "",
        })),
        ...d.edges.map((e, i) => ({ data: { id: `e${i}`, source: e.source, target: e.target } })),
      ],
      minZoom: 0.05,
      maxZoom: 3,
      wheelSensitivity: 0.25,
      layout: {
        name: "fcose", animate: false, randomize: true,
        quality: big ? "draft" : "default",
        nodeRepulsion: 30000, idealEdgeLength: 150, edgeElasticity: 0.3, gravity: 0.12, gravityRange: 3.8,
        nodeSeparation: 140, padding: 40,
      },
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(color)",
            "background-opacity": 0.9,
            "border-color": C.bg,
            "border-width": 1.5,
            width: "mapData(pr, 0, 1, 8, 38)",
            height: "mapData(pr, 0, 1, 8, 38)",
            label: "data(label)",
            color: C.text2,
            "font-family": "JetBrains Mono, monospace",
            "font-size": 8,
            "min-zoomed-font-size": 11,
            "text-valign": "bottom",
            "text-margin-y": 4,
            "text-outline-color": C.bg,
            "text-outline-width": 2.5,
          },
        },
        { selector: "node.top", style: { "font-size": 12, "min-zoomed-font-size": 8, color: C.text, "font-weight": 600 } },
        {
          selector: "edge",
          style: {
            width: 1,
            "line-color": C.edge,
            "target-arrow-color": C.edge,
            "target-arrow-shape": big ? "none" : "triangle",
            "arrow-scale": 0.7,
            "curve-style": d.edges.length > 3000 ? "haystack" : "bezier",
            opacity: big ? 0.18 : 0.3,
          },
        },
        { selector: ".faded", style: { opacity: 0.08, "text-opacity": 0 } },
        {
          selector: "node.pinned",
          style: { "text-opacity": 1, opacity: 1, "z-index": 99, "font-size": 11, "min-zoomed-font-size": 4 },
        },
        {
          selector: "edge.pinned",
          style: {
            "line-color": C.accent, "target-arrow-color": C.accent,
            "target-arrow-shape": "triangle", opacity: 0.9, width: 1.6, "z-index": 98,
          },
        },
        {
          selector: "node:selected",
          style: { "border-color": C.text, "border-width": 3, "text-opacity": 1 },
        },
      ],
    });

    cy.on("tap", "node", (evt) => {
      const n = evt.target;
      highlight(n);
      setSelected({
        id: n.id(), label: n.data("label"), file: n.data("file"), type: n.data("type"),
        indeg: n.indegree(false), outdeg: n.outdegree(false),
      });
    });
    cy.on("tap", (evt) => {
      if (evt.target === cy) { highlight(null); setSelected(null); cy.$(":selected").unselect(); }
    });
    cy.on("mouseover", "node", () => (containerRef.current.style.cursor = "pointer"));
    cy.on("mouseout", "node", () => (containerRef.current.style.cursor = "default"));
    cy.fit(undefined, 40);
    cyRef.current = cy;
    return () => { cy.destroy(); cyRef.current = null; };
  }, [state.data, highlight]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || !state.data) return [];
    return state.data.nodes
      .filter((n) => n.id.toLowerCase().includes(q))
      .sort((a, b) => b.pagerank - a.pagerank)
      .slice(0, 8);
  }, [query, state.data]);

  const focusNode = (id) => {
    const cy = cyRef.current;
    if (!cy) return;
    const n = cy.getElementById(id);
    if (!n.nonempty()) return;
    cy.$(":selected").unselect();
    n.select();
    n.emit("tap");
    cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 1.1) }, { duration: 300 });
    setQuery("");
  };

  return (
    <div className="arch">
      <div className="toolbar">
        <div className="segmented" role="tablist" aria-label="Graph level">
          {LEVELS.map((l) => (
            <button key={l.id} role="tab" aria-selected={level === l.id}
              className={level === l.id ? "on" : ""} onClick={() => setLevel(l.id)}>
              {l.label}
            </button>
          ))}
        </div>

        <div className="search-box">
          <Search size={15} aria-hidden />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && matches[0] && focusNode(matches[0].id)}
            placeholder={`Find a ${level}…`}
            aria-label="Find a node in the graph"
            disabled={!state.data}
          />
          {matches.length > 0 && (
            <div className="search-pop" role="listbox">
              {matches.map((m) => (
                <button key={m.id} className="search-opt" onClick={() => focusNode(m.id)} role="option">
                  <span className="swatch" style={{ background: colorForGroup(groupOf(m)) }} />
                  <span className="mono">{m.label}</span>
                  <span className="muted mono small ellipsis">{m.file}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <span className="grow" />
        {state.data && (
          <span className="muted small mono">
            {state.data.nodes.length.toLocaleString()} nodes · {state.data.edges.length.toLocaleString()} edges
          </span>
        )}
        <button className="icon-btn" onClick={() => cyRef.current?.fit(undefined, 40)} aria-label="Fit graph to screen" title="Fit to screen" disabled={!state.data}>
          <Maximize2 size={16} />
        </button>
        <button className="icon-btn" onClick={() => setReloadKey((k) => k + 1)} aria-label="Reload graph" title="Reload">
          <RefreshCw size={16} />
        </button>
      </div>

      <div className="graph-wrap">
        {state.loading && (
          <div className="graph-overlay"><Loader2 className="spin" size={20} /> Building {level} graph…</div>
        )}
        {state.error && (
          <div className="graph-overlay"><EmptyState title="Graph unavailable">{state.error}</EmptyState></div>
        )}
        {state.data && state.data.nodes.length === 0 && (
          <div className="graph-overlay"><EmptyState title="Nothing to show">The graph is empty at this level.</EmptyState></div>
        )}
        <div ref={containerRef} className="graph-canvas" aria-label="Dependency graph" />

        {stats && (
          <div className="legend">
            <div className="legend-title">Largest groups</div>
            {stats.groups.map(([g, n]) => (
              <div key={g} className="legend-row">
                <span className="swatch" style={{ background: colorForGroup(g) }} />
                <span className="mono ellipsis">{g}</span>
                <span className="muted mono">{n}</span>
              </div>
            ))}
            <div className="legend-note muted">Node size = PageRank (importance)</div>
          </div>
        )}

        {selected && (
          <div className="node-card">
            <div className="node-card-head">
              <span className="swatch" style={{ background: colorForGroup(groupOf(selected)) }} />
              <span className="mono strong ellipsis">{selected.label}</span>
              <button className="icon-btn sm" onClick={() => { highlight(null); setSelected(null); }} aria-label="Close details">
                <X size={14} />
              </button>
            </div>
            <div className="mono small muted ellipsis" title={selected.file}>{selected.file || selected.id}</div>
            <div className="node-stats">
              <div><ArrowDownLeft size={13} /> <b>{selected.indeg}</b> <span className="muted">incoming</span></div>
              <div><ArrowUpRight size={13} /> <b>{selected.outdeg}</b> <span className="muted">outgoing</span></div>
              <div><span className="muted">rank</span> <b>#{stats?.rank.get(selected.id)}</b></div>
            </div>
            <div className="node-actions">
              {selected.file && selected.file.includes(".") && (
                <button className="btn sm" onClick={() => onOpenSource({ path: selected.file, line: 1 })}>
                  <FileCode2 size={14} /> Open source
                </button>
              )}
              {level === "function" && (
                <button className="btn sm" onClick={() => onImpact(selected.id)}>
                  <Workflow size={14} /> Impact
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      <p className="footnote muted">
        Click a node to highlight its neighbours · drag to pan · scroll to zoom. Edges come from the
        Sprint 1 static graph and may miss dynamic calls.
      </p>
    </div>
  );
}
