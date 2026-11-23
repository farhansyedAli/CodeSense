"""Chunker retrieval evaluation — run all four strategies against the
20-query Flask ground truth and pick a winner.

For each strategy:
  1. Chunk the target repo with that strategy.
  2. Embed every chunk's `embed_text` via sentence-transformers all-MiniLM-L6-v2
     (same model the pipeline uses).
  3. Embed every query.
  4. Rank chunks by cosine similarity per query.
  5. A result is correct if the top-K list contains ANY chunk whose file_path
     matches `expected_file` and whose [start_line, end_line] overlaps
     `expected_lines`.

Metrics: recall@1, recall@5, recall@10, MRR, mean chunk_count, mean chunking
time, mean embedding time. The winner maximises recall@5 then MRR.

Output: `backend/evaluation/results/chunker_eval.json`.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from indexing.chunker import CHUNKERS   # noqa: E402

REPO = ROOT / "data/repos/fa1df9ec-61f6-4497-ab7d-184bbc30aee5"
GT   = ROOT / "backend/evaluation/ground_truth/flask_queries.json"
OUT  = ROOT / "backend/evaluation/results/chunker_eval.json"


def _overlaps(a_lo: int, a_hi: int, b_lo: int, b_hi: int) -> bool:
    return a_lo <= b_hi and b_lo <= a_hi


def _is_hit(chunk: dict, query: dict) -> bool:
    if chunk.get("file_path") != query["expected_file"]:
        return False
    s, e = chunk.get("start_line", 0), chunk.get("end_line", 0)
    q_lo, q_hi = query["expected_lines"]
    return _overlaps(s, e, q_lo, q_hi)


def _cosine_rank(query_vec: np.ndarray, chunk_vecs: np.ndarray) -> np.ndarray:
    """Return indices of chunks ranked by descending cosine similarity."""
    # vectors are normalized by SentenceTransformer? No — do it ourselves.
    qn = query_vec / (np.linalg.norm(query_vec) + 1e-12)
    cn = chunk_vecs / (np.linalg.norm(chunk_vecs, axis=1, keepdims=True) + 1e-12)
    sims = cn @ qn
    return np.argsort(-sims)


def main() -> None:
    from sentence_transformers import SentenceTransformer
    print("[chunker_eval] loading embedding model (all-MiniLM-L6-v2)")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    tokenizer = model.tokenizer
    budget = int(model.max_seq_length)

    queries = json.loads(GT.read_text())["queries"]
    q_vecs = model.encode([q["query"] for q in queries],
                          show_progress_bar=False, batch_size=32,
                          convert_to_numpy=True)
    print(f"[chunker_eval] {len(queries)} queries embedded")

    results = []
    for name, fn in CHUNKERS.items():
        print(f"\n[chunker_eval] === {name} ===")
        t0 = time.time()
        chunks, skipped = fn(REPO, tokenizer, budget)
        t_chunk = time.time() - t0
        print(f"  chunked in {t_chunk:.1f}s, {len(chunks)} chunks "
              f"({len(skipped)} skipped files)")

        t1 = time.time()
        texts = [c["embed_text"] for c in chunks]
        vecs = model.encode(texts, show_progress_bar=False, batch_size=64,
                            convert_to_numpy=True)
        t_embed = time.time() - t1
        print(f"  embedded in {t_embed:.1f}s")

        hits_at = {1: 0, 5: 0, 10: 0}
        rrs: list[float] = []
        per_query = []
        for i, q in enumerate(queries):
            order = _cosine_rank(q_vecs[i], vecs)
            rank_of_first_hit = None
            for rank, idx in enumerate(order, start=1):
                if _is_hit(chunks[idx], q):
                    rank_of_first_hit = rank
                    break
            for k in hits_at:
                if rank_of_first_hit is not None and rank_of_first_hit <= k:
                    hits_at[k] += 1
            rrs.append(1.0 / rank_of_first_hit if rank_of_first_hit else 0.0)
            per_query.append({
                "query": q["query"],
                "expected_file": q["expected_file"],
                "expected_qualname": q["expected_qualname"],
                "rank_of_first_hit": rank_of_first_hit,
            })

        n = len(queries)
        results.append({
            "chunker": name,
            "chunk_count": len(chunks),
            "skipped_files": len(skipped),
            "seconds_chunking": round(t_chunk, 2),
            "seconds_embedding": round(t_embed, 2),
            "recall_at_1":  round(hits_at[1] / n, 4),
            "recall_at_5":  round(hits_at[5] / n, 4),
            "recall_at_10": round(hits_at[10] / n, 4),
            "mrr":          round(sum(rrs) / n, 4),
            "per_query": per_query,
        })
        print(f"  R@1 {results[-1]['recall_at_1']}  R@5 {results[-1]['recall_at_5']}  "
              f"R@10 {results[-1]['recall_at_10']}  MRR {results[-1]['mrr']}")

    # Winner: maximise recall@5 then MRR
    winner = max(results, key=lambda r: (r["recall_at_5"], r["mrr"]))
    print(f"\n[chunker_eval] WINNER: {winner['chunker']} "
          f"(R@5 {winner['recall_at_5']}, MRR {winner['mrr']})")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "repo": "flask",
        "query_count": len(queries),
        "embedding_model": "all-MiniLM-L6-v2",
        "winner": winner["chunker"],
        "ranking_rule": "max recall@5, then MRR",
        "results": [{k: v for k, v in r.items() if k != "per_query"}
                    for r in results],
        "per_strategy": {r["chunker"]: r["per_query"] for r in results},
    }, indent=2))


if __name__ == "__main__":
    main()
