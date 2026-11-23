# Graph Builder

CodeSense's AST-based source-graph component. Replaces the identifier-string heuristic that used to live in `pipeline.build_dependency_graph`.

The old builder drew an edge wherever an identifier appeared anywhere in another chunk's body (including inside comments and string literals), and keyed its symbol table on bare names so every function called `index` collapsed into one node. On Flask that gave **1,309 nodes and 18,320 edges** — ratio 14.00. The new builder reads the syntax tree instead and resolves names through [Jedi](https://jedi.readthedocs.io/), producing **2,519 nodes and 3,014 edges** — ratio 1.20 — with every edge tagged by type, confidence, and source.

Code: [`backend/indexing/graph_builder.py`](../indexing/graph_builder.py). Shared id helper: [`backend/indexing/ids.py`](../indexing/ids.py). Tests: [`backend/tests/test_graph_builder.py`](../tests/test_graph_builder.py).

---

## 1. What a graph contains

### Nodes

Four kinds, each tagged so downstream code can filter them:

| `node_type` | Represents | Id format |
|---|---|---|
| `func` | A function or method | `<file_path>::<qualname>` |
| `class` | A class | `<file_path>::<qualname>` |
| `file` | A whole file (import source/target) | `<file_path>` |
| `external` | A symbol or module outside the indexed repo | `external::<name>` |

The symbol id format is **byte-identical to the chunker's `chunk_id`** so the graph joins cleanly against the index. See [`backend/indexing/ids.py`](../indexing/ids.py).

Every node also carries:

- `is_src` / `is_test` / `is_docs` — mutually exclusive classification from the file path
- `external` — True for pip / stdlib targets, False for anything in the repo
- `is_entry_point` — True for public API (see §3)
- `pagerank` — call-graph centrality score (see §4)

Nodes also carry `type` as an alias for `node_type` so the existing `backend/app/routers/graph.py` router doesn't break.

### Edges

Four types. **Direction: `A -> B` means "A calls / imports / inherits-from / decorates B".** So "what would break if I change B" walks edges **backwards** from B (its predecessors). Getting this inverted silently inverts every impact answer.

| `edge_type` | Meaning | Resolver |
|---|---|---|
| `calls` | A's body contains a call to B | tree-sitter + Jedi |
| `imports` | A imports B (file-to-file) | tree-sitter + path resolver |
| `inherits` | Class A extends class B | tree-sitter + class symbol table |
| `decorates` | Decorator A is applied to symbol B | tree-sitter |

Every edge has three required attributes:

- `edge_type` — one of the four above
- `confidence` — `extracted` (one definition found), `ambiguous` (>1), or `dynamic` (observed executing)
- `source` — `static`, `dynamic`, or `both`

An edge found by **both** static and dynamic analysis is the high-confidence layer.

---

## 2. The six phases

All phases live in `graph_builder.py` and run from one entry point:

```python
build_dependency_graph(chunks, repo_path=None, trace_file=None) -> nx.DiGraph
```

The signature is backward-compatible: old callers passing only `chunks` still work (they get an empty graph, warning on stderr). `pipeline.py` passes `repo_path` so real analysis runs.

### Phase 1 — imports

Tree-sitter queries only, no resolver needed:

```
(import_statement name: (dotted_name) @abs_name)
(import_from_statement module_name: (dotted_name) @from_abs)
(import_from_statement module_name: (relative_import) @from_rel)
```

Resolves each dotted name to a file inside the repo. Handles:

- **Absolute imports** — `flask.app` → `src/flask/app.py`
- **Package imports** — `flask` → `src/flask/__init__.py`
- **Relative imports** — `from . import x`, `from ..scaffold import X`
- **src/-layout** — tries `<repo>/src/` as an implicit root
- **Progressive prefix** — `flask.app.Flask` (symbol, not module) still resolves to `src/flask/app.py`

Anything that doesn't resolve becomes an `external::` node — the external graph still has to exist, because a dependency CVE (for example) lives in external code; a reachability query that stops at the repo boundary could never reach it.

### Phase 2 — dynamic trace

Ground truth. Runs the target repo's code under `sys.setprofile` and records every `(caller, callee)` pair where both sides are inside the repo. Edges are tagged `confidence="dynamic"`, `source="dynamic"`.

Two runner scripts:

- [`backend/evaluation/trace_calls.py`](../evaluation/trace_calls.py) — pytest-based; needs the target repo's test deps installed. Best effort — if the suite won't run, we record zero edges and keep going.
- [`backend/evaluation/trace_flask_direct.py`](../evaluation/trace_flask_direct.py) — a direct-exercise harness that runs Flask's real request lifecycle in-process. Same profiler, same JSON output format. Faster than pytest-on-Windows (which was prohibitively slow with `setprofile`).

Caller/callee ids use the **same** `<file_path>::<qualname>` format the chunker produces, so dynamic edges join directly with static ones.

### Phase 3 — Jedi spike

Not a build step — a measurement. Before building Phase 4, we time Jedi on 10 real files from the target repo to decide whether it's fast enough. Results on Flask:

- Mean: 3.9 s/file
- Resolution rate: 94–100 %
- Worst case: `src/flask/app.py` at 20.5 s (1,632 refs, Flask's god-object)

Script: [`backend/evaluation/jedi_spike.py`](../evaluation/jedi_spike.py). If the mean is above 5 s/file, the spec says to stop and pick a cheaper fallback.

### Phase 4 — static call edges

Tree-sitter finds the call sites and the enclosing function (the caller). Jedi resolves the target.

In scope:

- **Bare call** — `foo(x)` → Jedi resolves `foo` at the call site
- **Self method** — `self.method()` → resolves to the enclosing class
- **Class method** — `ClassName.method()` → via the symbol table
- **Module call** — `mod.function()` → via the import table

Out of scope — documented as a known limitation:

- `obj.method()` where `obj` is a variable (needs type inference)
- `getattr` / `setattr` / `__getattr__`, monkey-patching, `eval` / `exec`

Confidence from Jedi's return value:

- Exactly one definition → `extracted`
- More than one → `ambiguous`, one edge to each target
- Zero → recorded as unresolved, **never silently skipped**

Analytics track the top 10 unresolved patterns per repo so the limitations are visible, not invisible.

**Jedi's `full_name` prefix quirk.** For src-layout repos, Jedi returns names like `src.flask.sansio.blueprints.Blueprint.record` — with the `src.` prefix. Other times it returns `flask.sansio.blueprints.Blueprint.record` — without it. The chunker's qualname format (`Blueprint.record`) is just the within-file ancestry, so neither of Jedi's conventions matches directly. [`_file_dotted_variants`](../indexing/graph_builder.py) + [`_strip_module_prefix`](../indexing/graph_builder.py) try every plausible decomposition and pick the longest match. This single detail is the difference between **0 %** and **46.5 %** static-vs-dynamic agreement.

### Phase 5 — inherits + decorates

Pure tree-sitter, no resolver:

```
(class_definition
    name: (identifier) @cls
    superclasses: (argument_list) @supers)

(decorated_definition
    (decorator) @deco
    definition: [(function_definition name: (identifier) @fn_name)
                 (class_definition name: (identifier) @cls_name)])
```

Inheritance resolves against a pre-built class symbol table ([`_build_class_symbol_table`](../indexing/graph_builder.py)); a base class defined outside the repo becomes an `external::` edge. Multiple classes with the same name become `ambiguous` with one edge to each.

Decorator edges use the decorator's identifier (stripped of any `@` and `(...)`) as the source node — same-file decorators land via the static call phase; cross-file decorators become `external::`.

### Phase 6 — union + PageRank + reachability

**Union.** [`_union_edges`](../indexing/graph_builder.py) merges all edge lists keyed on `(src, tgt, edge_type)`. If a pair is found by both static and dynamic analysis, the merged edge gets `source="both"` and keeps the higher-confidence label (`dynamic` > `extracted` > `ambiguous`). Edge `meta` fields are dict-merged.

**PageRank — call graph only.** Running PageRank over the union of calls + imports would be meaningless: file nodes have 5–15 edges each, function nodes 1–4, and the two populations barely connect. PageRank normalises across all nodes, so the file population would swamp the function population and the scores would mean nothing in either direction. [`_run_pagerank_on_call_graph`](../indexing/graph_builder.py) builds a subgraph of just `edge_type == "calls"`, runs PageRank there, and writes scores back to the main graph. File and external nodes get `pagerank = 0.0`.

**Reachability** — [`reachable_from_entry_points(graph, node_id)`](../indexing/graph_builder.py):

- Depth cap (64 by default)
- Visited-path check
- **Both** — one catches cycles the other somehow misses; recursion gives self-edges and mutual recursion gives cycles

A test ([`test_reachability_terminates_on_mutual_recursion`](../tests/test_graph_builder.py)) proves it terminates on a file with `a → b → a → …`.

---

## 3. Entry points

A node is an entry point if any of these four signals apply:

1. **Named in `__all__`** of a package `__init__.py`
2. **Non-underscore top-level name** at a package `__init__.py` (functions, classes, re-exported imports)
3. **Route-decorated functions** — anything decorated with `route`, `get`, `post`, `put`, `delete`, `patch`, `head`, `options`, `add_url_rule`, or `cli`
4. **`[project.scripts]` / `[project.gui-scripts]`** in `pyproject.toml`

Flask is a library (no `main()`), so this is the correct shape of its public surface. Each hit gets `is_entry_point = True`; everything else gets `False`.

A node with **zero inbound edges** is "no inbound edges found", not "dead code." It might be an entry point, called dynamically from a framework, or missed by the static resolver.

---

## 4. Integration

`pipeline.py` calls into the builder like this:

```python
from .graph_builder import build_dependency_graph

graph = build_dependency_graph(chunks, repo_path=repo_path)
```

And writes it out with the existing names:

- `graph.graphml` — the graph itself, same path as before
- `metadata.json` — now carries `graph_stats` (edges by type/source/confidence, nodes by tag, call-phase stats) plus `graph_builder_version = "sprint2-ast-graph"`

The `nx.write_graphml` call can't serialise Counter / dict graph-level attributes, so pipeline.py pops them off the graph and folds them into `metadata.json` before writing.

The `backend/app/routers/graph.py` endpoint and the frontend read `graph.graphml` unchanged — nodes carry the `type` alias so the existing router code works without edits.

---

## 5. Current limitations

These are documented, not bugs:

- **Variable-method calls.** `client.get(...)`, `app.route(...)`, `request.get_json(...)` — the callee type is inferred at runtime, and Jedi can't resolve them statically without a type hint. On Flask these are the top 10 unresolved patterns (260 × `.get`, 169 × `.route`, …). Phase 2's dynamic trace is the intended complement for this case.
- **Dynamism** — `getattr`, `setattr`, `eval`, `exec`, `__getattr__`, monkey-patching. No attempt made.
- **Decorator cross-file resolution.** Same-file decorators flow through the normal call-edge phase. Cross-file decorators become `external::` nodes rather than resolving to the real target. (The call-edge phase does catch the decorated function's calls into the decorator body when Jedi resolves them.)
- **Dynamic trace scope.** The direct-exercise harness runs a small scenario (request lifecycle + blueprint + templates). A bigger workload would raise both the dynamic edge count and the static-vs-dynamic agreement — we're at 46.5 % on 159 observed edges.

---

## 6. Repo layout

```
backend/
├── indexing/
│   ├── graph_builder.py       the component
│   ├── ids.py                 symbol_id / file_id / external_id helpers
│   └── pipeline.py            wires it in
├── tests/
│   └── test_graph_builder.py  12 tests, all invariants + regressions
├── evaluation/
│   ├── phase0_baseline.py     captures the old graph for before/after
│   ├── jedi_spike.py          Phase 3 timing measurement
│   ├── trace_calls.py         Phase 2 pytest runner
│   ├── trace_flask_direct.py  Phase 2 direct-exercise runner for Flask
│   ├── run_new_builder.py     end-to-end driver, writes analytics JSON
│   ├── baseline/              frozen baseline graph(s)
│   └── results/               phase results + new-builder graphs
└── docs/
    └── graph-builder.md       this file
```

---

## 7. Flask results summary

| Metric | Old (identifier heuristic) | New (AST + Jedi + trace) |
|---|---:|---:|
| Nodes | 1,309 | 2,519 |
| Edges | 18,320 | 3,014 |
| Edge-to-node ratio | **13.99** | **1.20** |
| Static recall of dynamic edges | n/a | **46.5 %** |
| Call sites resolved | n/a | 72 % |
| Jedi timing (full call-edge phase) | n/a | 80 s |
| Edges by type | n/a | calls 1,839 · decorates 652 · imports 411 · inherits 112 |
| Nodes by tag | n/a | src 721 · test 1,599 · docs 89 · external 149 |

Full numbers in [`backend/evaluation/results/phase6_graph_new.json`](../evaluation/results/phase6_graph_new.json).

---

## 8. How to run it

Index a repo through the normal pipeline — the new builder is wired in:

```bash
# via the backend API as usual (/api/repositories, /api/index etc.)
```

To re-run just the graph phase on an already-indexed repo (useful for iterating on the builder without re-embedding):

```bash
python backend/evaluation/run_new_builder.py
```

It reads `data/indexes/<repo-id>/chunks_full.json`, reruns the builder, overlays `data/evaluation/results/dynamic_edges_flask.json` if present, and writes a fresh graph + analytics JSON into `backend/evaluation/results/`.
