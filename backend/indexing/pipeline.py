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

from .chunker import CHUNKERS
from .graph_builder import (
    build_dependency_graph as ast_build_dependency_graph,
    GRAPH_BUILDER_VERSION as AST_GRAPH_BUILDER_VERSION,
)


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
REPOS_DIR = DATA_DIR / "repos"
INDEXES_DIR = DATA_DIR / "indexes"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"   # Sprint 1 only; Sprint 2 will upgrade
CHUNKER_NAME = os.environ.get("CODESENSE_CHUNKER", "function_level")
GRAPH_BUILDER_VERSION = AST_GRAPH_BUILDER_VERSION
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


def chunk_repository(
    repo_path: Path,
    tokenizer,
    budget: int,
    chunker_name: str = CHUNKER_NAME,
    on_progress: Callable | None = None,
):
    """Walk the repository and produce chunks via the configured chunker.

    Returns (chunks, skipped_files). Each skipped entry is {file, reason}.
    """
    chunker = CHUNKERS.get(chunker_name)
    if chunker is None:
        raise ValueError(
            f"Unknown chunker {chunker_name!r}; valid: {sorted(CHUNKERS)}"
        )
    chunks, skipped = chunker(repo_path, tokenizer, budget, on_progress=on_progress)
    return chunks, skipped


# ─────────────────────────────────────────────────────────────────────────────
# Embeddings
# ─────────────────────────────────────────────────────────────────────────────

def generate_embeddings(chunks, model, on_progress: Callable | None = None):
    """Embed every chunk's header-prefixed embed_text. Returns the embeddings array."""
    texts = [c["embed_text"] for c in chunks]

    if on_progress:
        on_progress("embedding", 40)

    print(f"[pipeline] Embedding {len(texts)} chunks...")
    embeddings = model.encode(texts, show_progress_bar=True, batch_size=32)
    embeddings = np.asarray(embeddings, dtype=np.float32)

    if on_progress:
        on_progress("embedding", 75)

    return embeddings


def _load_embedding_model():
    from sentence_transformers import SentenceTransformer
    print(f"[pipeline] Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL)
    return model


# ─────────────────────────────────────────────────────────────────────────────
# Graph
# ─────────────────────────────────────────────────────────────────────────────

def build_dependency_graph(chunks, repo_path=None, trace_file=None):
    """AST-based dependency graph. Delegates to `graph_builder.py`.

    Signature extended with `repo_path` (optional, required for real analysis)
    and `trace_file` (optional, overlays dynamic call edges). Old callers that
    pass only `chunks` get an empty graph with the builder version stamped,
    and a warning on stderr.
    """
    return ast_build_dependency_graph(chunks, repo_path=repo_path, trace_file=trace_file)


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

    # 2. Load embedding model (needed up-front so the chunker shares its tokenizer
    #    and token budget with the embedder).
    model = _load_embedding_model()
    tokenizer = model.tokenizer
    budget = int(model.max_seq_length)

    # 3. Chunk
    chunks, skipped_files = chunk_repository(
        repo_path, tokenizer, budget,
        chunker_name=CHUNKER_NAME,
        on_progress=on_progress,
    )
    report("parsing", 30)
    print(f"[pipeline] {len(chunks)} chunks produced via {CHUNKER_NAME!r}")
    if skipped_files:
        print(f"[pipeline] skipped {len(skipped_files)} files")

    # 4. Embed
    embeddings = generate_embeddings(chunks, model, on_progress=on_progress)
    report("embedding", 80)

    # 5. Graph
    graph = build_dependency_graph(chunks, repo_path=repo_path)
    report("graph", 90)

    # 6. Persist to disk (chunks.json was unused — dropped).
    index_dir = INDEXES_DIR / repo_id
    index_dir.mkdir(parents=True, exist_ok=True)

    with open(index_dir / "chunks_full.json", "w") as f:
        json.dump(chunks, f)   # with code — needed for the query endpoint
    np.savez_compressed(index_dir / "embeddings.npz", embeddings=embeddings)

    # graphml cannot serialize Counter / dict graph-level attrs, so pop them
    # out and fold them into metadata.json. The graph itself only carries
    # scalar attrs on nodes/edges.
    graph_stats = {
        "edge_counts_by_type":       dict(graph.graph.pop("edge_counts_by_type", {})),
        "edge_counts_by_source":     dict(graph.graph.pop("edge_counts_by_source", {})),
        "edge_counts_by_confidence": dict(graph.graph.pop("edge_counts_by_confidence", {})),
        "node_counts_by_tag":        graph.graph.pop("node_counts_by_tag", {}),
        "call_phase_stats":          graph.graph.pop("call_phase_stats", {}),
    }
    nx.write_graphml(graph, index_dir / "graph.graphml")

    with open(index_dir / "metadata.json", "w") as f:
        json.dump({
            "repo_id": repo_id,
            "repo_url": repo_url,
            "chunk_count": len(chunks),
            "graph_nodes": graph.number_of_nodes(),
            "graph_edges": graph.number_of_edges(),
            "embedding_model": EMBEDDING_MODEL,
            "chunker_name": CHUNKER_NAME,
            "token_budget": budget,
            "graph_builder_version": GRAPH_BUILDER_VERSION,
            "graph_stats": graph_stats,
            "skipped_files": skipped_files,
        }, f, indent=2)

    report("complete", 100)

    return {
        "chunk_count": len(chunks),
        "graph_nodes": graph.number_of_nodes(),
        "graph_edges": graph.number_of_edges(),
        "embedding_model": EMBEDDING_MODEL,
        "chunker_name": CHUNKER_NAME,
        "skipped_files": skipped_files,
    }