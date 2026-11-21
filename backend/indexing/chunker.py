"""
CodeSense chunker — pluggable strategies on a shared tree-sitter layer.

Four strategies (chunk_function_level, chunk_class_level, chunk_sliding_window,
chunk_greedy_sibling_merge) differ only in how they group symbols. They all
pass through the same post-processing: split_oversized -> gap_fill -> validate.

Every chunk carries seven invariant keys consumed downstream
(chunk_id, file_path, start_line, end_line, chunk_type, name, code) plus six
additive keys (qualname, header, embed_text, content_hash, parent, token_count).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from tree_sitter import Parser, Language
import tree_sitter_python as tspython


# ─────────────────────────────────────────────────────────────────────────────
# Errors / types
# ─────────────────────────────────────────────────────────────────────────────

class ChunkingError(Exception):
    """Raised by validate() for any invariant violation."""


@dataclass
class Symbol:
    name: str
    qualname: str
    kind: str                       # "func" | "class"
    start_line: int                 # 1-indexed, inclusive
    end_line: int                   # 1-indexed, inclusive
    start_byte: int                 # UTF-8 byte offset into source, inclusive
    end_byte: int                   # exclusive
    source: str                     # raw text for [start_byte:end_byte]
    parent: str | None              # parent qualname, or None if top-level


_parser_singleton: tuple[Parser, Language] | None = None


def _parser() -> tuple[Parser, Language]:
    global _parser_singleton
    if _parser_singleton is None:
        lang = Language(tspython.language())
        p = Parser()
        p.language = lang
        _parser_singleton = (p, lang)
    return _parser_singleton


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_symbols(source: str, path: str) -> list[Symbol]:
    """Extract every function_definition and class_definition.

    When a definition is wrapped in decorated_definition, the outer range is
    used so decorator lines belong to their function (orphaned @t.overload
    decorators were a coverage hole in the audit).

    qualname is the dot-joined ancestry chain of enclosing func/class names
    inside the file (e.g. "Flask.route", "test_appctx.index"). If two symbols
    would end up with the same qualname (rare — redefinitions inside branches),
    both get a "@L<line>" suffix so chunk_ids remain unique.
    """
    parser, _ = _parser()
    source_bytes = source.encode("utf-8", errors="replace")
    tree = parser.parse(source_bytes)

    symbols: list[Symbol] = []

    def walk(node, parent_qn: str | None) -> None:
        if node.type in ("function_definition", "class_definition"):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                name = "<anon>"
            else:
                name = source_bytes[name_node.start_byte:name_node.end_byte].decode(
                    "utf-8", errors="replace"
                )

            effective = node
            if node.parent is not None and node.parent.type == "decorated_definition":
                effective = node.parent

            qualname = f"{parent_qn}.{name}" if parent_qn else name
            kind = "func" if node.type == "function_definition" else "class"

            sym = Symbol(
                name=name,
                qualname=qualname,
                kind=kind,
                start_line=effective.start_point[0] + 1,
                end_line=effective.end_point[0] + 1,
                start_byte=effective.start_byte,
                end_byte=effective.end_byte,
                source=source_bytes[effective.start_byte:effective.end_byte].decode(
                    "utf-8", errors="replace"
                ),
                parent=parent_qn,
            )
            symbols.append(sym)

            for child in node.children:
                walk(child, qualname)
        else:
            for child in node.children:
                walk(child, parent_qn)

    walk(tree.root_node, None)

    counts: dict[str, int] = {}
    for s in symbols:
        counts[s.qualname] = counts.get(s.qualname, 0) + 1
    if any(v > 1 for v in counts.values()):
        symbols = [
            replace(s, qualname=f"{s.qualname}@L{s.start_line}")
            if counts[s.qualname] > 1 else s
            for s in symbols
        ]

    return symbols


# ─────────────────────────────────────────────────────────────────────────────
# Header / budget
# ─────────────────────────────────────────────────────────────────────────────

def build_header(path: str, qualname: str) -> str:
    return f"# file: {path}\n# symbol: {qualname}\n"


def token_count(text: str, tokenizer) -> int:
    return len(tokenizer.encode(text, add_special_tokens=True))


def enforce_budget(text: str, tokenizer, budget: int) -> bool:
    return token_count(text, tokenizer) <= budget


# ─────────────────────────────────────────────────────────────────────────────
# Chunk construction
# ─────────────────────────────────────────────────────────────────────────────

def _make_chunk(
    *,
    file_path: str,
    qualname: str,
    name: str,
    chunk_type: str,
    start_line: int,
    end_line: int,
    start_byte: int,
    end_byte: int,
    code: str,
    parent: str | None,
    tokenizer,
) -> dict:
    header = build_header(file_path, qualname)
    embed_text = header + code
    return {
        "chunk_id": f"{file_path}::{qualname}",
        "file_path": file_path,
        "start_line": start_line,
        "end_line": end_line,
        "start_byte": start_byte,
        "end_byte": end_byte,
        "chunk_type": chunk_type,
        "name": name,
        "qualname": qualname,
        "code": code,
        "header": header,
        "embed_text": embed_text,
        "parent": parent,
        "content_hash": hashlib.sha256(code.encode("utf-8")).hexdigest()[:16],
        "token_count": token_count(embed_text, tokenizer),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Oversized splitter
# ─────────────────────────────────────────────────────────────────────────────

def _line_byte_offsets(code_bytes: bytes) -> list[int]:
    """Byte offset of the start of each line (0-indexed). Trailing sentinel = len."""
    offsets = [0]
    for i, b in enumerate(code_bytes):
        if b == 0x0A:                       # '\n'
            offsets.append(i + 1)
    offsets.append(len(code_bytes))
    return offsets


def split_oversized(chunk: dict, tokenizer, budget: int) -> list[dict]:
    """Split an over-budget chunk at physical line boundaries.

    Pieces are consecutive, non-overlapping, and together cover the original
    chunk's byte range exactly. Each piece gets its own header + "[part N of M]"
    marker and its chunk_type becomes "fragment".
    """
    if enforce_budget(chunk["embed_text"], tokenizer, budget):
        return [chunk]

    code = chunk["code"]
    path = chunk["file_path"]
    base_qn = chunk["qualname"]
    name = chunk["name"]
    parent = chunk.get("parent")
    base_start_line = chunk["start_line"]
    base_start_byte = chunk["start_byte"]

    code_bytes = code.encode("utf-8")
    line_offsets = _line_byte_offsets(code_bytes)
    n_lines = len(line_offsets) - 1
    lines = code.split("\n")
    # code.split("\n") length equals the number of logical lines separated by \n.
    # If code ends with '\n' it adds a trailing empty entry — that is fine.
    # We clamp loops to len(lines) to stay consistent.
    n_lines = len(lines)

    # Greedy-fill: a trial header that upper-bounds length so the decision matches
    # the final per-piece header.
    worst_header = build_header(path, f"{base_qn}#part999") + "# [part 999 of 999]\n"

    groups: list[list[int]] = []
    current: list[int] = []
    for i in range(n_lines):
        trial_idx = current + [i]
        trial_text = "\n".join(lines[j] for j in trial_idx)
        if enforce_budget(worst_header + trial_text, tokenizer, budget):
            current = trial_idx
        else:
            if current:
                groups.append(current)
                current = [i]
            else:
                # A single physical line exceeds budget; emit it alone (unavoidable
                # without mid-line splitting, which the spec forbids).
                groups.append([i])
                current = []
    if current:
        groups.append(current)

    total = len(groups)
    pieces: list[dict] = []
    for idx, group in enumerate(groups, start=1):
        first_i = group[0]
        last_i = group[-1]

        # Byte range relative to `code`. For the final line, end at end of code
        # so we don't shed the trailing newline fraction.
        rel_start = line_offsets[first_i]
        if last_i + 1 < len(line_offsets):
            rel_end = line_offsets[last_i + 1]      # includes trailing newline
            # Strip the trailing newline so pieces concatenate without doubling.
            if rel_end > rel_start and code_bytes[rel_end - 1:rel_end] == b"\n":
                rel_end -= 1
        else:
            rel_end = len(code_bytes)

        abs_start_byte = base_start_byte + rel_start
        abs_end_byte = base_start_byte + rel_end
        abs_start_line = base_start_line + first_i
        abs_end_line = base_start_line + last_i

        piece_code = code_bytes[rel_start:rel_end].decode("utf-8", errors="replace")
        piece_qn = f"{base_qn}#part{idx}"
        header = build_header(path, piece_qn) + f"# [part {idx} of {total}]\n"
        embed_text = header + piece_code

        pieces.append({
            "chunk_id": f"{path}::{piece_qn}",
            "file_path": path,
            "start_line": abs_start_line,
            "end_line": abs_end_line,
            "start_byte": abs_start_byte,
            "end_byte": abs_end_byte,
            "chunk_type": "fragment",
            "name": name,
            "qualname": piece_qn,
            "code": piece_code,
            "header": header,
            "embed_text": embed_text,
            "parent": base_qn if parent is None else parent,
            "content_hash": hashlib.sha256(piece_code.encode("utf-8")).hexdigest()[:16],
            "token_count": token_count(embed_text, tokenizer),
        })
    return pieces


# ─────────────────────────────────────────────────────────────────────────────
# Gap fill
# ─────────────────────────────────────────────────────────────────────────────

def _merge_intervals(ivs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge half-open intervals [s, e). Returns sorted non-overlapping list."""
    if not ivs:
        return []
    ivs = sorted(ivs)
    out = [list(ivs[0])]
    for s, e in ivs[1:]:
        if s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _subtract(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Set difference a - b on half-open intervals."""
    a = _merge_intervals(a)
    b = _merge_intervals(b)
    result: list[tuple[int, int]] = []
    bi = 0
    for s, e in a:
        cur = s
        while bi < len(b) and b[bi][1] <= cur:
            bi += 1
        j = bi
        while j < len(b) and b[j][0] < e:
            bs, be = b[j]
            if bs > cur:
                result.append((cur, min(bs, e)))
            cur = max(cur, be)
            if cur >= e:
                break
            j += 1
        if cur < e:
            result.append((cur, e))
    return result


def _nonblank_line_ranges(source: str) -> list[tuple[int, int, int]]:
    """Return list of (start_byte, end_byte_excl, line_number_1b) for non-blank lines.

    end_byte excludes the trailing newline so ranges from adjacent non-blank lines
    are not adjacent in bytes — a blank line between them (even one char) creates
    a gap that keeps them as distinct byte intervals.
    """
    out: list[tuple[int, int, int]] = []
    source_bytes = source.encode("utf-8")
    pos = 0
    for i, line in enumerate(source.split("\n")):
        lb = line.encode("utf-8")
        start = pos
        end = pos + len(lb)
        if line.strip():
            out.append((start, end, i + 1))
        pos = end + 1   # +1 for the '\n' separator
    # The last split entry has no trailing '\n' in the source, so pos may overshoot
    # by one; harmless because we only read ranges already computed.
    return out


def gap_fill(
    path: str,
    source: str,
    claimed_ranges: list[tuple[int, int]],
    tokenizer,
    budget: int,
) -> list[dict]:
    """Emit one "module_level" chunk per contiguous run of uncovered non-blank lines.

    Replaces the old fixed "first 120 lines" overview entirely.
    """
    nonblank = _nonblank_line_ranges(source)
    if not nonblank:
        return []

    nb_intervals = [(s, e) for s, e, _ in nonblank]
    uncovered = _subtract(nb_intervals, claimed_ranges)
    if not uncovered:
        return []

    # Group non-blank lines whose intervals land in the uncovered set, into
    # contiguous runs. A line counts as uncovered if any part of it is uncovered.
    uncovered_merged = _merge_intervals(uncovered)
    uncov_lines: list[tuple[int, int, int]] = []
    ui = 0
    for s, e, ln in nonblank:
        while ui < len(uncovered_merged) and uncovered_merged[ui][1] <= s:
            ui += 1
        j = ui
        hit = False
        while j < len(uncovered_merged) and uncovered_merged[j][0] < e:
            if uncovered_merged[j][0] < e and uncovered_merged[j][1] > s:
                hit = True
                break
            j += 1
        if hit:
            uncov_lines.append((s, e, ln))

    # Collapse adjacent-by-line entries into runs.
    runs: list[list[tuple[int, int, int]]] = []
    for item in uncov_lines:
        if runs and item[2] == runs[-1][-1][2] + 1:
            runs[-1].append(item)
        else:
            # Allow blank lines between — spec says contiguous *run* of uncovered,
            # but we treat a gap of covered or blank lines as a run boundary so
            # each gap_fill chunk is a tight, self-contained range.
            if runs and item[2] - runs[-1][-1][2] <= 1:
                runs[-1].append(item)
            else:
                runs.append([item])

    source_bytes = source.encode("utf-8")
    chunks: list[dict] = []
    for run in runs:
        start_byte = run[0][0]
        end_byte = run[-1][1]
        start_line = run[0][2]
        end_line = run[-1][2]
        code = source_bytes[start_byte:end_byte].decode("utf-8", errors="replace")
        qualname = f"<module_level>@L{start_line}-{end_line}"
        c = _make_chunk(
            file_path=path,
            qualname=qualname,
            name=f"module_level@{start_line}",
            chunk_type="module_level",
            start_line=start_line,
            end_line=end_line,
            start_byte=start_byte,
            end_byte=end_byte,
            code=code,
            parent=None,
            tokenizer=tokenizer,
        )
        if not enforce_budget(c["embed_text"], tokenizer, budget):
            chunks.extend(split_oversized(c, tokenizer, budget))
        else:
            chunks.append(c)
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Validate
# ─────────────────────────────────────────────────────────────────────────────

def validate(chunks: list[dict], source: str, path: str, tokenizer, budget: int) -> None:
    """Raise ChunkingError on any invariant violation.

    Uses `raise` not `assert` so it survives `python -O`.
    """
    # 1. Coverage: every non-blank-line byte is in the union of chunk ranges.
    claimed = [(c["start_byte"], c["end_byte"]) for c in chunks]
    nb = [(s, e) for s, e, _ in _nonblank_line_ranges(source)]
    missing = _subtract(nb, claimed)
    if missing:
        # Translate uncovered byte ranges back to line numbers for the message.
        line_map = _nonblank_line_ranges(source)
        bad_lines: list[int] = []
        mi = 0
        for ms, me in missing:
            while mi < len(line_map) and line_map[mi][1] <= ms:
                mi += 1
            j = mi
            while j < len(line_map) and line_map[j][0] < me:
                bad_lines.append(line_map[j][2])
                j += 1
        bad_lines = sorted(set(bad_lines))
        raise ChunkingError(
            f"{path}: {len(bad_lines)} non-blank line(s) not covered by any chunk "
            f"(bytes uncovered: {sum(e-s for s,e in missing)}). "
            f"First uncovered lines: {bad_lines[:20]}"
        )

    # 2. Budget.
    for c in chunks:
        if token_count(c["embed_text"], tokenizer) > budget:
            raise ChunkingError(
                f"{path}: chunk {c['chunk_id']} "
                f"(lines {c['start_line']}-{c['end_line']}) exceeds budget "
                f"({token_count(c['embed_text'], tokenizer)} > {budget})"
            )

    # 3. Header present and non-empty.
    for c in chunks:
        if not c.get("header"):
            raise ChunkingError(
                f"{path}: chunk {c['chunk_id']} "
                f"(lines {c['start_line']}-{c['end_line']}) lacks a header"
            )

    # 4. chunk_id uniqueness.
    seen: dict[str, dict] = {}
    for c in chunks:
        cid = c["chunk_id"]
        if cid in seen:
            prev = seen[cid]
            raise ChunkingError(
                f"{path}: duplicate chunk_id {cid} "
                f"(lines {prev['start_line']}-{prev['end_line']} vs "
                f"{c['start_line']}-{c['end_line']})"
            )
        seen[cid] = c


# ─────────────────────────────────────────────────────────────────────────────
# Strategy helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_file(file_path: Path, repo_path: Path) -> tuple[str, str]:
    rel_path = str(file_path.relative_to(repo_path)).replace("\\", "/")
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        source = f.read()
    return rel_path, source


def _sort_by_start_byte(chunks: list[dict]) -> list[dict]:
    return sorted(chunks, key=lambda c: (c["start_byte"], c["end_byte"]))


# ─────────────────────────────────────────────────────────────────────────────
# Strategy: function level
# ─────────────────────────────────────────────────────────────────────────────

def _file_chunks_function_level(
    file_path: Path, repo_path: Path, tokenizer, budget: int
) -> list[dict]:
    rel_path, source = _read_file(file_path, repo_path)
    symbols = parse_symbols(source, rel_path)

    produced: list[dict] = []
    claimed: list[tuple[int, int]] = []

    for sym in symbols:
        c = _make_chunk(
            file_path=rel_path,
            qualname=sym.qualname,
            name=sym.name,
            chunk_type=sym.kind,
            start_line=sym.start_line,
            end_line=sym.end_line,
            start_byte=sym.start_byte,
            end_byte=sym.end_byte,
            code=sym.source,
            parent=sym.parent,
            tokenizer=tokenizer,
        )
        pieces = split_oversized(c, tokenizer, budget)
        produced.extend(pieces)
        claimed.append((sym.start_byte, sym.end_byte))

    produced.extend(gap_fill(rel_path, source, claimed, tokenizer, budget))
    produced = _sort_by_start_byte(produced)
    validate(produced, source, rel_path, tokenizer, budget)
    return produced


# ─────────────────────────────────────────────────────────────────────────────
# Strategy: class level
# ─────────────────────────────────────────────────────────────────────────────

def _file_chunks_class_level(
    file_path: Path, repo_path: Path, tokenizer, budget: int
) -> list[dict]:
    rel_path, source = _read_file(file_path, repo_path)
    symbols = parse_symbols(source, rel_path)
    by_qn = {s.qualname: s for s in symbols}

    def inside_class(sym: Symbol) -> bool:
        p = sym.parent
        while p is not None:
            anc = by_qn.get(p)
            if anc is None:
                return False
            if anc.kind == "class":
                return True
            p = anc.parent
        return False

    kept: list[Symbol] = []
    for s in symbols:
        if s.kind == "class":
            kept.append(s)
        elif s.kind == "func" and not inside_class(s):
            kept.append(s)

    produced: list[dict] = []
    claimed: list[tuple[int, int]] = []
    for sym in kept:
        c = _make_chunk(
            file_path=rel_path,
            qualname=sym.qualname,
            name=sym.name,
            chunk_type=sym.kind,
            start_line=sym.start_line,
            end_line=sym.end_line,
            start_byte=sym.start_byte,
            end_byte=sym.end_byte,
            code=sym.source,
            parent=sym.parent,
            tokenizer=tokenizer,
        )
        pieces = split_oversized(c, tokenizer, budget)
        produced.extend(pieces)
        claimed.append((sym.start_byte, sym.end_byte))

    produced.extend(gap_fill(rel_path, source, claimed, tokenizer, budget))
    produced = _sort_by_start_byte(produced)
    validate(produced, source, rel_path, tokenizer, budget)
    return produced


# ─────────────────────────────────────────────────────────────────────────────
# Strategy: sliding window
# ─────────────────────────────────────────────────────────────────────────────

def _file_chunks_sliding_window(
    file_path: Path,
    repo_path: Path,
    tokenizer,
    budget: int,
    window: int | None = None,
    overlap: int | None = None,
) -> list[dict]:
    """Fixed token-count windows over the source, ignoring symbols.

    Defaults: window = budget - 16 tokens (to leave room for the header),
    overlap = window // 5.
    """
    rel_path, source = _read_file(file_path, repo_path)
    source_bytes = source.encode("utf-8")

    if window is None:
        window = max(16, budget - 16)
    if overlap is None:
        overlap = max(0, window // 5)
    step = max(1, window - overlap)

    enc = tokenizer(
        source,
        return_offsets_mapping=True,
        add_special_tokens=False,
        truncation=False,
    )
    ids = enc["input_ids"]
    offsets = enc["offset_mapping"]

    produced: list[dict] = []
    claimed: list[tuple[int, int]] = []

    if ids:
        idx = 0
        while idx < len(ids):
            end_idx = min(idx + window, len(ids))
            char_start = offsets[idx][0]
            char_end = offsets[end_idx - 1][1]
            if char_end <= char_start:
                if end_idx >= len(ids):
                    break
                idx += step
                continue

            start_byte = len(source[:char_start].encode("utf-8"))
            end_byte = len(source[:char_end].encode("utf-8"))
            code = source[char_start:char_end]

            start_line = source[:char_start].count("\n") + 1
            end_line = source[:char_end].count("\n") + 1

            qualname = f"<window>@tok{idx}"
            c = _make_chunk(
                file_path=rel_path,
                qualname=qualname,
                name=f"window@{start_line}",
                chunk_type="module_level",
                start_line=start_line,
                end_line=end_line,
                start_byte=start_byte,
                end_byte=end_byte,
                code=code,
                parent=None,
                tokenizer=tokenizer,
            )
            pieces = split_oversized(c, tokenizer, budget)
            produced.extend(pieces)
            claimed.append((start_byte, end_byte))

            if end_idx >= len(ids):
                break
            idx += step

    produced.extend(gap_fill(rel_path, source, claimed, tokenizer, budget))
    produced = _sort_by_start_byte(produced)
    validate(produced, source, rel_path, tokenizer, budget)
    return produced


# ─────────────────────────────────────────────────────────────────────────────
# Strategy: greedy sibling merge
# ─────────────────────────────────────────────────────────────────────────────

def _file_chunks_greedy_sibling_merge(
    file_path: Path, repo_path: Path, tokenizer, budget: int
) -> list[dict]:
    """Walk top-level AST siblings, merging adjacent ones until the budget fills."""
    rel_path, source = _read_file(file_path, repo_path)
    source_bytes = source.encode("utf-8")
    parser, _ = _parser()
    tree = parser.parse(source_bytes)

    top = [c for c in tree.root_node.children if c.type != "comment"]

    produced: list[dict] = []
    claimed: list[tuple[int, int]] = []
    group: list = []

    def flush(group_nodes):
        if not group_nodes:
            return
        sb = group_nodes[0].start_byte
        eb = group_nodes[-1].end_byte
        sl = group_nodes[0].start_point[0] + 1
        el = group_nodes[-1].end_point[0] + 1
        code = source_bytes[sb:eb].decode("utf-8", errors="replace")
        qn = f"<merged>@L{sl}-{el}"
        c = _make_chunk(
            file_path=rel_path,
            qualname=qn,
            name=f"merged@{sl}",
            chunk_type="module_level",
            start_line=sl,
            end_line=el,
            start_byte=sb,
            end_byte=eb,
            code=code,
            parent=None,
            tokenizer=tokenizer,
        )
        pieces = split_oversized(c, tokenizer, budget)
        produced.extend(pieces)
        claimed.append((sb, eb))

    for node in top:
        trial = group + [node]
        sb = trial[0].start_byte
        eb = trial[-1].end_byte
        trial_code = source_bytes[sb:eb].decode("utf-8", errors="replace")
        trial_embed = build_header(rel_path, f"<merged>@L{trial[0].start_point[0]+1}") + trial_code
        if enforce_budget(trial_embed, tokenizer, budget):
            group = trial
        else:
            if group:
                flush(group)
                group = [node]
            else:
                flush([node])
                group = []
    flush(group)

    produced.extend(gap_fill(rel_path, source, claimed, tokenizer, budget))
    produced = _sort_by_start_byte(produced)
    validate(produced, source, rel_path, tokenizer, budget)
    return produced


# ─────────────────────────────────────────────────────────────────────────────
# Public strategies — (repo_path, tokenizer, budget) -> list[dict]
# ─────────────────────────────────────────────────────────────────────────────

_SKIP_PARTS = ("site-packages", "venv", ".venv", "node_modules")


def _iter_py_files(repo_path: Path) -> list[Path]:
    files = [
        f for f in repo_path.rglob("*.py")
        if not any(skip in str(f) for skip in _SKIP_PARTS)
    ]
    return sorted(files)


def _run_strategy(
    file_fn: Callable[[Path, Path, object, int], list[dict]],
    repo_path: Path,
    tokenizer,
    budget: int,
    on_progress: Callable | None = None,
) -> tuple[list[dict], list[dict]]:
    """Common driver: sorted file list, collect chunks and skipped_files."""
    files = _iter_py_files(repo_path)
    total = len(files)
    all_chunks: list[dict] = []
    skipped: list[dict] = []

    for i, fp in enumerate(files):
        try:
            all_chunks.extend(file_fn(fp, repo_path, tokenizer, budget))
        except Exception as e:
            rel = str(fp.relative_to(repo_path)).replace("\\", "/")
            skipped.append({"file": rel, "reason": f"{type(e).__name__}: {e}"})

        if on_progress and total > 0:
            on_progress("parsing", int(10 + (i + 1) / total * 20))

    return all_chunks, skipped


def chunk_function_level(repo_path, tokenizer, budget, on_progress=None):
    return _run_strategy(_file_chunks_function_level, Path(repo_path), tokenizer, budget, on_progress)


def chunk_class_level(repo_path, tokenizer, budget, on_progress=None):
    return _run_strategy(_file_chunks_class_level, Path(repo_path), tokenizer, budget, on_progress)


def chunk_sliding_window(repo_path, tokenizer, budget, on_progress=None):
    return _run_strategy(_file_chunks_sliding_window, Path(repo_path), tokenizer, budget, on_progress)


def chunk_greedy_sibling_merge(repo_path, tokenizer, budget, on_progress=None):
    return _run_strategy(_file_chunks_greedy_sibling_merge, Path(repo_path), tokenizer, budget, on_progress)


CHUNKERS: dict[str, Callable] = {
    "function_level": chunk_function_level,
    "class_level": chunk_class_level,
    "sliding_window": chunk_sliding_window,
    "greedy_sibling_merge": chunk_greedy_sibling_merge,
}
