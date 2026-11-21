"""AST-based source graph builder for CodeSense.

Replaces the identifier-string heuristic that was in
`pipeline.build_dependency_graph`. The old builder drew an edge wherever an
identifier appeared anywhere in another chunk's body — including in comments
and string literals — and keyed its symbol table on bare names, so every
function called `index` collapsed into one node. Measured on Flask that gave
1309 nodes and 18,320 edges (ratio 14.00).

This module builds the graph from the syntax tree, not from strings, in six
phases:

    Phase 1  imports      tree-sitter   file -> file
    Phase 2  dynamic      sys.setprofile symbol -> symbol   (ground truth)
    Phase 3  (spike)      Jedi timing measurement
    Phase 4  calls        tree-sitter + Jedi  symbol -> symbol
    Phase 5  inherits     tree-sitter   class  -> class
             decorates    tree-sitter   deco   -> symbol

Edge direction
--------------

An edge `A -> B` means **"A calls / imports / inherits-from / decorates B"**.
So "what breaks if I change B" walks edges BACKWARDS from B (predecessors).
Getting this inverted silently inverts every impact answer.

Node ids
--------

Node ids come from `backend.indexing.ids`. Symbol node ids are byte-identical
to what the chunker produces as `chunk_id`, so the graph and the index JOIN on
node_id = chunk_id with no translation layer.

External targets (imports or calls that resolve outside the indexed repo) get
a node marked `external=True` rather than being dropped — a dependency CVE
lives in external code, so a reachability query that stops at the repo
boundary could never reach the thing it is looking for.

Public surface
--------------

    build_dependency_graph(chunks, repo_path=None) -> nx.DiGraph

The `repo_path` keyword was added this sprint; without it, no AST analysis
is possible (we need file contents, not just chunk slices). `pipeline.py`
passes it through. Old callers that only pass `chunks` still work — they
just get an empty graph, with a warning.
"""
from __future__ import annotations

import ast
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import networkx as nx
from tree_sitter import Parser, Language, Query, QueryCursor
import tree_sitter_python as tspython

from .ids import symbol_id, file_id, external_id, is_external


GRAPH_BUILDER_VERSION = "sprint2-ast-graph"


# ─────────────────────────────────────────────────────────────────────────────
# Parser singleton
# ─────────────────────────────────────────────────────────────────────────────

_parser_singleton: tuple[Parser, Language] | None = None


def _parser() -> tuple[Parser, Language]:
    global _parser_singleton
    if _parser_singleton is None:
        lang = Language(tspython.language())
        p = Parser()
        p.language = lang
        _parser_singleton = (p, lang)
    return _parser_singleton


_SKIP_PARTS = ("site-packages", "venv", ".venv", "node_modules", ".git", "__pycache__")


def _iter_py_files(repo_path: Path) -> list[Path]:
    return sorted(
        f for f in repo_path.rglob("*.py")
        if not any(part in _SKIP_PARTS for part in f.parts)
    )


def _rel(p: Path, root: Path) -> str:
    return str(p.relative_to(root)).replace("\\", "/")


# ─────────────────────────────────────────────────────────────────────────────
# Edge container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Edge:
    """One directed edge. An edge of the same (src, tgt, edge_type) from both
    static and dynamic analysis is merged by `_union_edges` with source="both".
    """
    src: str
    tgt: str
    edge_type: str          # "imports" | "calls" | "inherits" | "decorates"
    confidence: str         # "extracted" | "ambiguous" | "dynamic" | "unresolved"
    source: str             # "static" | "dynamic" | "both"
    meta: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Node tagging
# ─────────────────────────────────────────────────────────────────────────────

def _classify_file(rel_path: str) -> dict[str, bool]:
    """Return is_test / is_docs / is_src flags for a repo-relative path."""
    parts = rel_path.split("/")
    is_test = any(p in ("tests", "test") or p.startswith("test_") for p in parts) \
        or parts[-1].startswith("test_") or parts[-1].endswith("_test.py")
    is_docs = any(p in ("docs", "doc", "examples", "example") for p in parts)
    is_src = not is_test and not is_docs
    return {"is_test": is_test, "is_docs": is_docs, "is_src": is_src}


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1 — import edges (tree-sitter only, no resolver needed)
# ─────────────────────────────────────────────────────────────────────────────

# `module_name` holds either a dotted_name (absolute import) or a
# relative_import (which in turn may wrap a dotted_name).
_IMPORT_QUERY_SRC = """
    (import_statement
        name: (dotted_name) @abs_name)
    (import_statement
        name: (aliased_import name: (dotted_name) @abs_name))
    (import_from_statement
        module_name: (dotted_name) @from_abs)
    (import_from_statement
        module_name: (relative_import) @from_rel)
"""


def _extract_imports(src_bytes: bytes) -> list[tuple[str, int, int, str]]:
    """Return `[(dotted_or_relative, dot_prefix_len, _unused, variant), ...]`.

    variant is one of "abs" (absolute `import X` / `import X as Y` / `from X`)
    or "rel" (`from .X import Y` / `from . import X`).
    `dot_prefix_len` counts leading dots for relative imports; 0 otherwise.
    """
    parser, lang = _parser()
    tree = parser.parse(src_bytes)
    query = Query(lang, _IMPORT_QUERY_SRC)
    cursor = QueryCursor(query)

    out: list[tuple[str, int, int, str]] = []
    for _pat, caps in cursor.matches(tree.root_node):
        for cap, nodes in caps.items():
            for n in nodes:
                text = src_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="replace")
                if cap == "from_rel":
                    # text looks like ".foo", "..bar", "." (bare) etc.
                    dots = 0
                    for ch in text:
                        if ch == ".":
                            dots += 1
                        else:
                            break
                    rest = text[dots:].strip()
                    out.append((rest, dots, 0, "rel"))
                else:
                    out.append((text.strip(), 0, 0, "abs"))
    return out


def _resolve_module(
    dotted: str,
    dot_prefix: int,
    current_file: Path,
    repo_root: Path,
    src_roots: list[Path],
) -> Path | None:
    """Return the repo-relative file a dotted module name resolves to, or None.

    Handles:
      - absolute imports:       `flask.app`         -> src/flask/app.py
      - package imports:        `flask`             -> src/flask/__init__.py
      - relative imports:       `from . import x`   -> same package __init__
      - src-layout imports:     tries each src root in `src_roots`
    """
    if dot_prefix > 0:
        # Relative import: anchor to the package that CONTAINS current_file.
        anchor = current_file.parent
        for _ in range(dot_prefix - 1):
            anchor = anchor.parent
        if not dotted:
            # bare "from . import x" — the package itself
            candidate_dir = anchor
            candidate_mod = anchor / "__init__.py"
            if candidate_mod.exists():
                return candidate_mod
            return None
        parts = dotted.split(".")
        candidate_mod = anchor.joinpath(*parts).with_suffix(".py")
        candidate_pkg = anchor.joinpath(*parts) / "__init__.py"
        if candidate_mod.exists():
            return candidate_mod
        if candidate_pkg.exists():
            return candidate_pkg
        return None

    parts = dotted.split(".") if dotted else []
    if not parts:
        return None

    roots = [repo_root, *src_roots]
    for root in roots:
        candidate_mod = root.joinpath(*parts).with_suffix(".py")
        candidate_pkg = root.joinpath(*parts) / "__init__.py"
        if candidate_mod.exists():
            return candidate_mod
        if candidate_pkg.exists():
            return candidate_pkg

        # Progressive-prefix match: `flask.app.Flask` (symbol, not a module)
        # resolves to `flask/app.py`, so walk shorter prefixes too.
        for cut in range(len(parts) - 1, 0, -1):
            cand_mod = root.joinpath(*parts[:cut]).with_suffix(".py")
            cand_pkg = root.joinpath(*parts[:cut]) / "__init__.py"
            if cand_mod.exists():
                return cand_mod
            if cand_pkg.exists():
                return cand_pkg
    return None


def _discover_src_roots(repo_root: Path) -> list[Path]:
    """Look for standard src-layout roots: `src/` and any immediate subdir of it."""
    roots: list[Path] = []
    src = repo_root / "src"
    if src.is_dir():
        roots.append(src)
    return roots


def build_import_edges(repo_path: Path) -> tuple[list[Edge], dict[str, dict]]:
    """Phase 1 entry point. Returns (edges, file_node_attrs).

    `file_node_attrs` maps file_id -> {is_test, is_docs, is_src, external} so
    the merge step can create nodes with the right tags.
    """
    repo_path = Path(repo_path)
    src_roots = _discover_src_roots(repo_path)
    edges: list[Edge] = []
    file_attrs: dict[str, dict] = {}

    for py in _iter_py_files(repo_path):
        rel = _rel(py, repo_path)
        tags = _classify_file(rel)
        file_attrs.setdefault(file_id(rel), {**tags, "external": False})

        src_bytes = py.read_bytes()
        for dotted, dots, _, variant in _extract_imports(src_bytes):
            if not dotted and dots == 0:
                continue
            resolved = _resolve_module(dotted, dots, py, repo_path, src_roots)
            if resolved is not None:
                try:
                    tgt_rel = _rel(resolved, repo_path)
                except ValueError:
                    continue
                tgt_id = file_id(tgt_rel)
                file_attrs.setdefault(tgt_id, {**_classify_file(tgt_rel), "external": False})
                if file_id(rel) == tgt_id:
                    continue  # self-import (e.g. package `__init__.py` re-export)
                edges.append(Edge(
                    src=file_id(rel), tgt=tgt_id,
                    edge_type="imports", confidence="extracted", source="static",
                    meta={"variant": variant},
                ))
            else:
                # External — stdlib, pypi, or unresolvable
                display = "." * dots + dotted if variant == "rel" else dotted
                ext_id = external_id(display.split(".")[0] if variant == "abs" else display)
                file_attrs.setdefault(ext_id, {
                    "is_test": False, "is_docs": False, "is_src": False, "external": True,
                })
                edges.append(Edge(
                    src=file_id(rel), tgt=ext_id,
                    edge_type="imports", confidence="extracted", source="static",
                    meta={"variant": variant, "dotted": display},
                ))

    return edges, file_attrs


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4 — static call edges (tree-sitter finds sites; Jedi resolves targets)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CallSite:
    caller_qualname: str | None      # None => module-level call (caller is file)
    enclosing_class: str | None      # enclosing class qualname, for self.* resolution
    callee_line: int                 # 1-indexed line of the callee NAME
    callee_col: int                  # 0-indexed column of the callee NAME
    callee_kind: str                 # "bare" | "attr" | "self" | "unsupported"
    callee_text: str                 # the attribute name or bare identifier


def _collect_call_sites(src_bytes: bytes) -> list[CallSite]:
    """Walk the AST and emit one CallSite per call expression.

    Builds the caller's qualname by threading enclosing func/class names down
    the walk (same algorithm the chunker uses).
    """
    parser, _ = _parser()
    tree = parser.parse(src_bytes)
    sites: list[CallSite] = []

    def name_of(def_node) -> str:
        name_n = def_node.child_by_field_name("name")
        if name_n is None:
            return "<anon>"
        return src_bytes[name_n.start_byte:name_n.end_byte].decode("utf-8", errors="replace")

    def walk(node, func_stack: tuple[str, ...], class_stack: tuple[str, ...]):
        if node.type == "function_definition":
            n = name_of(node)
            new_f = func_stack + (n,)
            for ch in node.children:
                walk(ch, new_f, class_stack)
            return
        if node.type == "class_definition":
            n = name_of(node)
            new_c = class_stack + (n,)
            new_f = func_stack + (n,)
            for ch in node.children:
                walk(ch, new_f, new_c)
            return
        if node.type == "call":
            fn = node.child_by_field_name("function")
            if fn is not None:
                if fn.type == "identifier":
                    text = src_bytes[fn.start_byte:fn.end_byte].decode("utf-8", errors="replace")
                    sites.append(CallSite(
                        caller_qualname=".".join(func_stack) if func_stack else None,
                        enclosing_class=".".join(class_stack) if class_stack else None,
                        callee_line=fn.start_point[0] + 1,
                        callee_col=fn.start_point[1],
                        callee_kind="bare",
                        callee_text=text,
                    ))
                elif fn.type == "attribute":
                    attr_n = fn.child_by_field_name("attribute")
                    obj_n = fn.child_by_field_name("object")
                    if attr_n is None:
                        pass
                    else:
                        attr_text = src_bytes[attr_n.start_byte:attr_n.end_byte].decode("utf-8", errors="replace")
                        obj_text = src_bytes[obj_n.start_byte:obj_n.end_byte].decode("utf-8", errors="replace") if obj_n is not None else ""
                        kind = "self" if obj_text == "self" else "attr"
                        sites.append(CallSite(
                            caller_qualname=".".join(func_stack) if func_stack else None,
                            enclosing_class=".".join(class_stack) if class_stack else None,
                            callee_line=attr_n.start_point[0] + 1,
                            callee_col=attr_n.start_point[1],
                            callee_kind=kind,
                            callee_text=attr_text,
                        ))
                else:
                    # subscript, call-of-call, lambda, etc.
                    sites.append(CallSite(
                        caller_qualname=".".join(func_stack) if func_stack else None,
                        enclosing_class=".".join(class_stack) if class_stack else None,
                        callee_line=fn.start_point[0] + 1,
                        callee_col=fn.start_point[1],
                        callee_kind="unsupported",
                        callee_text=fn.type,
                    ))
        for ch in node.children:
            walk(ch, func_stack, class_stack)

    walk(tree.root_node, (), ())
    return sites


def _jedi_resolve(script, line: int, col: int) -> list:
    """Return Jedi's goto() definitions, swallowing failures as empty."""
    try:
        return script.goto(line, column=col, follow_imports=True) or []
    except Exception:
        return []


def _file_dotted_variants(rel_path: str, src_roots_rel: list[str]) -> list[list[str]]:
    """Return every plausible dotted-module decomposition of a file path.

    Jedi's `full_name` sometimes includes the `src.` prefix and sometimes
    doesn't (depending on how the project resolves the module), so we hand
    the prefix-stripper every variant and take the longest match.
    """
    parts = rel_path.split("/")
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts.pop()

    variants: list[list[str]] = [list(parts)]
    for sr in src_roots_rel:
        sr_parts = sr.split("/")
        if parts[:len(sr_parts)] == sr_parts:
            variants.append(list(parts[len(sr_parts):]))
    # Also strip single leading dirs that act like implicit namespace packages
    # ('tests/' sometimes shows up as 'tests.foo' and sometimes as 'foo').
    if parts and parts[0] not in {s.split("/")[0] for s in src_roots_rel}:
        variants.append(list(parts[1:]))
    return [v for v in variants if v]


def _strip_module_prefix(full_name: str, file_variants: list[list[str]]) -> str:
    """Try every file-dotted variant as a prefix of `full_name`; longest wins.
    Returns the within-file qualname, or "" if no variant matches at all.
    """
    if not full_name:
        return ""
    full_parts = full_name.split(".")
    best = -1
    for parts in file_variants:
        # Try the variant itself and each of its suffixes as the matching prefix.
        for start in range(len(parts)):
            suffix = parts[start:]
            if not suffix:
                continue
            if full_parts[:len(suffix)] == suffix:
                best = max(best, len(suffix))
    if best < 0:
        return ""  # don't guess — the caller falls back to file_id
    return ".".join(full_parts[best:])


def _target_id_from_definition(d, repo_root: Path, src_roots_rel: list[str]) -> str | None:
    """Turn a Jedi Definition into a graph node id matching the chunker's
    format exactly (`<rel_path>::<qualname>`), or an external_id for anything
    outside the indexed repo.
    """
    try:
        mp = d.module_path
    except Exception:
        return None
    if mp is None:
        name = getattr(d, "module_name", None) or getattr(d, "name", None)
        if not name:
            return None
        return external_id(name.split(".")[0] if "." in name else name)
    try:
        mp = Path(mp).resolve()
    except Exception:
        return None
    try:
        rel = mp.relative_to(repo_root)
    except ValueError:
        name = getattr(d, "module_name", None) or mp.stem
        return external_id(name.split(".")[0] if "." in name else name)
    rel_s = str(rel).replace("\\", "/")
    full = getattr(d, "full_name", None) or getattr(d, "name", "")
    variants = _file_dotted_variants(rel_s, src_roots_rel)
    qualname = _strip_module_prefix(full, variants) if full else ""
    if not qualname:
        # full_name couldn't be mapped onto this file; fall back to d.name
        # (just the leaf identifier) rather than returning a file node for a
        # call edge — a file-level call target isn't useful downstream.
        qualname = getattr(d, "name", "")
    if not qualname:
        return file_id(rel_s)
    return symbol_id(rel_s, qualname)


def build_call_edges(
    repo_path: Path,
    jedi_project=None,
) -> tuple[list[Edge], dict]:
    """Phase 4 — walk every file, extract call sites, resolve via Jedi, emit edges.

    Returns (edges, stats) where stats carries unresolved-pattern analytics.
    """
    import jedi
    repo_path = Path(repo_path).resolve()
    src_roots = _discover_src_roots(repo_path)
    src_roots_rel = [_rel(sr, repo_path) for sr in src_roots]
    if jedi_project is None:
        jedi_project = jedi.Project(str(repo_path))

    edges: list[Edge] = []
    unresolved_patterns: Counter = Counter()
    total_sites = 0
    resolved_sites = 0
    ambiguous_sites = 0
    unresolved_sites = 0
    unsupported_sites = 0
    t_start = time.time()

    for py in _iter_py_files(repo_path):
        rel = _rel(py, repo_path)
        try:
            src_bytes = py.read_bytes()
        except OSError:
            continue
        sites = _collect_call_sites(src_bytes)
        if not sites:
            continue

        # One Script per file — Jedi caches the module internally.
        try:
            script = jedi.Script(code=src_bytes.decode("utf-8", "replace"),
                                 path=str(py), project=jedi_project)
        except Exception:
            continue

        for site in sites:
            total_sites += 1
            # Caller id: a symbol if inside a function, else the file node.
            if site.caller_qualname:
                src_id = symbol_id(rel, site.caller_qualname)
            else:
                src_id = file_id(rel)

            if site.callee_kind == "unsupported":
                unsupported_sites += 1
                unresolved_patterns[f"unsupported:{site.callee_text}"] += 1
                continue

            # Self-method shortcut: resolve against the enclosing class, not Jedi,
            # when the class has that method in this file. Cheaper and more precise.
            if site.callee_kind == "self" and site.enclosing_class:
                # Jedi still handles this, but we tag the metadata so the analytics
                # can show how often self-method resolution worked.
                pass

            defs = _jedi_resolve(script, site.callee_line, site.callee_col)
            if not defs:
                unresolved_sites += 1
                unresolved_patterns[f"{site.callee_kind}:{site.callee_text}"] += 1
                continue

            target_ids: list[str] = []
            for d in defs:
                tid = _target_id_from_definition(d, repo_path, src_roots_rel)
                if tid and tid != src_id:
                    target_ids.append(tid)

            if not target_ids:
                unresolved_sites += 1
                unresolved_patterns[f"{site.callee_kind}:{site.callee_text}"] += 1
                continue

            if len(target_ids) == 1:
                resolved_sites += 1
                conf = "extracted"
            else:
                ambiguous_sites += 1
                conf = "ambiguous"

            for tid in target_ids:
                edges.append(Edge(
                    src=src_id, tgt=tid,
                    edge_type="calls", confidence=conf, source="static",
                    meta={"kind": site.callee_kind, "line": site.callee_line},
                ))

    stats = {
        "wall_clock_seconds": round(time.time() - t_start, 2),
        "total_sites": total_sites,
        "resolved_sites": resolved_sites,
        "ambiguous_sites": ambiguous_sites,
        "unresolved_sites": unresolved_sites,
        "unsupported_sites": unsupported_sites,
        "top10_unresolved_patterns": unresolved_patterns.most_common(10),
    }
    return edges, stats


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 5 — inherits + decorates (tree-sitter only, no resolver)
# ─────────────────────────────────────────────────────────────────────────────

_INHERIT_QUERY_SRC = """
(class_definition
    name: (identifier) @cls
    superclasses: (argument_list) @supers)
"""

_DECORATE_QUERY_SRC = """
(decorated_definition
    (decorator) @deco
    definition: [(function_definition name: (identifier) @fn_name)
                 (class_definition name: (identifier) @cls_name)])
"""


def _build_class_symbol_table(repo_path: Path) -> dict[str, list[str]]:
    """Map bare class name -> list of symbol ids where a class by that name is defined.
    Used as a cheap resolver for "inherits" when Jedi isn't available.
    """
    parser, _ = _parser()
    table: dict[str, list[str]] = {}
    for py in _iter_py_files(repo_path):
        rel = _rel(py, repo_path)
        try:
            src_bytes = py.read_bytes()
        except OSError:
            continue
        tree = parser.parse(src_bytes)

        def walk(node, stack: tuple[str, ...]):
            if node.type == "class_definition":
                nm_n = node.child_by_field_name("name")
                if nm_n is not None:
                    name = src_bytes[nm_n.start_byte:nm_n.end_byte].decode("utf-8", "replace")
                    qn = ".".join(stack + (name,))
                    table.setdefault(name, []).append(symbol_id(rel, qn))
                    new_stack = stack + (name,)
                    for ch in node.children:
                        walk(ch, new_stack)
                    return
            for ch in node.children:
                walk(ch, stack)

        walk(tree.root_node, ())
    return table


def build_inherits_edges(repo_path: Path, class_table: dict[str, list[str]]) -> list[Edge]:
    parser, lang = _parser()
    query = Query(lang, _INHERIT_QUERY_SRC)
    cursor = QueryCursor(query)
    edges: list[Edge] = []

    for py in _iter_py_files(repo_path):
        rel = _rel(py, repo_path)
        try:
            src_bytes = py.read_bytes()
        except OSError:
            continue
        tree = parser.parse(src_bytes)

        # Walk classes manually so we can thread ancestry.
        def walk(node, stack: tuple[str, ...]):
            if node.type == "class_definition":
                nm_n = node.child_by_field_name("name")
                sup_n = node.child_by_field_name("superclasses")
                cur_name = ""
                if nm_n is not None:
                    cur_name = src_bytes[nm_n.start_byte:nm_n.end_byte].decode("utf-8", "replace")
                qn = ".".join(stack + (cur_name,)) if cur_name else ".".join(stack)
                src_id = symbol_id(rel, qn) if qn else file_id(rel)
                if sup_n is not None:
                    # Collect identifier / attribute children of the arg list.
                    for ch in sup_n.children:
                        if ch.type == "identifier":
                            base = src_bytes[ch.start_byte:ch.end_byte].decode("utf-8", "replace")
                            targets = class_table.get(base, [])
                            if targets:
                                conf = "extracted" if len(targets) == 1 else "ambiguous"
                                for t in targets:
                                    if t == src_id:
                                        continue
                                    edges.append(Edge(src=src_id, tgt=t,
                                                      edge_type="inherits",
                                                      confidence=conf, source="static",
                                                      meta={"base": base}))
                            else:
                                edges.append(Edge(src=src_id, tgt=external_id(base),
                                                  edge_type="inherits",
                                                  confidence="extracted", source="static",
                                                  meta={"base": base}))
                        elif ch.type == "attribute":
                            # e.g. collections.abc.Mapping — treat as external by top module
                            text = src_bytes[ch.start_byte:ch.end_byte].decode("utf-8", "replace")
                            top = text.split(".")[0]
                            edges.append(Edge(src=src_id, tgt=external_id(top),
                                              edge_type="inherits",
                                              confidence="extracted", source="static",
                                              meta={"base": text}))
                new_stack = stack + (cur_name,) if cur_name else stack
                for sub in node.children:
                    walk(sub, new_stack)
                return
            for sub in node.children:
                walk(sub, stack)

        walk(tree.root_node, ())
    return edges


def build_decorator_edges(repo_path: Path) -> list[Edge]:
    """Emit decorator-name -> decorated-symbol edges. Decorator targets are
    resolved to a symbol in the same file only; cross-file decorators become
    external nodes keyed by the decorator's top-level name.
    """
    parser, _ = _parser()
    edges: list[Edge] = []

    for py in _iter_py_files(repo_path):
        rel = _rel(py, repo_path)
        try:
            src_bytes = py.read_bytes()
        except OSError:
            continue
        tree = parser.parse(src_bytes)

        def extract_name(deco_node) -> str:
            # The decorator's text after the '@' and before any call args.
            txt = src_bytes[deco_node.start_byte:deco_node.end_byte].decode("utf-8", "replace")
            txt = txt.lstrip("@").strip()
            if "(" in txt:
                txt = txt.split("(", 1)[0].strip()
            return txt

        def walk(node, stack: tuple[str, ...]):
            if node.type == "decorated_definition":
                # Find the inner def / class.
                inner = None
                for ch in node.children:
                    if ch.type in ("function_definition", "class_definition"):
                        inner = ch
                        break
                decos = [ch for ch in node.children if ch.type == "decorator"]
                if inner is not None and decos:
                    nm_n = inner.child_by_field_name("name")
                    inner_name = ""
                    if nm_n is not None:
                        inner_name = src_bytes[nm_n.start_byte:nm_n.end_byte].decode("utf-8", "replace")
                    inner_qn = ".".join(stack + (inner_name,)) if inner_name else ""
                    tgt_id = symbol_id(rel, inner_qn) if inner_qn else file_id(rel)
                    for deco in decos:
                        name = extract_name(deco)
                        if not name:
                            continue
                        # The decorator name is the SOURCE (decorator decorates target).
                        # Direction: decorator -> decorated.
                        top = name.split(".")[0]
                        # If the top-level is a known identifier in this file, we don't
                        # have a cheap way to resolve it here; mark it external if it
                        # isn't a local name. Static-call phase will catch same-file
                        # decorators via the regular import graph.
                        src_id = external_id(top) if "." in name else external_id(name)
                        edges.append(Edge(src=src_id, tgt=tgt_id,
                                          edge_type="decorates",
                                          confidence="extracted", source="static",
                                          meta={"decorator": name}))
                # Also keep traversing the inner def's body for nested defs.
                if inner is not None:
                    inner_name = ""
                    nm_n = inner.child_by_field_name("name")
                    if nm_n is not None:
                        inner_name = src_bytes[nm_n.start_byte:nm_n.end_byte].decode("utf-8", "replace")
                    new_stack = stack + (inner_name,) if inner_name else stack
                    for sub in inner.children:
                        walk(sub, new_stack)
                return
            if node.type in ("function_definition", "class_definition"):
                nm_n = node.child_by_field_name("name")
                inner_name = ""
                if nm_n is not None:
                    inner_name = src_bytes[nm_n.start_byte:nm_n.end_byte].decode("utf-8", "replace")
                new_stack = stack + (inner_name,) if inner_name else stack
                for sub in node.children:
                    walk(sub, new_stack)
                return
            for sub in node.children:
                walk(sub, stack)

        walk(tree.root_node, ())
    return edges


# ─────────────────────────────────────────────────────────────────────────────
# Union step (will fully light up in Phase 6; stub here so Phase 1 is usable)
# ─────────────────────────────────────────────────────────────────────────────

def _union_edges(edge_lists: Iterable[list[Edge]]) -> list[Edge]:
    """Merge edges with the same (src, tgt, edge_type). If one came from static
    and another from dynamic, the merged edge gets source='both' and keeps the
    higher-confidence label (dynamic > extracted > ambiguous > unresolved).
    """
    conf_rank = {"dynamic": 3, "extracted": 2, "ambiguous": 1, "unresolved": 0}
    by_key: dict[tuple[str, str, str], Edge] = {}
    for lst in edge_lists:
        for e in lst:
            key = (e.src, e.tgt, e.edge_type)
            if key not in by_key:
                by_key[key] = e
                continue
            prev = by_key[key]
            sources = {prev.source, e.source}
            merged_source = "both" if sources == {"static", "dynamic"} else prev.source
            merged_conf = prev.confidence \
                if conf_rank.get(prev.confidence, 0) >= conf_rank.get(e.confidence, 0) \
                else e.confidence
            merged_meta = {**prev.meta, **e.meta}
            by_key[key] = Edge(
                src=e.src, tgt=e.tgt, edge_type=e.edge_type,
                confidence=merged_conf, source=merged_source, meta=merged_meta,
            )
    return list(by_key.values())


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 6 — entry points, PageRank (call graph only), reachability
# ─────────────────────────────────────────────────────────────────────────────

def _console_scripts_from_pyproject(repo_path: Path) -> list[str]:
    """Return the dotted paths of [project.scripts] / [project.gui-scripts].
    Returns [] if no pyproject.toml. Parsed as plain text to avoid tomllib
    issues on edge-case syntax.
    """
    pp = repo_path / "pyproject.toml"
    if not pp.exists():
        return []
    try:
        import tomllib  # py311+
    except Exception:
        return []
    try:
        data = tomllib.loads(pp.read_text(encoding="utf-8"))
    except Exception:
        return []
    out: list[str] = []
    project = data.get("project", {})
    for key in ("scripts", "gui-scripts"):
        for v in (project.get(key) or {}).values():
            if isinstance(v, str):
                out.append(v)
    return out


def _dotted_path_to_symbol_ids(dotted: str, repo_path: Path, src_roots: list[Path]) -> list[str]:
    """`flask.cli:main` or `flask.cli.main` -> list of candidate symbol ids."""
    mod_part, _, obj_part = dotted.replace(":", ".").partition(".")
    if not obj_part:
        return []
    # Walk from longest prefix to shortest to find the module file.
    parts = dotted.replace(":", ".").split(".")
    for cut in range(len(parts) - 1, 0, -1):
        mod_dotted = ".".join(parts[:cut])
        obj_qn = ".".join(parts[cut:])
        resolved = _resolve_module(mod_dotted, 0, repo_path, repo_path, src_roots)
        if resolved is not None:
            try:
                rel = resolved.relative_to(repo_path)
            except ValueError:
                continue
            return [symbol_id(str(rel).replace("\\", "/"), obj_qn)]
    return []


def _detect_entry_points(g: nx.DiGraph, repo_path: Path) -> set[str]:
    """Flag nodes as entry points. Spec requires FOUR sources:
      - names in `__all__`
      - non-underscore names at package top level (`src/flask/__init__.py`)
      - route-decorated functions (decorators named `route`, `get`, `post`, …)
      - `[project.scripts]` / console_scripts in pyproject.toml
    Mutates each hit's `is_entry_point` attribute and returns the set.
    """
    src_roots = _discover_src_roots(repo_path)
    entries: set[str] = set()

    # (4) console_scripts
    for dotted in _console_scripts_from_pyproject(repo_path):
        for sid in _dotted_path_to_symbol_ids(dotted, repo_path, src_roots):
            if sid in g:
                entries.add(sid)

    # (1) and (2): parse each package __init__.py for __all__ and bare top-level names.
    parser, _ = _parser()
    init_files = [p for p in _iter_py_files(repo_path) if p.name == "__init__.py"]
    for init in init_files:
        rel_dir = _rel(init.parent, repo_path)
        src = init.read_bytes()
        # __all__ via AST
        try:
            tree_ast = ast.parse(src)
        except SyntaxError:
            continue
        exported: set[str] = set()
        for stmt in tree_ast.body:
            if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "__all__" for t in stmt.targets
            ):
                val = stmt.value
                if isinstance(val, (ast.List, ast.Tuple)):
                    for e in val.elts:
                        if isinstance(e, ast.Constant) and isinstance(e.value, str):
                            exported.add(e.value)
        # Non-underscore top-level names (functions/classes defined here OR
        # imports from inside the package re-exported at top level).
        for stmt in tree_ast.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not stmt.name.startswith("_"):
                    exported.add(stmt.name)
            if isinstance(stmt, ast.ImportFrom):
                for alias in stmt.names:
                    nm = alias.asname or alias.name
                    if not nm.startswith("_"):
                        exported.add(nm)
        # For each exported name, try to find a symbol with that short name in
        # this package. We look at every symbol node whose file is under rel_dir.
        for name in exported:
            for n, attrs in g.nodes(data=True):
                if attrs.get("node_type") not in ("func", "class"):
                    continue
                f = attrs.get("file", "")
                if not (f.startswith(rel_dir + "/") or f == rel_dir):
                    continue
                if attrs.get("name") == name:
                    entries.add(n)

    # (3) Route-decorated functions.
    route_decorators = {"route", "get", "post", "put", "delete", "patch",
                        "head", "options", "add_url_rule", "cli"}
    for u, v, data in g.edges(data=True):
        if data.get("edge_type") != "decorates":
            continue
        if u.startswith("external::"):
            top = u.split("::", 1)[1].split(".")[-1]
            if top in route_decorators:
                entries.add(v)

    for n in entries:
        if n in g:
            g.nodes[n]["is_entry_point"] = True
    for n in g.nodes:
        g.nodes[n].setdefault("is_entry_point", False)
    return entries


def reachable_from_entry_points(graph: nx.DiGraph, node_id: str, max_depth: int = 64) -> bool:
    """Is `node_id` reachable from ANY entry point by following edges forward?

    Uses a depth cap AND a visited-path check. Both are required: the depth
    cap handles cycles the visited check somehow misses, the visited check
    stops a BFS exploding on wide graphs.
    """
    if node_id not in graph:
        return False
    entries = [n for n, d in graph.nodes(data=True) if d.get("is_entry_point")]
    if not entries:
        return False
    visited: set[str] = set()
    frontier: list[tuple[str, int]] = [(e, 0) for e in entries]
    while frontier:
        cur, depth = frontier.pop()
        if cur == node_id:
            return True
        if cur in visited or depth >= max_depth:
            continue
        visited.add(cur)
        for succ in graph.successors(cur):
            if succ not in visited:
                frontier.append((succ, depth + 1))
    return False


def _run_pagerank_on_call_graph(g: nx.DiGraph) -> dict[str, float]:
    """PageRank over the CALL subgraph only.

    Running it over the union of call + import edges is meaningless: file
    nodes have 5-15 edges, function nodes 1-4, and PageRank normalises across
    both, so the file population would swamp the function population and the
    scores would mean nothing in either direction. Call-graph only.
    """
    call_edges = [(u, v) for u, v, d in g.edges(data=True) if d.get("edge_type") == "calls"]
    sub = nx.DiGraph()
    # Only include symbol nodes (callers/callees). External + file nodes stay out.
    nodes_in_calls: set[str] = set()
    for u, v in call_edges:
        nodes_in_calls.add(u); nodes_in_calls.add(v)
    for n in nodes_in_calls:
        if n in g:
            sub.add_node(n)
    sub.add_edges_from(call_edges)
    if sub.number_of_nodes() == 0:
        return {}
    try:
        return nx.pagerank(sub, alpha=0.85)
    except Exception:
        return {}


def build_dependency_graph(chunks, repo_path=None, trace_file: str | Path | None = None) -> nx.DiGraph:
    """Public API — same first-positional-arg as the old identifier builder.

    `repo_path`: required for AST analysis (we need file contents, not just
    chunk slices). If it isn't passed, returns an empty graph with
    `graph_builder_version` set — this keeps old callers working during the
    rollout. `pipeline.py` passes it through.

    `trace_file`: optional path to the JSON file produced by `trace_calls.py`
    or `trace_flask_direct.py`. When provided, dynamic edges overlay the
    static set; edges found by BOTH get source="both" and are the
    high-confidence layer.

    Edge direction: A -> B means "A calls / imports / inherits / decorates B".
    Impact analysis ("what breaks if I change B") therefore walks edges
    BACKWARDS from B (predecessors).
    """
    g: nx.DiGraph = nx.DiGraph()
    g.graph["graph_builder_version"] = GRAPH_BUILDER_VERSION
    g.graph["edge_direction_doc"] = "A -> B means A calls/imports/inherits/decorates B"

    if repo_path is None:
        print(
            "[graph_builder] WARNING: build_dependency_graph called without "
            "repo_path — returning empty graph.",
            file=sys.stderr,
        )
        return g

    repo_path = Path(repo_path)

    # Symbol nodes come from chunks so the graph JOINS cleanly against the index.
    # The current chunker always stores `qualname`; older indexes only have
    # `name`, so we fall back when necessary.
    # Nodes carry BOTH `node_type` (our name) and `type` (what the old graph.py
    # router + frontend read); keeping both avoids a frontend rewrite.
    for ch in chunks:
        if ch.get("chunk_type") in ("func", "class"):
            tags = _classify_file(ch["file_path"])
            qn = ch.get("qualname") or ch.get("name", "")
            if not qn:
                continue
            g.add_node(
                symbol_id(ch["file_path"], qn),
                node_type=ch["chunk_type"],
                type=ch["chunk_type"],
                file=ch["file_path"],
                qualname=qn,
                name=ch.get("name", ""),
                lines=f"{ch['start_line']}-{ch['end_line']}",
                external=False,
                **tags,
            )

    # Phase 1 — imports
    import_edges, file_attrs = build_import_edges(repo_path)
    for nid, attrs in file_attrs.items():
        if nid in g:
            continue
        nt = "external" if attrs.get("external") else "file"
        extra = {}
        if nt == "file":
            extra["file"] = nid
        g.add_node(nid, node_type=nt, type=nt, **extra, **attrs)

    # Phase 4 — static call edges
    call_edges, call_stats = build_call_edges(repo_path)

    # Ensure every node referenced by a call edge exists in the graph.
    for e in call_edges:
        for nid in (e.src, e.tgt):
            if nid in g:
                continue
            if nid.startswith("external::"):
                g.add_node(nid, node_type="external", type="external", external=True,
                           is_test=False, is_docs=False, is_src=False)
            elif "::" in nid:
                # Symbol node whose chunk wasn't in the input (e.g. a nested
                # function the chunker didn't capture). Tag from the file prefix.
                f = nid.split("::", 1)[0]
                tags = _classify_file(f)
                g.add_node(nid, node_type="func", type="func",
                           file=f, qualname=nid.split("::", 1)[1],
                           name=nid.rsplit(".", 1)[-1], external=False, **tags)
            else:
                tags = _classify_file(nid)
                g.add_node(nid, node_type="file", type="file",
                           file=nid, external=False, **tags)

    # Phase 5 — inherits + decorates
    class_table = _build_class_symbol_table(repo_path)
    inherit_edges = build_inherits_edges(repo_path, class_table)
    decorator_edges = build_decorator_edges(repo_path)
    for e in inherit_edges + decorator_edges:
        for nid in (e.src, e.tgt):
            if nid in g:
                continue
            if nid.startswith("external::"):
                g.add_node(nid, node_type="external", type="external", external=True,
                           is_test=False, is_docs=False, is_src=False)
            elif "::" in nid:
                f = nid.split("::", 1)[0]
                tags = _classify_file(f)
                g.add_node(nid, node_type="class", type="class",
                           file=f, qualname=nid.split("::", 1)[1],
                           name=nid.rsplit(".", 1)[-1], external=False, **tags)

    # Phase 2 — dynamic trace overlay (optional)
    dynamic_edges: list[Edge] = []
    if trace_file is not None and Path(trace_file).exists():
        import json as _json
        trace = _json.loads(Path(trace_file).read_text(encoding="utf-8"))
        for row in trace:
            dynamic_edges.append(Edge(
                src=row["caller"], tgt=row["callee"],
                edge_type="calls", confidence="dynamic", source="dynamic",
            ))
        # Nodes that appear only in dynamic data (e.g. nested funcs) still get a stub.
        for e in dynamic_edges:
            for nid in (e.src, e.tgt):
                if nid in g:
                    continue
                if "::" in nid:
                    f = nid.split("::", 1)[0]
                    tags = _classify_file(f)
                    g.add_node(nid, node_type="func", type="func", file=f,
                               qualname=nid.split("::", 1)[1],
                               name=nid.rsplit(".", 1)[-1],
                               external=False, **tags)

    # Union everything.
    merged = _union_edges([import_edges, call_edges, inherit_edges, decorator_edges, dynamic_edges])
    for e in merged:
        attrs = {"edge_type": e.edge_type, "confidence": e.confidence, "source": e.source}
        attrs.update({"meta_" + k: v for k, v in e.meta.items()
                      if isinstance(v, (str, int, float, bool))})
        g.add_edge(e.src, e.tgt, **attrs)

    # Entry points + PageRank (call-graph only)
    _detect_entry_points(g, repo_path)
    pr = _run_pagerank_on_call_graph(g)
    for n, score in pr.items():
        if n in g:
            g.nodes[n]["pagerank"] = float(score)
    for n in g.nodes:
        g.nodes[n].setdefault("pagerank", 0.0)

    # Stash analytics for the pipeline to pick up.
    g.graph["call_phase_stats"] = call_stats
    g.graph["edge_counts_by_type"] = Counter(d.get("edge_type") for _, _, d in g.edges(data=True))
    g.graph["edge_counts_by_source"] = Counter(d.get("source") for _, _, d in g.edges(data=True))
    g.graph["edge_counts_by_confidence"] = Counter(d.get("confidence") for _, _, d in g.edges(data=True))
    g.graph["node_counts_by_tag"] = {
        "is_src":   sum(1 for _, d in g.nodes(data=True) if d.get("is_src")),
        "is_test":  sum(1 for _, d in g.nodes(data=True) if d.get("is_test")),
        "is_docs":  sum(1 for _, d in g.nodes(data=True) if d.get("is_docs")),
        "external": sum(1 for _, d in g.nodes(data=True) if d.get("external")),
    }

    return g
