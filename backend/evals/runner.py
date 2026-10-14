"""
Minimal retrieval eval. Prerequisite: backend server running on localhost:8000.

Usage (from backend/):
    python -m evals.runner
"""
import json
import time
import httpx
from pathlib import Path
from datetime import datetime
from statistics import mean

API = "http://localhost:8000"
GOLD = Path(__file__).parent / "gold.json"
RESULTS_DIR = Path(__file__).parent / "results"


def overlap(a_start, a_end, b_start, b_end):
    """True if the two line ranges share at least one line."""
    return max(a_start, b_start) <= min(a_end, b_end)


def matches(citation, gold_list):
    """A retrieved chunk matches gold if same file + any line overlap."""
    for g in gold_list:
        if citation["file"] == g["file"] and overlap(
            citation["start_line"], citation["end_line"],
            g["start_line"], g["end_line"]
        ):
            return True
    return False


def main():
    gold = json.loads(GOLD.read_text())
    per_query = []

    print(f"Running {len(gold['queries'])} queries against {API}\n")

    with httpx.Client(timeout=30) as client:
        for q in gold["queries"]:
            start = time.time()
            try:
                res = client.post(f"{API}/api/query", json={
                    "repo_id": q["repo_id"],
                    "question": q["question"],
                })
            except Exception as e:
                print(f"✗ {q['id']}  ERROR: {e}")
                per_query.append({"id": q["id"], "rank": None, "hit_at_3": 0, "latency_ms": 0, "error": str(e)})
                continue

            latency_ms = (time.time() - start) * 1000

            if res.status_code != 200:
                print(f"✗ {q['id']}  HTTP {res.status_code}")
                per_query.append({"id": q["id"], "rank": None, "hit_at_3": 0, "latency_ms": round(latency_ms), "http": res.status_code})
                continue

            citations = res.json().get("citations", [])
            rank = None
            for i, c in enumerate(citations, start=1):
                if matches(c, q["gold"]):
                    rank = i
                    break

            per_query.append({
                "id": q["id"],
                "intent": q.get("intent", "unknown"),
                "rank": rank,
                "hit_at_1": 1 if rank == 1 else 0,
                "hit_at_3": 1 if rank and rank <= 3 else 0,
                "latency_ms": round(latency_ms),
            })
            mark = "✓" if rank and rank <= 3 else "✗"
            print(f"{mark} {q['id']:20s}  rank={rank}  {latency_ms:.0f}ms")

    # Aggregate
    recall_at_1 = mean(q.get("hit_at_1", 0) for q in per_query)
    recall_at_3 = mean(q.get("hit_at_3", 0) for q in per_query)
    mrr = mean(1 / q["rank"] if q.get("rank") else 0 for q in per_query)
    lat = sorted(q["latency_ms"] for q in per_query if q.get("latency_ms"))
    p50 = lat[len(lat) // 2] if lat else 0

    print(f"\n--- summary ---")
    print(f"n_queries   : {len(per_query)}")
    print(f"recall@1    : {recall_at_1:.1%}")
    print(f"recall@3    : {recall_at_3:.1%}")
    print(f"mrr         : {mrr:.3f}")
    print(f"latency p50 : {p50}ms")

    # Save result
    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"{datetime.utcnow().strftime('%Y-%m-%d_%H%M')}_baseline.json"
    out.write_text(json.dumps({
        "ran_at": datetime.utcnow().isoformat(),
        "config": "baseline (Sprint 1 pipeline)",
        "n_queries": len(per_query),
        "metrics": {
            "recall_at_1": recall_at_1,
            "recall_at_3": recall_at_3,
            "mrr": mrr,
            "latency_p50_ms": p50,
        },
        "per_query": per_query,
    }, indent=2))
    print(f"\n→ saved to {out.relative_to(Path(__file__).parent.parent)}")


if __name__ == "__main__":
    main()