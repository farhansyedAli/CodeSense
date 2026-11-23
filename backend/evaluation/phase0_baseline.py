"""Phase 0 — capture baseline graph stats for the Flask index.

Reads backend/evaluation/baseline/graph_flask_baseline.graphml and writes
backend/evaluation/results/phase0_graph_baseline.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx


BASELINE = Path(__file__).parent / "baseline" / "graph_flask_baseline.graphml"
OUT = Path(__file__).parent / "results" / "phase0_graph_baseline.json"


def main() -> None:
    g: nx.DiGraph = nx.read_graphml(BASELINE)
    nodes = g.number_of_nodes()
    edges = g.number_of_edges()
    ratio = edges / nodes if nodes else 0.0
    self_edges = sum(1 for u, v in g.edges() if u == v)

    out_deg = sorted(g.out_degree(), key=lambda kv: kv[1], reverse=True)[:10]
    top10 = [{"node": n, "out_degree": d} for n, d in out_deg]

    result = {
        "source_file": str(BASELINE.relative_to(Path(__file__).parent.parent.parent)),
        "node_count": nodes,
        "edge_count": edges,
        "edge_to_node_ratio": round(ratio, 4),
        "self_edges": self_edges,
        "top_10_out_degree": top10,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
