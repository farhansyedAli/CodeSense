"""Run the new AST builder on Flask chunks and report analytics.

Loads chunks from the existing Flask index (doesn't re-embed), runs
`build_dependency_graph` with the dynamic trace overlay, writes the new
graph to backend/evaluation/results/graph_flask_new.graphml, and dumps
the analytics the spec asks for.
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import networkx as nx                          # noqa: E402

from indexing.graph_builder import build_dependency_graph  # noqa: E402


REPO  = ROOT / "data/repos/fa1df9ec-61f6-4497-ab7d-184bbc30aee5"
INDEX = ROOT / "data/indexes/fa1df9ec-61f6-4497-ab7d-184bbc30aee5"
TRACE = ROOT / "backend/evaluation/results/dynamic_edges_flask.json"
OUT_GRAPH   = ROOT / "backend/evaluation/results/graph_flask_new.graphml"
OUT_RESULTS = ROOT / "backend/evaluation/results/phase6_graph_new.json"
BASELINE    = ROOT / "backend/evaluation/baseline/graph_flask_baseline.graphml"


def _top10_by_pagerank(g: nx.DiGraph) -> list[dict]:
    rows = []
    for n, d in g.nodes(data=True):
        if d.get("node_type") not in ("func", "class"):
            continue
        rows.append({
            "node": n,
            "pagerank": float(d.get("pagerank", 0.0)),
            "is_test": bool(d.get("is_test")),
            "is_src":  bool(d.get("is_src")),
            "is_docs": bool(d.get("is_docs")),
        })
    rows.sort(key=lambda r: r["pagerank"], reverse=True)
    return rows[:10]


def _static_vs_dynamic(g: nx.DiGraph) -> dict:
    """Of the CALL edges observed executing (source dynamic/both), what fraction
    did static analysis also catch? The spec says this is the headline number.
    """
    dyn_edges = [(u, v) for u, v, d in g.edges(data=True)
                 if d.get("edge_type") == "calls" and d.get("source") in ("dynamic", "both")]
    if not dyn_edges:
        return {"dynamic_edge_count": 0, "static_recall": None}
    both = sum(1 for u, v in dyn_edges
               if g.edges[u, v].get("source") == "both")
    return {
        "dynamic_edge_count": len(dyn_edges),
        "found_by_static_too": both,
        "static_recall": round(both / len(dyn_edges), 3),
    }


def main() -> None:
    chunks = json.loads((INDEX / "chunks_full.json").read_text())
    print(f"[run] loaded {len(chunks)} chunks from existing Flask index")

    t0 = time.time()
    g = build_dependency_graph(
        chunks,
        repo_path=REPO,
        trace_file=TRACE if TRACE.exists() else None,
    )
    elapsed = time.time() - t0
    print(f"[run] built graph in {elapsed:.1f}s")

    # Make a serializable snapshot of the stats BEFORE writing graphml strips them.
    stats = {
        "wall_clock_seconds": round(elapsed, 2),
        "graph_builder_version": g.graph.get("graph_builder_version"),
        "edge_direction_doc": g.graph.get("edge_direction_doc"),
        "node_count": g.number_of_nodes(),
        "edge_count": g.number_of_edges(),
        "edge_to_node_ratio": round(g.number_of_edges() / g.number_of_nodes(), 4)
            if g.number_of_nodes() else 0.0,
        "edge_counts_by_type":       dict(g.graph.get("edge_counts_by_type", {})),
        "edge_counts_by_source":     dict(g.graph.get("edge_counts_by_source", {})),
        "edge_counts_by_confidence": dict(g.graph.get("edge_counts_by_confidence", {})),
        "node_counts_by_tag":        g.graph.get("node_counts_by_tag", {}),
        "call_phase_stats":          g.graph.get("call_phase_stats", {}),
        "top10_by_pagerank":         _top10_by_pagerank(g),
        "static_vs_dynamic":         _static_vs_dynamic(g),
    }

    baseline = nx.read_graphml(BASELINE) if BASELINE.exists() else None
    if baseline is not None:
        stats["baseline"] = {
            "node_count": baseline.number_of_nodes(),
            "edge_count": baseline.number_of_edges(),
            "edge_to_node_ratio": round(baseline.number_of_edges() / baseline.number_of_nodes(), 4)
                if baseline.number_of_nodes() else 0.0,
        }

    # Pop non-scalar graph attrs before writing graphml (same thing pipeline.py does).
    for k in ("call_phase_stats", "edge_counts_by_type", "edge_counts_by_source",
              "edge_counts_by_confidence", "node_counts_by_tag"):
        g.graph.pop(k, None)
    OUT_GRAPH.parent.mkdir(parents=True, exist_ok=True)
    nx.write_graphml(g, OUT_GRAPH)

    OUT_RESULTS.write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
