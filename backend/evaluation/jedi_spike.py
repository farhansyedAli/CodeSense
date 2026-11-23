"""Phase 3 — measure Jedi's resolution speed on real Flask files.

Spec: time it on the 10th file, not the first (Jedi caches modules). If the
mean is worse than ~5 s/file, STOP and ask the user before Phase 4.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import jedi


REPO = Path("data/repos/fa1df9ec-61f6-4497-ab7d-184bbc30aee5").resolve()
OUT  = Path("backend/evaluation/results/phase3_jedi_spike.json").resolve()

TARGETS = [
    "src/flask/__init__.py",
    "src/flask/app.py",
    "src/flask/blueprints.py",
    "src/flask/cli.py",
    "src/flask/config.py",
    "src/flask/ctx.py",
    "src/flask/helpers.py",
    "src/flask/sessions.py",
    "src/flask/testing.py",
    "src/flask/views.py",
]


def main() -> None:
    project = jedi.Project(str(REPO))
    results = []
    for rel in TARGETS:
        p = REPO / rel
        if not p.exists():
            continue
        try:
            script = jedi.Script(path=str(p), project=project)
        except Exception as e:
            results.append({"file": rel, "error": f"{type(e).__name__}: {e}"})
            continue

        t0 = time.time()
        try:
            names = list(script.get_names(references=True, all_scopes=True))
        except Exception as e:
            results.append({"file": rel, "error": f"get_names: {type(e).__name__}: {e}"})
            continue

        refs = len(names)
        resolved = 0
        err = None
        for n in names:
            try:
                defs = n.goto(follow_imports=True)
                if defs:
                    resolved += 1
            except Exception as e:
                err = f"goto: {type(e).__name__}: {e}"
        dt = time.time() - t0
        row = {
            "file": rel,
            "refs": refs,
            "resolved": resolved,
            "resolved_fraction": round(resolved / refs, 3) if refs else 0.0,
            "seconds": round(dt, 3),
        }
        if err:
            row["first_error"] = err
        results.append(row)
        print(json.dumps(row))

    # Representative = 10th file (0-indexed: 9) if we have it, else last
    non_err = [r for r in results if "seconds" in r]
    rep = non_err[-1] if non_err else None
    mean = (sum(r["seconds"] for r in non_err) / len(non_err)) if non_err else 0.0
    total = sum(r["seconds"] for r in non_err)
    summary = {
        "files_measured": len(non_err),
        "total_seconds": round(total, 3),
        "mean_seconds_per_file": round(mean, 3),
        "representative_file_seconds": rep["seconds"] if rep else None,
        "representative_file": rep["file"] if rep else None,
        "verdict": "OK" if mean <= 5.0 else "TOO SLOW — stop and consult user",
        "per_file": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2))
    print("---")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_file"}, indent=2))


if __name__ == "__main__":
    main()
