"""Canonical node-id helpers for the index, the graph, and the retriever.

Mirrors the chunker's inline chunk_id format (`<file_path>::<qualname>`, see
`_make_chunk` in `chunker.py`). The chunker isn't touched this sprint, so these
helpers are added as a shared module here; a test in `test_graph_builder.py`
verifies that for a known chunker output the two formats stay in lockstep.

Three kinds of ids:

    symbol_id(file_path, qualname)      -> "src/flask/app.py::Flask.route"
    file_id(file_path)                  -> "src/flask/app.py"
    external_id(dotted_name)            -> "external::requests"

The symbol form is byte-identical to what `chunker._make_chunk` produces, so
graph nodes JOIN cleanly against chunk ids in the index.
"""
from __future__ import annotations


def symbol_id(file_path: str, qualname: str) -> str:
    """Symbol-level node id. Matches chunker.chunk_id exactly."""
    return f"{file_path}::{qualname}"


def file_id(file_path: str) -> str:
    """File-level node id. A file as a whole, used for `imports` edges."""
    return file_path


def external_id(dotted_name: str) -> str:
    """Node id for a symbol / module outside the indexed repo."""
    return f"external::{dotted_name}"


def is_external(node_id: str) -> bool:
    return node_id.startswith("external::")


def is_file_node(node_id: str) -> bool:
    """A file node has no `::` separator (and isn't external)."""
    return "::" not in node_id
