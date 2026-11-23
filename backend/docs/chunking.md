# Chunking Mechanism

CodeSense breaks every Python file in a repository into token-budget-aware **chunks** before embedding. A chunk is a self-contained, searchable unit — one function, one class, a module-level region, or a bounded slice of something too large to fit in the embedder's context window.

The chunker lives in [`backend/indexing/chunker.py`](../indexing/chunker.py). It is pluggable: four strategies pick symbols differently, but all four share the same parsing layer and the same post-processing pipeline.

---

## 1. The chunk contract

Every chunk is a dict with 13 keys. Downstream code (embedding, retrieval, graph build) depends on these invariants:

| Key | Meaning |
|---|---|
| `chunk_id` | Unique identifier: `"<file_path>::<qualname>"` |
| `file_path` | Repo-relative path, forward-slashed |
| `start_line` / `end_line` | 1-indexed, inclusive line range |
| `start_byte` / `end_byte` | UTF-8 byte range into the source file (half-open) |
| `chunk_type` | `"func"`, `"class"`, `"module_level"`, or `"fragment"` |
| `name` | Short display name (function/class name) |
| `qualname` | Dot-joined ancestry (e.g. `Flask.route`) |
| `code` | Raw source text for the byte range |
| `header` | `# file: <path>\n# symbol: <qualname>\n` |
| `embed_text` | `header + code` — what actually gets embedded |
| `parent` | Parent qualname, or `None` for top-level |
| `content_hash` | First 16 hex chars of SHA-256 of `code` |
| `token_count` | Tokens in `embed_text` under the embedder's tokenizer |

Four invariants are enforced by [`validate()`](../indexing/chunker.py):

1. **Coverage** — every non-blank-line byte in the source must be inside the union of chunk byte ranges.
2. **Budget** — `token_count(embed_text) ≤ budget` for every chunk.
3. **Header** — every chunk has a non-empty header.
4. **Uniqueness** — every `chunk_id` appears once.

`validate()` uses `raise ChunkingError`, not `assert`, so it still runs under `python -O`.

---

## 2. Shared pipeline

Regardless of strategy, each file is processed as:

```
parse_symbols  →  build chunks  →  split_oversized  →  gap_fill  →  sort  →  validate
```

### 2.1 `parse_symbols`

Uses `tree-sitter-python` to walk the AST and emit one `Symbol` per `function_definition` and `class_definition`.

Two subtleties:

- **Decorators stay with their function.** If a definition is wrapped in a `decorated_definition` node, the outer range is used, so `@t.overload` lines are not orphaned.
- **`qualname` collisions get a line suffix.** If two symbols end up with the same qualname (redefinitions inside branches), both are rewritten to `name@L<line>` so chunk_ids remain unique.

### 2.2 `split_oversized`

If a chunk's `embed_text` exceeds the token budget, it is split at **physical line boundaries** — never mid-line. Pieces are consecutive, non-overlapping, and cover the original byte range exactly.

Each piece becomes `chunk_type="fragment"` with qualname `<base>#part<N>` and a header line `# [part N of M]`.

The splitter uses a greedy fill against a **worst-case header** (`#part999`, `[part 999 of 999]`) so the token-fit decision made at split time matches the final header's size.

If a single physical line already exceeds the budget on its own, it's emitted alone — the spec forbids mid-line splitting.

### 2.3 `gap_fill`

After symbol chunks are produced, there are usually leftover lines — imports, module-level constants, top-level `if __name__ == "__main__"` blocks, etc. `gap_fill` computes the byte-set difference between all **non-blank** source lines and all claimed byte ranges, then emits one `module_level` chunk per contiguous uncovered run.

This replaces the earlier "first 120 lines as an overview" heuristic — nothing is lost, nothing is duplicated.

A `gap_fill` chunk that itself exceeds the budget is routed back through `split_oversized`.

### 2.4 `validate`

Runs the four invariants. On failure it raises `ChunkingError` with a message pointing at the first offending lines or chunk id.

---

## 3. The four strategies

All four take `(repo_path, tokenizer, budget, on_progress=None)` and return `(chunks, skipped_files)`. They are registered in the `CHUNKERS` dict and selected via the `CODESENSE_CHUNKER` env var (default: `"function_level"`).

### 3.1 `function_level`

One chunk per function and per class — including nested ones. Simplest and most granular. Best for symbol-level retrieval ("show me the `login` function").

### 3.2 `class_level`

Classes are kept whole (methods are not split out). Top-level functions are kept. Functions **inside** a class are suppressed in favor of the enclosing class chunk. Produces fewer, larger chunks when classes are the unit of meaning.

### 3.3 `sliding_window`

Ignores symbols entirely. Uses the tokenizer's offset mapping to slide a fixed window across the source:

- Default window: `budget - 16` tokens (leaving room for the header).
- Default overlap: `window // 5`.
- Step: `max(1, window - overlap)`.

Useful as a baseline and for languages where symbol extraction is weaker.

### 3.4 `greedy_sibling_merge`

Walks the top-level AST children and greedily merges adjacent siblings into one chunk, flushing whenever the next sibling would exceed the budget. Produces chunks aligned to natural top-level boundaries — several small helpers grouped, one large function alone.

---

## 4. File walking

[`_iter_py_files()`](../indexing/chunker.py) collects every `*.py` under the repo, skipping anything whose path contains `site-packages`, `venv`, `.venv`, or `node_modules`.

[`_run_strategy()`](../indexing/chunker.py) is the common driver: it iterates files, catches per-file exceptions into a `skipped_files` list (`{file, reason}`) so one broken file doesn't kill the run, and reports progress 10 % → 30 %.

---

## 5. Integration with the pipeline

[`backend/indexing/pipeline.py`](../indexing/pipeline.py) wires the chunker into the full indexing flow:

```python
model     = SentenceTransformer(EMBEDDING_MODEL)
tokenizer = model.tokenizer
budget    = int(model.max_seq_length)

chunks, skipped = chunk_repository(
    repo_path, tokenizer, budget,
    chunker_name=CHUNKER_NAME,          # from CODESENSE_CHUNKER env var
    on_progress=on_progress,
)
```

The **same tokenizer and max_seq_length** are shared between chunker and embedder, so any chunk that passes `validate()` is guaranteed to fit at embed time — no silent truncation.

The pipeline then calls `generate_embeddings(chunks, model)`, builds the identifier-based dependency graph, and persists `chunks_full.json`, `embeddings.npz`, `graph.graphml`, and `metadata.json` (including the chunker name and token budget) under `data/indexes/<repo_id>/`.

---

## 6. Switching strategies

```bash
# function-level (default)
export CODESENSE_CHUNKER=function_level

# class-level
export CODESENSE_CHUNKER=class_level

# sliding window
export CODESENSE_CHUNKER=sliding_window

# greedy sibling merge
export CODESENSE_CHUNKER=greedy_sibling_merge
```

An unknown value raises `ValueError` at indexing time with the list of valid names.

---

## 7. Why these invariants matter

- **Coverage** means retrieval never has a blind spot — no source line is unreachable through similarity search.
- **Budget** means the embedder never sees a truncated chunk, which would silently misalign the vector with the code it claims to represent.
- **Uniqueness** means `chunk_id` is a safe primary key for the embedding store and the graph.
- **Header-prefixed `embed_text`** gives the embedder the file path and symbol name as context, which measurably improves retrieval for short or generic function bodies.
