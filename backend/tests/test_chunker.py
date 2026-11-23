"""
Tests for backend/indexing/chunker.py.

For each of the four strategies, on three real Flask files (including the
module that originally had orphaned @t.overload decorators, src/flask/cli.py),
we check that:

  * every non-blank line is covered by at least one chunk
  * no chunk exceeds the tokenizer's budget
  * chunk_ids are unique
  * every chunk has a non-empty header
  * running the strategy twice on the same input produces identical output

Plus one test that a file that fails to parse is recorded in skipped_files
rather than silently dropped.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `backend/` importable so `indexing.chunker` resolves from tests/.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indexing.chunker import (                      # noqa: E402
    CHUNKERS,
    parse_symbols,
    validate,
)


FLASK_REPO = ROOT.parent / "data" / "repos" / "fa1df9ec-61f6-4497-ab7d-184bbc30aee5"

# Three real Flask files. cli.py is the one whose decorator lines were orphaned
# by the old chunker; app.py is a large class-heavy module; __init__.py is small.
TARGET_FILES = [
    "src/flask/cli.py",
    "src/flask/app.py",
    "src/flask/__init__.py",
]

STRATEGIES = list(CHUNKERS.keys())


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def model():
    if not FLASK_REPO.exists():
        pytest.skip(f"Flask repo not present at {FLASK_REPO}")
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("all-MiniLM-L6-v2")


@pytest.fixture(scope="session")
def tokenizer(model):
    return model.tokenizer


@pytest.fixture(scope="session")
def budget(model):
    return int(model.max_seq_length)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run_on_one_file(strategy_name, rel_file, tokenizer, budget, tmp_path):
    """Run the given strategy on a single file and return its chunks.

    We copy the one file into a throwaway directory so the strategy's
    rglob picks up exactly one file. The run also exercises the sort order,
    skipped_files collection, and the shared validate() step.
    """
    src = FLASK_REPO / rel_file
    if not src.exists():
        pytest.skip(f"missing source file: {src}")
    target = tmp_path / rel_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(src.read_bytes())

    chunks, skipped = CHUNKERS[strategy_name](tmp_path, tokenizer, budget)
    assert skipped == [], f"unexpected skip: {skipped}"
    return chunks


def _nonblank_lines_of(path: Path) -> set[int]:
    return {
        i + 1
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").split("\n"))
        if line.strip()
    }


# ─────────────────────────────────────────────────────────────────────────────
# Per-strategy invariants on three real files
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("rel_file", TARGET_FILES)
def test_full_coverage(strategy, rel_file, tokenizer, budget, tmp_path):
    chunks = _run_on_one_file(strategy, rel_file, tokenizer, budget, tmp_path)
    assert chunks, "no chunks produced"

    nb = _nonblank_lines_of(FLASK_REPO / rel_file)
    covered = set()
    for c in chunks:
        for ln in range(c["start_line"], c["end_line"] + 1):
            if ln in nb:
                covered.add(ln)
    missing = sorted(nb - covered)
    assert not missing, (
        f"{strategy} on {rel_file}: {len(missing)} uncovered non-blank lines; "
        f"first few: {missing[:10]}"
    )


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("rel_file", TARGET_FILES)
def test_budget_respected(strategy, rel_file, tokenizer, budget, tmp_path):
    chunks = _run_on_one_file(strategy, rel_file, tokenizer, budget, tmp_path)
    for c in chunks:
        tok = len(tokenizer.encode(c["embed_text"], add_special_tokens=True))
        assert tok <= budget, (
            f"{strategy} on {rel_file}: chunk {c['chunk_id']} "
            f"has {tok} tokens, budget {budget}"
        )


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("rel_file", TARGET_FILES)
def test_chunk_ids_unique(strategy, rel_file, tokenizer, budget, tmp_path):
    chunks = _run_on_one_file(strategy, rel_file, tokenizer, budget, tmp_path)
    ids = [c["chunk_id"] for c in chunks]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"{strategy} on {rel_file}: duplicate chunk_ids {dupes}"


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("rel_file", TARGET_FILES)
def test_header_present(strategy, rel_file, tokenizer, budget, tmp_path):
    chunks = _run_on_one_file(strategy, rel_file, tokenizer, budget, tmp_path)
    for c in chunks:
        assert c.get("header"), f"empty header on {c['chunk_id']}"
        assert c["embed_text"].startswith(c["header"]), (
            f"embed_text does not start with header for {c['chunk_id']}"
        )


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.parametrize("rel_file", TARGET_FILES)
def test_determinism(strategy, rel_file, tokenizer, budget, tmp_path):
    a = _run_on_one_file(strategy, rel_file, tokenizer, budget, tmp_path / "a")
    b = _run_on_one_file(strategy, rel_file, tokenizer, budget, tmp_path / "b")

    def key(c):
        return (c["chunk_id"], c["start_byte"], c["end_byte"], c["content_hash"])

    assert [key(c) for c in a] == [key(c) for c in b], (
        f"{strategy} on {rel_file} is non-deterministic"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Parse failures surface via skipped_files
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_failure_recorded(tmp_path, tokenizer, budget, monkeypatch):
    """A file whose chunker raises is captured in skipped_files, not vanished.

    We simulate a parse failure by monkeypatching parse_symbols for one path.
    The strategy's per-file try/except must turn that into a skipped_files entry.
    """
    good = tmp_path / "pkg" / "ok.py"
    bad = tmp_path / "pkg" / "broken.py"
    good.parent.mkdir(parents=True)
    good.write_text("def hello():\n    return 1\n", encoding="utf-8")
    bad.write_text("def broken():\n    pass\n", encoding="utf-8")

    from indexing import chunker as C
    real = C.parse_symbols

    def flaky(source, path):
        if "broken.py" in path:
            raise RuntimeError("synthetic parse failure")
        return real(source, path)

    monkeypatch.setattr(C, "parse_symbols", flaky)

    chunks, skipped = C.chunk_function_level(tmp_path, tokenizer, budget)
    rel_skipped = {s["file"] for s in skipped}
    assert "pkg/broken.py" in rel_skipped, (
        f"broken file not in skipped_files: {skipped}"
    )
    assert any(c["file_path"] == "pkg/ok.py" for c in chunks), (
        "good file silently dropped"
    )
    for s in skipped:
        assert "synthetic parse failure" in s["reason"]


# ─────────────────────────────────────────────────────────────────────────────
# Decorators belong to their function
# ─────────────────────────────────────────────────────────────────────────────

def test_decorator_wrapped_symbol():
    """Decorated functions include their decorator lines in the symbol range.

    This is the specific regression the audit found at
    src/flask/cli.py:229 and :235 (@t.overload lines were orphaned by the
    old chunker, which keyed off function_definition directly).
    """
    source = (
        "import typing as t\n"
        "\n"
        "@t.overload\n"
        "def ping(x: int) -> int: ...\n"
        "@t.overload\n"
        "def ping(x: str) -> str: ...\n"
    )
    syms = parse_symbols(source, "x.py")
    assert len(syms) == 2
    assert syms[0].start_line == 3 and syms[0].end_line == 4
    assert syms[1].start_line == 5 and syms[1].end_line == 6
    assert "@t.overload" in syms[0].source
    assert "@t.overload" in syms[1].source
