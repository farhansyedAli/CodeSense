"""
CodeSense indexing pipeline — reusable library.

This is a refactored version of indexing_spike_v3.py, adapted to be called
from FastAPI. The core algorithms are identical; only the entry point and
the progress reporting have changed.
"""

import os
import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable

import numpy as np
import networkx as nx
from tree_sitter import Parser, Language
import tree_sitter_python as tspython


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
REPOS_DIR = DATA_DIR / "repos"
INDEXES_DIR = DATA_DIR / "indexes"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"   # Sprint 1 only; Sprint 2 will upgrade
EMBED_CHAR_LIMIT = 2048
MAX_REPO_LOC = 100_000                 # Prototype size cap


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

def _get_parser():
    lang = Language(tspython.language())
    parser = Parser()
    parser.language = lang
    return parser, lang


def parse_python_file(parser, language, file_path: str):
    """Extract function and class nodes from a single file."""
    from tree_sitter import Query, QueryCursor

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()

    tree = parser.parse(bytes(code, "utf-8"))
    query = Query(language, """
        (function_definition) @func
        (class_definition) @class
    """)
    cursor = QueryCursor(query)
    matches = cursor.matches(tree.root_node)

    nodes = []
    for _pattern_idx, captures in matches:
        for capture_type, node_list in captures.items():
            for node in node_list:
                name = None
                for child in node.children:
                    if child.type == "identifier":
                        name = code[child.start_byte:child.end_byte]
                        break
                if name is None:
                    name = capture_type
                nodes.append({
                    "type": capture_type,
                    "name": name,
                    "file_path": str(file_path),
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                    "source": code[node.start_byte:node.end_byte],
                })
    return nodes, code


def chunk_repository(repo_path: Path, on_progress: Callable | None = None):
    """Walk the repository and produce chunks."""
    parser, language = _get_parser()
    all_chunks = []

    py_files = [
        f for f in repo_path.rglob("*.py")
        if not any(skip in str(f) for skip in
                   ("site-packages", "venv", ".venv", "node_modules"))
    ]
    total = len(py_files)

    for i, file_path in enumerate(py_files):
        try:
            nodes, code = parse_python_file(parser, language, str(file_path))
            rel_path = str(file_path.relative_to(repo_path)).replace("\\", "/")

            # Module overview chunk
            lines = code.split("\n")
            all_chunks.append({
                "chunk_id": f"{rel_path}::overview",
                "file_path": rel_path,
                "start_line": 1,
                "end_line": min(len(lines), 120),
                "chunk_type": "module",
                "name": Path(rel_path).stem,
                "code": "\n".join(lines[:120]),
            })

            # Function/class chunks
            for node in nodes:
                all_chunks.append({
                    "chunk_id": f"{rel_path}::{node['name']}",
                    "file_path": rel_path,
                    "start_line": node["start_line"],
                    "end_line": node["end_line"],
                    "chunk_type": node["type"],
                    "name": node["name"],
                    "code": node["source"],
                })
        except Exception as e:
            print(f"[pipeline] Skipping {file_path}: {e}")

        if on_progress and total > 0:
            pct = int(10 + (i + 1) / total * 20)   # 10% to 30%
            on_progress("parsing", pct)

    return all_chunks


# ─────────────────────────────────────────────────────────────────────────────
# Embeddings
# ─────────────────────────────────────────────────────────────────────────────

def generate_embeddings(chunks, on_progress: Callable | None = None):
    """Embed every chunk. Returns (embeddings_array, model, model_name)."""
    from sentence_transformers import SentenceTransformer

    print(f"[pipeline] Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)
    texts = [c["code"][:EMBED_CHAR_LIMIT] for c in chunks]

    if on_progress:
        on_progress("embedding", 40)

    print(f"[pipeline] Embedding {len(texts)} chunks...")
    embeddings = model.encode(texts, show_progress_bar=True, batch_size=32)
    embeddings = np.asarray(embeddings, dtype=np.float32)

    if on_progress:
        on_progress("embedding", 75)

    return embeddings, model, EMBEDDING_MODEL


# ─────────────────────────────────────────────────────────────────────────────
# Graph
# ─────────────────────────────────────────────────────────────────────────────

def build_dependency_graph(chunks):
    """Identifier-based call graph (same as spike)."""
    import re
    ident_re = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

    graph = nx.DiGraph()
    name_to_ids = {}

    for chunk in chunks:
        if chunk["chunk_type"] in ("func", "class"):
            node_id = f"{chunk['file_path']}::{chunk['name']}"
            graph.add_node(
                node_id,
                type=chunk["chunk_type"],
                file=chunk["file_path"],
                lines=f"{chunk['start_line']}-{chunk['end_line']}",
            )
            name_to_ids.setdefault(chunk["name"], []).append(node_id)

    for chunk in chunks:
        if chunk["chunk_type"] not in ("func", "class"):
            continue
        source_id = f"{chunk['file_path']}::{chunk['name']}"
        tokens = set(ident_re.findall(chunk["code"]))
        for name, targets in name_to_ids.items():
            if name == chunk["name"] or len(name) < 3:
                continue
            if name not in tokens:
                continue
            for target_id in targets:
                if source_id != target_id:
                    graph.add_edge(source_id, target_id)

    return graph


# ─────────────────────────────────────────────────────────────────────────────
# Full Pipeline
# ─────────────────────────────────────────────────────────────────────────────

def clone_repository(repo_url: str, repo_id: str, pat: str | None = None) -> Path:
    """Clone a GitHub repo to local disk. Returns the local path."""
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    target = REPOS_DIR / repo_id

    if target.exists():
        shutil.rmtree(target)

    url = repo_url
    if pat and "github.com" in repo_url:
        url = repo_url.replace("https://", f"https://{pat}@")

    print(f"[pipeline] Cloning {repo_url} -> {target}")
    result = subprocess.run(
        ["git", "clone", "--depth=1", url, str(target)],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed: {result.stderr}")

    return target


def index_repository(
    repo_id: str,
    repo_url: str,
    on_progress: Callable[[str, int], None] | None = None,
    pat: str | None = None,
) -> dict:
    """
    Full indexing pipeline for one repository.

    on_progress(stage, percent) is called as the pipeline advances.
    Returns a summary dict.
    """
    def report(stage, pct):
        if on_progress:
            on_progress(stage, pct)

    # 1. Clone
    report("cloning", 5)
    repo_path = clone_repository(repo_url, repo_id, pat=pat)
    report("cloning", 10)

    # 2. Chunk
    chunks = chunk_repository(repo_path, on_progress=on_progress)
    report("parsing", 30)
    print(f"[pipeline] {len(chunks)} chunks produced")

    # 3. Embed
    embeddings, _model, model_name = generate_embeddings(
        chunks, on_progress=on_progress
    )
    report("embedding", 80)

    # 4. Graph
    graph = build_dependency_graph(chunks)
    report("graph", 90)

    # 5. Persist to disk
    index_dir = INDEXES_DIR / repo_id
    index_dir.mkdir(parents=True, exist_ok=True)

    chunks_meta = [{k: v for k, v in c.items() if k != "code"} for c in chunks]
    with open(index_dir / "chunks.json", "w") as f:
        json.dump(chunks_meta, f)
    with open(index_dir / "chunks_full.json", "w") as f:
        json.dump(chunks, f)   # with code — needed for the query endpoint
    np.savez_compressed(index_dir / "embeddings.npz", embeddings=embeddings)
    nx.write_graphml(graph, index_dir / "graph.graphml")

    with open(index_dir / "metadata.json", "w") as f:
        json.dump({
            "repo_id": repo_id,
            "repo_url": repo_url,
            "chunk_count": len(chunks),
            "graph_nodes": graph.number_of_nodes(),
            "graph_edges": graph.number_of_edges(),
            "embedding_model": model_name,
        }, f, indent=2)

    report("complete", 100)

    return {
        "chunk_count": len(chunks),
        "graph_nodes": graph.number_of_nodes(),
        "graph_edges": graph.number_of_edges(),
        "embedding_model": model_name,
    }