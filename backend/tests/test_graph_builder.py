"""Tests for the AST-based graph builder.

Covers the invariants from the sprint-2 spec:

  - node ids are unique
  - symbol node ids use the chunker's chunk_id format
  - every edge carries edge_type, confidence, source
  - no edge points at a non-existent node
  - reachability terminates on mutual recursion
  - entry-point detection finds a small module's public API
  - running twice is deterministic
  - PageRank sums to ~1.0 over the call-graph population only

The chunker is NOT a dependency of these tests — we build tiny repos inside
`tmp_path` and only need the chunker's inline `<file_path>::<qualname>`
convention, which we mirror via `backend.indexing.ids.symbol_id`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.indexing.graph_builder import (
    build_dependency_graph,
    build_import_edges,
    build_call_edges,
    build_inherits_edges,
    build_decorator_edges,
    _build_class_symbol_table,
    reachable_from_entry_points,
    _run_pagerank_on_call_graph,
    _file_dotted_variants,
    _strip_module_prefix,
    GRAPH_BUILDER_VERSION,
)
from backend.indexing.ids import symbol_id, file_id


# ─────────────────────────────────────────────────────────────────────────────
# Tiny repo fixtures
# ─────────────────────────────────────────────────────────────────────────────

SIMPLE_APP = {
    "pkg/__init__.py": 'from .core import run\n__all__ = ["run"]\n',
    "pkg/core.py": (
        "from .util import helper\n"
        "\n"
        "class Service:\n"
        "    def __init__(self):\n"
        "        self.x = 0\n"
        "    def run(self):\n"
        "        return helper(self.x)\n"
        "\n"
        "def run():\n"
        "    return Service().run()\n"
    ),
    "pkg/util.py": "def helper(n):\n    return n + 1\n",
    "tests/test_core.py": (
        "from pkg.core import Service\n"
        "def test_service():\n"
        "    assert Service().run() == 1\n"
    ),
}

MUTUAL_RECURSION = {
    "pkg/__init__.py": "",
    "pkg/rec.py": (
        "def a(n):\n"
        "    if n <= 0: return 0\n"
        "    return b(n - 1)\n"
        "def b(n):\n"
        "    if n <= 0: return 1\n"
        "    return a(n - 1)\n"
    ),
}

INHERITS_AND_DECO = {
    "pkg/__init__.py": "",
    "pkg/base.py": "class Base:\n    pass\n",
    "pkg/derived.py": (
        "from .base import Base\n"
        "def my_dec(f):\n"
        "    return f\n"
        "class Mid(Base):\n"
        "    @my_dec\n"
        "    def go(self):\n"
        "        return 1\n"
    ),
}


def _write_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return root


def _chunks_for(root: Path, files: dict[str, str]) -> list[dict]:
    """Minimal chunk list the builder needs — only chunk_type / file_path /
    qualname / name / start_line / end_line are read."""
    import ast
    chunks = []
    for rel, body in files.items():
        try:
            tree = ast.parse(body)
        except SyntaxError:
            continue
        def walk(node, parent_qn=""):
            for stmt in getattr(node, "body", []):
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qn = f"{parent_qn}.{stmt.name}" if parent_qn else stmt.name
                    chunks.append({
                        "chunk_type": "func", "file_path": rel,
                        "qualname": qn, "name": stmt.name,
                        "start_line": stmt.lineno, "end_line": stmt.end_lineno,
                    })
                    walk(stmt, qn)
                elif isinstance(stmt, ast.ClassDef):
                    qn = f"{parent_qn}.{stmt.name}" if parent_qn else stmt.name
                    chunks.append({
                        "chunk_type": "class", "file_path": rel,
                        "qualname": qn, "name": stmt.name,
                        "start_line": stmt.lineno, "end_line": stmt.end_lineno,
                    })
                    walk(stmt, qn)
        walk(tree)
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Pure-function unit tests
# ─────────────────────────────────────────────────────────────────────────────

def test_file_dotted_variants_strips_src_layout():
    v = _file_dotted_variants("src/flask/app.py", ["src"])
    assert ["flask", "app"] in v
    assert ["src", "flask", "app"] in v


def test_strip_module_prefix_handles_both_jedi_conventions():
    v = _file_dotted_variants("src/flask/sansio/blueprints.py", ["src"])
    # Jedi with src-prefix
    assert _strip_module_prefix("src.flask.sansio.blueprints.Blueprint.record", v) \
        == "Blueprint.record"
    # Jedi without src-prefix
    assert _strip_module_prefix("flask.sansio.blueprints.Blueprint.record", v) \
        == "Blueprint.record"
    # Unmatchable prefix returns "" (caller falls back)
    assert _strip_module_prefix("totally.unrelated.X", v) == ""


# ─────────────────────────────────────────────────────────────────────────────
# Phase-1 tests (imports, no Jedi)
# ─────────────────────────────────────────────────────────────────────────────

def test_import_edges_resolve_relative_and_mark_external(tmp_path: Path):
    repo = _write_repo(tmp_path, SIMPLE_APP)
    edges, attrs = build_import_edges(repo)
    srcs_tgts = {(e.src, e.tgt, e.edge_type) for e in edges}
    # pkg/__init__.py -> pkg/core.py (relative import)
    assert ("pkg/__init__.py", "pkg/core.py", "imports") in srcs_tgts
    # pkg/core.py -> pkg/util.py (relative import)
    assert ("pkg/core.py", "pkg/util.py", "imports") in srcs_tgts
    # every edge carries the right metadata
    for e in edges:
        assert e.edge_type == "imports"
        assert e.confidence == "extracted"
        assert e.source == "static"


# ─────────────────────────────────────────────────────────────────────────────
# Phase-4 tests (calls, uses Jedi)
# ─────────────────────────────────────────────────────────────────────────────

def test_call_edges_connect_caller_to_callee(tmp_path: Path):
    repo = _write_repo(tmp_path, SIMPLE_APP)
    edges, stats = build_call_edges(repo)
    pairs = {(e.src, e.tgt) for e in edges}
    # pkg.core.run -> pkg.core.Service (constructor call)
    assert any(
        e.src == symbol_id("pkg/core.py", "run") and
        e.tgt == symbol_id("pkg/core.py", "Service")
        for e in edges
    ), f"got pairs: {pairs}"
    # tests.test_core.test_service -> pkg.core.Service
    assert any(
        e.src == symbol_id("tests/test_core.py", "test_service")
        for e in edges
    )
    # Every edge has the full attribute set.
    for e in edges:
        assert e.edge_type == "calls"
        assert e.confidence in ("extracted", "ambiguous")
        assert e.source == "static"
    # Resolution budget sanity: at least half the sites resolved.
    assert stats["resolved_sites"] >= stats["total_sites"] // 2


# ─────────────────────────────────────────────────────────────────────────────
# Phase-5 tests (inherits + decorates)
# ─────────────────────────────────────────────────────────────────────────────

def test_inherits_and_decorates_are_captured(tmp_path: Path):
    repo = _write_repo(tmp_path, INHERITS_AND_DECO)
    table = _build_class_symbol_table(repo)
    inh = build_inherits_edges(repo, table)
    dec = build_decorator_edges(repo)

    assert any(
        e.src == symbol_id("pkg/derived.py", "Mid") and
        e.tgt == symbol_id("pkg/base.py", "Base") and
        e.edge_type == "inherits"
        for e in inh
    )
    assert any(
        e.edge_type == "decorates" and
        e.tgt == symbol_id("pkg/derived.py", "Mid.go")
        for e in dec
    )


# ─────────────────────────────────────────────────────────────────────────────
# Full-graph invariants
# ─────────────────────────────────────────────────────────────────────────────

def test_graph_invariants_simple_app(tmp_path: Path):
    repo = _write_repo(tmp_path, SIMPLE_APP)
    chunks = _chunks_for(repo, SIMPLE_APP)
    g = build_dependency_graph(chunks, repo_path=repo)

    # Unique node ids
    assert len(g.nodes) == len(set(g.nodes))

    # Symbol nodes use the chunker's chunk_id format (file::qualname)
    for n, d in g.nodes(data=True):
        if d.get("node_type") in ("func", "class"):
            assert "::" in n
            assert n == symbol_id(d["file"], d["qualname"])

    # Every edge carries edge_type / confidence / source
    for u, v, data in g.edges(data=True):
        assert "edge_type" in data
        assert "confidence" in data
        assert "source" in data

    # No dangling edges — both endpoints exist
    nodes = set(g.nodes)
    for u, v in g.edges():
        assert u in nodes, f"edge src missing: {u}"
        assert v in nodes, f"edge tgt missing: {v}"


def test_reachability_terminates_on_mutual_recursion(tmp_path: Path):
    repo = _write_repo(tmp_path, MUTUAL_RECURSION)
    chunks = _chunks_for(repo, MUTUAL_RECURSION)
    g = build_dependency_graph(chunks, repo_path=repo)
    # Force an entry point and ask about a node reachable through recursion.
    a_id = symbol_id("pkg/rec.py", "a")
    b_id = symbol_id("pkg/rec.py", "b")
    assert a_id in g and b_id in g
    g.nodes[a_id]["is_entry_point"] = True
    # Should return a bool quickly, not hang.
    assert reachable_from_entry_points(g, b_id) is True


def test_entry_point_detection_finds_public_api(tmp_path: Path):
    repo = _write_repo(tmp_path, SIMPLE_APP)
    chunks = _chunks_for(repo, SIMPLE_APP)
    g = build_dependency_graph(chunks, repo_path=repo)
    run_id = symbol_id("pkg/core.py", "run")
    assert run_id in g
    assert g.nodes[run_id]["is_entry_point"] is True


def test_deterministic_on_repeat(tmp_path: Path):
    repo = _write_repo(tmp_path, SIMPLE_APP)
    chunks = _chunks_for(repo, SIMPLE_APP)
    g1 = build_dependency_graph(chunks, repo_path=repo)
    g2 = build_dependency_graph(chunks, repo_path=repo)
    assert set(g1.nodes) == set(g2.nodes)
    assert set(g1.edges()) == set(g2.edges())


def test_pagerank_sums_to_approximately_one(tmp_path: Path):
    repo = _write_repo(tmp_path, SIMPLE_APP)
    chunks = _chunks_for(repo, SIMPLE_APP)
    g = build_dependency_graph(chunks, repo_path=repo)
    pr = _run_pagerank_on_call_graph(g)
    if pr:
        assert abs(sum(pr.values()) - 1.0) < 1e-6
    # Non-call nodes (file / external) must NOT have a non-zero pagerank:
    # PageRank is run on the call subgraph only.
    for n, d in g.nodes(data=True):
        if d.get("node_type") in ("file", "external"):
            assert d.get("pagerank", 0.0) == 0.0


def test_graph_carries_version_and_edge_direction_doc(tmp_path: Path):
    repo = _write_repo(tmp_path, SIMPLE_APP)
    chunks = _chunks_for(repo, SIMPLE_APP)
    g = build_dependency_graph(chunks, repo_path=repo)
    assert g.graph["graph_builder_version"] == GRAPH_BUILDER_VERSION
    assert "A -> B" in g.graph["edge_direction_doc"]


def test_build_dependency_graph_without_repo_path_returns_empty(capsys):
    g = build_dependency_graph([], repo_path=None)
    assert g.number_of_nodes() == 0
    assert g.number_of_edges() == 0
    assert g.graph["graph_builder_version"] == GRAPH_BUILDER_VERSION
    err = capsys.readouterr().err
    assert "WARNING" in err
