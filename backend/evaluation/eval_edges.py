"""Edge-precision / recall / F1 evaluator against hand-labelled ground truth.

Reads `backend/evaluation/ground_truth/<repo>_edges.json` and compares the
TRUE-labelled edges against both the baseline graph (identifier regex) and
the new AST graph. The false-labelled edges catch false positives that the
regex builder tends to produce (bare-name collisions, direction inversions,
etc.).

Metrics (primary, over the full GT set):

    TP  — GT.label=true  AND predicted graph has (src, tgt, edge_type)
    FP  — GT.label=false AND predicted graph has (src, tgt, edge_type)
    FN  — GT.label=true  AND predicted graph has no matching edge
    TN  — GT.label=false AND predicted graph has no matching edge

    precision = TP / (TP + FP)
    recall    = TP / (TP + FN)
    F1        = 2PR / (P + R)

Secondary: edge-type accuracy, computed on GT edges whose (src, tgt) pair is
present in the predicted graph. "Accuracy" here is the fraction of those
pairs whose edge_type in the graph matches the GT edge_type. This
distinguishes "we have the edge, right type" from "we have the edge, wrong
type".

Output: `backend/evaluation/results/edge_eval.json`.
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx


ROOT = Path(__file__).resolve().parent.parent.parent
GT   = ROOT / "backend/evaluation/ground_truth/flask_edges.json"
BASE = ROOT / "backend/evaluation/baseline/graph_flask_baseline.graphml"
NEW  = ROOT / "backend/evaluation/results/graph_flask_new.graphml"
OUT  = ROOT / "backend/evaluation/results/edge_eval.json"


def _edge_by_pair(g: nx.DiGraph, src: str, tgt: str):
    """Return (edge_data_dict | None). DiGraph has at most one edge per pair."""
    if g.has_edge(src, tgt):
        return g.get_edge_data(src, tgt) or {}
    return None


def _to_leaf(node_id: str) -> str:
    """Normalize a symbol id from qualname form to leaf-name form:
    `src/flask/app.py::Flask.__call__` -> `src/flask/app.py::__call__`.
    File / external ids pass through unchanged.
    """
    if "::" not in node_id or node_id.startswith("external::"):
        return node_id
    file, qn = node_id.split("::", 1)
    return f"{file}::{qn.rsplit('.', 1)[-1]}"


def _edge_by_pair_leaf(g: nx.DiGraph, src: str, tgt: str):
    """Leaf-normalized match: strips qualnames down to leaf names on BOTH the
    query and every graph edge. Used to give the old builder credit for call
    edges even though its node ids never carried qualnames.
    """
    src_l, tgt_l = _to_leaf(src), _to_leaf(tgt)
    for u, v, d in g.edges(data=True):
        if _to_leaf(u) == src_l and _to_leaf(v) == tgt_l:
            return d
    return None


def _evaluate(gt_edges: list[dict], g: nx.DiGraph, leaf_match: bool = False) -> dict:
    tp = fp = fn = tn = 0
    pair_present = 0
    type_correct = 0
    per_type: dict[str, dict[str, int]] = {}
    examples = {"fn": [], "fp": [], "tp": [], "tn": [], "wrong_type": []}

    pair_lookup = _edge_by_pair_leaf if leaf_match else _edge_by_pair
    for e in gt_edges:
        et = e["edge_type"]
        per_type.setdefault(et, {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
        found = pair_lookup(g, e["src"], e["tgt"])
        # The old builder didn't tag edges with edge_type; treat missing as
        # implicit "calls" so it gets credit for the calls it did find.
        found_type = (found or {}).get("edge_type", "calls")
        has_match = found is not None and found_type == et
        pair_only = found is not None

        if pair_only:
            pair_present += 1
            if found_type == et:
                type_correct += 1
            else:
                examples["wrong_type"].append({
                    "gt_edge": {"src": e["src"], "tgt": e["tgt"],
                                "gt_edge_type": et, "label": e["label"]},
                    "predicted_edge_type": found_type,
                })

        if e["label"] is True:
            if has_match:
                tp += 1; per_type[et]["tp"] += 1
                if len(examples["tp"]) < 5:
                    examples["tp"].append(e)
            else:
                fn += 1; per_type[et]["fn"] += 1
                if len(examples["fn"]) < 5:
                    examples["fn"].append(e)
        else:
            if has_match:
                fp += 1; per_type[et]["fp"] += 1
                if len(examples["fp"]) < 5:
                    examples["fp"].append(e)
            else:
                tn += 1; per_type[et]["tn"] += 1
                if len(examples["tn"]) < 5:
                    examples["tn"].append(e)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall    = tp / (tp + fn) if (tp + fn) else 0.0
    f1        = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    type_acc  = (type_correct / pair_present) if pair_present else 0.0

    return {
        "totals": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "precision": round(precision, 4),
        "recall":    round(recall, 4),
        "f1":        round(f1, 4),
        "pair_match_rate_over_gt": round(pair_present / len(gt_edges), 4) if gt_edges else 0.0,
        "edge_type_accuracy_on_matched_pairs": round(type_acc, 4),
        "per_edge_type": per_type,
        "examples": examples,
    }


def main() -> None:
    gt = json.loads(GT.read_text())
    gt_edges = gt["edges"]
    base = nx.read_graphml(BASE)
    new  = nx.read_graphml(NEW)

    # Baseline (strict and leaf-normalized). The old builder's node ids used
    # leaf names, not qualnames, so a strict match reports zero recall even on
    # edges it did find. The leaf-normalized score is the fair floor.
    baseline        = _evaluate(gt_edges, base)
    baseline_leaf   = _evaluate(gt_edges, base, leaf_match=True)
    newbuild        = _evaluate(gt_edges, new)

    label_counts = {"true": sum(1 for e in gt_edges if e["label"]),
                    "false": sum(1 for e in gt_edges if not e["label"])}

    result = {
        "repo": gt["repo"],
        "ground_truth_size": len(gt_edges),
        "label_counts": label_counts,
        "baseline_strict":  baseline,
        "baseline_leaf":    baseline_leaf,
        "new_builder":      newbuild,
        "delta_vs_leaf_baseline": {
            "precision": round(newbuild["precision"] - baseline_leaf["precision"], 4),
            "recall":    round(newbuild["recall"]    - baseline_leaf["recall"], 4),
            "f1":        round(newbuild["f1"]        - baseline_leaf["f1"], 4),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    summary = lambda d: {k: d[k] for k in ("totals","precision","recall","f1","edge_type_accuracy_on_matched_pairs")}
    print(json.dumps({
        "ground_truth_size": len(gt_edges),
        "baseline_strict":  summary(baseline),
        "baseline_leaf":    summary(baseline_leaf),
        "new_builder":      summary(newbuild),
        "delta_vs_leaf_baseline": result["delta_vs_leaf_baseline"],
    }, indent=2))


if __name__ == "__main__":
    main()
