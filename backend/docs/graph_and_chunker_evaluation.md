# Graph and Chunker Evaluation — Flask

Written for: the CodeSense team. Combined evaluation covering the new AST-based graph builder and the four chunking strategies. Target repo: [`pallets/flask`](https://github.com/pallets/flask), indexed as `data/repos/fa1df9ec-61f6-4497-ab7d-184bbc30aee5`.

All result files live under `backend/evaluation/results/`. The ground truth is under `backend/evaluation/ground_truth/`.

---

## 1. Headline results

| What we measured | Result |
|---|---|
| Graph edge-to-node ratio (target < 5.0) | **14.00 → 1.20** |
| Edge precision against 52-edge GT (target ≥ 0.80) | **0.97** |
| Edge recall | **0.70** |
| Edge F1 | **0.81** |
| Static recall of dynamic trace edges | **46.5 %** |
| OSV reachability (5 vulnerable deps + 1 negative control) | **6 / 6 pass** |
| Top-10 PageRank nodes judged "core" (target ≥ 7) | **8 / 10** |
| Chunker winner (max R@5 then MRR on 20 queries) | **greedy_sibling_merge** (R@5 0.80, MRR 0.58) |

Full numbers in [`phase6_graph_new.json`](../evaluation/results/phase6_graph_new.json), [`edge_eval.json`](../evaluation/results/edge_eval.json), [`osv_reachability.json`](../evaluation/results/osv_reachability.json), [`pagerank_manual_rating.json`](../evaluation/results/pagerank_manual_rating.json), [`chunker_eval.json`](../evaluation/results/chunker_eval.json).

---

## 2. Graph: before vs after

Both graphs were built on the same Flask checkout.

| Metric | Baseline (identifier regex) | New (AST + Jedi + trace overlay) |
|---|---:|---:|
| Nodes | 1,309 | 2,519 |
| Edges | 18,320 | **3,014** |
| Edge-to-node ratio | 13.99 | **1.20** |
| Self-edges | 0 | 0 |
| Edge types | 1 (calls only, untagged) | 4 (calls, imports, inherits, decorates) |

The new graph is **larger by node count** on purpose: it now carries file, external, and nested-symbol nodes alongside func/class symbols so no edge dangles. Edges dropped 83 % while gaining three new types and confidence / source tags.

Edge breakdown on the new graph:

| Dimension | Buckets |
|---|---|
| Type | calls 1,839 · decorates 652 · imports 411 · inherits 112 |
| Source | static 2,855 · both (static + dynamic) 74 · dynamic-only 85 |
| Confidence | extracted 2,830 · dynamic 159 · ambiguous 25 |
| Node tags | is_src 721 · is_test 1,599 · is_docs 89 · external 149 |

## 3. Edge precision / recall / F1

### Ground truth

[`backend/evaluation/ground_truth/flask_edges.json`](../evaluation/ground_truth/flask_edges.json) — 52 edges, hand-verified by reading the Flask source. 43 labelled true, 9 labelled false. Covers all four edge types plus known false-positive patterns the old regex builder tends to produce (bare-name collisions, direction inversions, import-vs-call confusion).

### Scorer

[`backend/evaluation/eval_edges.py`](../evaluation/eval_edges.py). For each GT edge:

- TP if the predicted graph has an edge with the exact `(src, tgt, edge_type)` and GT says true
- FP if the graph has the edge but GT says false
- FN if GT says true but the graph doesn't have the edge
- TN if GT says false and the graph agrees

Two baseline scores: `baseline_strict` (byte-identical ids — fair to the new builder, unfair to the old one) and `baseline_leaf` (leaf-name normalized — the fair floor for the old builder, whose node ids were leaf names only).

### Results

| | Precision | Recall | F1 | Edge-type accuracy on matched pairs |
|---|---:|---:|---:|---:|
| **Baseline — strict** | 0.00 | 0.00 | 0.00 | 0.00 |
| **Baseline — leaf-normalized** | **1.00** | 0.33 | 0.49 | 0.82 |
| **New builder** | **0.97** | **0.70** | **0.81** | **1.00** |

Baseline-strict is 0 because the old builder's ids were leaf-only (e.g. `src/flask/app.py::__call__`) and the new GT uses qualnames (`Flask.__call__`) — that's the format mismatch problem being fixed.

Baseline-leaf gives the old builder credit on call-like edges. It still can't produce imports / inherits / decorates, so its recall caps around a third.

The new builder's single FP is `src/flask/cli.py -> src/flask/app.py` (import). GT marks this false (cli.py is imported BY app.py, not the other way); the builder picked up a `from . import cli`-adjacent pattern. Fix is to tighten the `from .X import Y` resolver — logged as a follow-up.

FNs are mostly `__enter__` / `__exit__` on `RequestContext` / `AppContext` (dynamic trace sees them, static resolver doesn't descend through the `with` statement to the context manager class), and a couple of inherits edges that landed with a different qualname than the GT entry.

---

## 4. Static vs dynamic agreement

Phase 2 of the builder is a `sys.setprofile` trace of Flask's real request lifecycle (app creation + multiple route dispatches + blueprint + templates + 404). Edges observed executing are ground-truth-by-definition.

- Dynamic edges observed: **159**
- Found by static analysis independently: **74**
- **Agreement rate: 46.5 %**

The gap is the out-of-scope variable-method case (see §7). It's not the resolver being weak; it's a class of calls Jedi fundamentally can't resolve without type inference.

---

## 5. Chunker evaluation

### Ground truth

[`backend/evaluation/ground_truth/flask_queries.json`](../evaluation/ground_truth/flask_queries.json) — 20 natural-language queries. For each: expected file, expected qualname, and expected line range. A result is correct if the top-K list contains any chunk in the expected file whose line range overlaps the expected range.

### Scorer

[`backend/evaluation/eval_chunkers.py`](../evaluation/eval_chunkers.py). For each strategy:

1. Chunk Flask with the strategy
2. Embed every chunk's `embed_text` via `all-MiniLM-L6-v2` (same model the pipeline uses)
3. Embed every query
4. Rank chunks by cosine similarity
5. Measure recall@1, recall@5, recall@10, MRR

### Results

| Strategy | Chunks | R@1 | R@5 | R@10 | MRR | Chunk time | Embed time |
|---|---:|---:|---:|---:|---:|---:|---:|
| function_level | 2,527 | 0.25 | 0.60 | 0.85 | 0.43 | 20 s | 161 s |
| class_level | 1,971 | 0.25 | 0.60 | 0.80 | 0.42 | 13 s | 131 s |
| sliding_window | 1,761 | 0.35 | 0.60 | **0.90** | 0.49 | 18 s | 114 s |
| **greedy_sibling_merge** | **1,015** | **0.45** | **0.80** | 0.85 | **0.58** | 547 s | 90 s |

Winner is chosen by max **recall@5** then **MRR**, since typical retrieval-augmented generation uses the top-5 chunks as context.

### Chunker decision

**Winner: `greedy_sibling_merge`.** It wins recall@5 by a 20-point margin (0.80 vs 0.60), wins MRR (0.58 vs 0.49), and wins recall@1 (0.45 vs 0.35). It also produces the fewest chunks (1,015 vs 1,761–2,527), so there's less to embed and store; the one cost is a slower chunking pass (~9 min on Flask vs ~20 s for the others) — a one-time cost per index.

Mechanism-wise this matches intuition: greedy_sibling_merge lets semantically related top-level definitions share a chunk, so the embedder sees enough surrounding context to disambiguate short or generic names. `function_level` fragments the same signal across many single-function chunks whose embeddings end up too similar to one another. `sliding_window` ignores structure entirely and scores close to the per-symbol strategies on R@5 despite being blind to definitions.

Set as the pipeline default in `backend/indexing/pipeline.py` — `CODESENSE_CHUNKER` env var still overrides for Farhan's embedding comparison across strategies and for Rizwan's hybrid-retrieval experiments.

---

## 6. PageRank sanity check

The top-10 call-graph PageRank nodes on Flask were rated by reading the referenced source. Rating: 8 / 10 **core** — exceeds the 7 / 10 target. Full breakdown in [`pagerank_manual_rating.json`](../evaluation/results/pagerank_manual_rating.json).

| Rank | PR | Node | Verdict |
|---:|---:|---|---|
| 1 | 0.00569 | `src/flask/sansio/blueprints.py::Blueprint.record_once` | core |
| 2 | 0.00380 | `examples/tutorial/flaskr/db.py::get_db` | peripheral |
| 3 | 0.00318 | `src/flask/sansio/blueprints.py::Blueprint.record` | core |
| 4 | 0.00272 | `src/flask/sansio/scaffold.py::Scaffold._method_route` | core |
| 5 | 0.00230 | `src/flask/json/tag.py::TaggedJSONSerializer.tag` | peripheral |
| 6 | 0.00186 | `src/flask/app.py::Flask.ensure_sync` | core |
| 7 | 0.00175 | `src/flask/sansio/scaffold.py::Scaffold.route` | core |
| 8 | 0.00168 | `src/flask/globals.py::ProxyMixin._get_current_object` | core |
| 9 | 0.00159 | `src/flask/sansio/scaffold.py::setupmethod` | core |
| 10 | 0.00158 | `src/flask/sansio/scaffold.py::setupmethod.<locals>.wrapper_func` | core |

The two peripheral entries (#2 and #5) are an `examples/tutorial/` function and a specific JSON-tag helper. Neither is in the hot path. Everything else is on the request / routing / setup-guard backbone.

Compare to the old builder's top-10: dominated by `Flask` (245 outgoing bogus edges), `App`, `Scaffold`, and a **test class** (`TestStreaming`) in slot 6. The new top-10 reads like Flask's internals, not noise.

---

## 7. OSV reachability

For each of 5 Flask dependencies with known OSV advisories, call `reachable_from_entry_points(graph, "external::<dep>")` and compare to the expected answer. One negative control (`django`, not a Flask dep) is included.

| Dependency | Advisory | Expected | Got | Pass |
|---|---|---|---|---|
| werkzeug | GHSA-2g68-c3qc-8985 | reachable | reachable | ✓ |
| jinja2 | GHSA-h5c8-rqwp-cp95 | reachable | reachable | ✓ |
| click | GHSA-qq66-rhrg-4pcx | reachable | reachable | ✓ |
| itsdangerous | PYSEC-2021-21 | reachable | reachable | ✓ |
| blinker | PYSEC-2019-166 | reachable | reachable | ✓ |
| django (control) | N/A | **NOT** reachable | NOT reachable | ✓ |

**6 / 6 pass** including the negative control. Graph has 65 entry points; reachability terminates via depth-and-visited guards.

---

## 8. Known limitations

- **Variable-method calls** (`client.get(...)`, `app.route(...)`, `self.view_functions[...]`). Static resolver can't infer the variable's type. Top unresolved patterns on Flask: `attr:get` (260), `attr:route` (169), `attr:register_blueprint` (55). Phase 2 (dynamic trace) is the designed complement.
- **Dynamism** — `getattr` / `setattr` / `eval` / monkey-patching. No attempt made.
- **Decorator cross-file resolution.** Same-file decorators flow via the static call phase. Cross-file decorators become `external::` nodes rather than resolving to the real target.
- **Dynamic trace scope.** Ran against the request lifecycle in-process (not the full pytest suite — `pytest + sys.setprofile` on Windows was prohibitively slow). A bigger workload would raise both the dynamic edge count and the 46.5 % overlap.
- **One FP on the new builder** (`src/flask/cli.py -> src/flask/app.py` direction inversion). Follow-up ticket.

---

## 9. Reproducing this evaluation

```bash
# 0. Capture baseline (already in backend/evaluation/baseline/)
python backend/evaluation/phase0_baseline.py

# 1. Jedi timing spike
python backend/evaluation/jedi_spike.py

# 2. Dynamic trace
python backend/evaluation/trace_flask_direct.py

# 3. Full new graph + headline analytics
python backend/evaluation/run_new_builder.py

# 4. Edge precision/recall/F1
python backend/evaluation/eval_edges.py

# 5. Chunker retrieval eval
python backend/evaluation/eval_chunkers.py

# 6. OSV reachability
python backend/evaluation/eval_osv_reachability.py
```

Tests: `pytest backend/tests/test_graph_builder.py` (12 tests) and `pytest backend/tests/test_chunker.py` (62 tests).

---

## 10. D2 report summary

> **Graph builder & chunker — D2 status.** The new AST-based graph builder is live on `feat/graph-builder`, replacing the identifier-regex heuristic. Edge-to-node ratio on Flask dropped from 14.00 to 1.20 (target < 5.0). Against a 52-edge hand-labelled ground truth, the new builder scores precision 0.97, recall 0.70, F1 0.81. Of 159 call edges observed executing under `sys.setprofile`, 74 (46.5 %) are independently confirmed by static analysis — the gap is the known variable-method-call limitation. The reachability helper validates 6 / 6 against known-dependency OSV findings including a negative control. Top-10 PageRank nodes on the call subgraph: 8 / 10 judged core by manual inspection. Chunker evaluation over 20 queries ran all four strategies against the Flask source with `all-MiniLM-L6-v2`; **`greedy_sibling_merge`** wins on R@5 (0.80 vs 0.60 next) and MRR (0.58 vs 0.49 next), and is now the pipeline default — unblocking Farhan's embedding comparison and Rizwan's hybrid retrieval integration.
