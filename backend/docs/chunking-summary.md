# Chunking — Done

Chunking is working on `chunking-strategies`. Here's what it does, in plain words.

## What chunking is

Before we can search a codebase, we have to break each file into small pieces. Each piece is a **chunk** — one function, one class, or a section of module-level code. We then turn every chunk into an embedding and store it. Search works by finding the chunks whose embeddings are closest to the query.

So chunking is just: **cut each Python file into pieces that fit the embedder's size limit.**

## What we built

One file: `backend/indexing/chunker.py`.

It reads every `.py` file in a repo (skipping `venv`, `node_modules`, etc.) and returns a list of chunks. Each chunk is a dict with the file path, line range, raw code, and a few other fields. That's what the rest of the pipeline embeds and indexes.

## Four strategies, same output shape

You pick a strategy with the `CODESENSE_CHUNKER` env var.

| Strategy | What it does |
|---|---|
| `function_level` (default) | One chunk per function and per class. Simplest. |
| `class_level` | Classes stay whole, methods are not split out. |
| `sliding_window` | Ignores structure, slides a fixed-size window over the file. |
| `greedy_sibling_merge` | Groups adjacent top-level things until the size limit fills up. |

All four produce chunks in the same shape, so the rest of the pipeline doesn't care which one you pick.

## How we find functions and classes

We use `tree-sitter` to parse each Python file into a syntax tree. For every node that's a `function_definition` or `class_definition` we record its name, line range, and parent. Decorators are kept with their function, not left dangling. Nested things get dotted names like `Service.run`.

No regex. No reading the file twice.

## The safety net

After the strategy picks its chunks, three things happen automatically:

1. **Oversized chunks get split.** If a chunk is bigger than the embedder's token limit, it's cut at line boundaries into numbered pieces (`#part1`, `#part2`, …). No chunk ever exceeds the limit.
2. **Gaps get filled.** Anything the strategy missed (imports, module-level code, `if __name__ == "__main__"`) is collected into `module_level` chunks so no line of real code is left behind.
3. **Validation.** We check that every non-blank line is covered, every chunk fits the token limit, every chunk has a header, and every chunk ID is unique. If any of that fails, we raise an error and skip the file — we never silently ship a broken index.

## Where to look

- Code: [`backend/indexing/chunker.py`](../indexing/chunker.py)
- Full technical write-up: [`backend/docs/chunking.md`](./chunking.md)
- Wired into: [`backend/indexing/pipeline.py`](../indexing/pipeline.py) — the pipeline shares the embedder's tokenizer and max length with the chunker, so anything the chunker produces is guaranteed to embed.
- Tests: [`backend/tests/test_chunker.py`](../tests/test_chunker.py)

## How to try it

Default (`function_level`) just works. To try another strategy:

```bash
export CODESENSE_CHUNKER=class_level
# then run the indexer as usual
```

Valid names: `function_level`, `class_level`, `sliding_window`, `greedy_sibling_merge`. An invalid name errors loudly at startup.

## Status

- Branch: `chunking-strategies`
- Merged into `main`: not yet — open a PR when you're ready to review.
- Blockers: none.
