from pathlib import Path
import networkx as nx
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["graph"])

INDEXES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "indexes"


def _package_of(path: str, depth: int = 2) -> str:
    if not path:
        return "unknown"
    parts = path.split("/")
    if len(parts) <= depth:
        return parts[0]
    return "/".join(parts[:depth])


@router.get("/graph/{repo_id}")
async def get_graph(repo_id: str, level: str = "module"):
    """
    Return dependency graph as JSON.
    level: 'package' | 'module' | 'function'
    """
    graph_path = INDEXES_DIR / repo_id / "graph.graphml"
    if not graph_path.exists():
        raise HTTPException(status_code=404, detail="Graph not found")

    g = nx.read_graphml(graph_path)

    if level == "package":
        pg = nx.DiGraph()
        for node, data in g.nodes(data=True):
            pkg = _package_of(data.get("file", ""), depth=2)
            if not pg.has_node(pkg):
                pg.add_node(pkg, type="package", file=pkg)
        for src, dst in g.edges():
            src_pkg = _package_of(g.nodes[src].get("file", ""), depth=2)
            dst_pkg = _package_of(g.nodes[dst].get("file", ""), depth=2)
            if src_pkg != dst_pkg:
                pg.add_edge(src_pkg, dst_pkg)
        g = pg

    elif level == "module":
        mg = nx.DiGraph()
        for node, data in g.nodes(data=True):
            file = data.get("file", "unknown")
            if not mg.has_node(file):
                mg.add_node(file, type="module", file=file)
        for src, dst in g.edges():
            src_file = g.nodes[src].get("file")
            dst_file = g.nodes[dst].get("file")
            if src_file and dst_file and src_file != dst_file:
                mg.add_edge(src_file, dst_file)
        g = mg

    # level == "function" uses graph as-is (symbol-level, full detail)

    try:
        pr = nx.pagerank(g) if g.number_of_nodes() > 0 else {}
    except Exception:
        pr = {}

    return {
        "level": level,
        "nodes": [
            {
                "id": n,
                "label": (n.split("::")[-1] if "::" in n else n.split("/")[-1]) or n,
                "pagerank": pr.get(n, 0),
                "file": g.nodes[n].get("file", ""),
                "type": g.nodes[n].get("type", ""),
            }
            for n in g.nodes()
        ],
        "edges": [{"source": s, "target": t} for s, t in g.edges()],
    }