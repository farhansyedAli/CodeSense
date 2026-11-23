"""Phase 2 — dynamic call trace via sys.setprofile.

Runs the target repo's own test suite (pytest) under a profiler that records
every (caller, callee) pair where BOTH sides are files inside the repo. These
edges definitely exist — they executed — so they're labeled source="dynamic",
confidence="dynamic".

Caller/callee ids use the SAME `<file_path>::<qualname>` format the chunker
produces, so the dynamic set JOINs directly with the static edges.

Best-effort: if the target repo's test deps aren't installed, record zero
edges and keep going. Phase 2's job is to produce a confidence layer, not a
blocker.

CLI:

    python backend/evaluation/trace_calls.py \
        --repo data/repos/<repo-id> \
        --tests tests/test_blueprints.py::test_blueprint_specific_error_handler \
        --out backend/evaluation/results/dynamic_edges_flask.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


TRACER_SCRIPT = r'''
import json, os, sys
from pathlib import Path

REPO_ROOT = Path(os.environ["CODESENSE_TRACE_REPO"]).resolve()
OUT_PATH  = Path(os.environ["CODESENSE_TRACE_OUT"]).resolve()

observed: set = set()

def _qualify(frame) -> str | None:
    code = frame.f_code
    fn = code.co_filename
    try:
        abs_fn = Path(fn).resolve()
    except (OSError, ValueError):
        return None
    try:
        rel = abs_fn.relative_to(REPO_ROOT)
    except ValueError:
        return None
    rel_s = str(rel).replace("\\", "/")
    qn = getattr(code, "co_qualname", code.co_name)
    if qn == "<module>":
        return None
    return f"{rel_s}::{qn}"

def _profiler(frame, event, arg):
    if event != "call":
        return
    callee = _qualify(frame)
    if callee is None:
        return
    back = frame.f_back
    caller = _qualify(back) if back is not None else None
    if caller and caller != callee:
        observed.add((caller, callee))

def install():
    sys.setprofile(_profiler)

def dump():
    sys.setprofile(None)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(
        [{"caller": c, "callee": e} for c, e in sorted(observed)],
        indent=2,
    ))

import atexit
atexit.register(dump)
install()
'''


def _write_sitecustomize(target_dir: Path) -> Path:
    """Write `sitecustomize.py` in target_dir; Python imports it on startup
    when the directory is on PYTHONPATH, which starts the profiler before
    any test code runs.
    """
    p = target_dir / "sitecustomize.py"
    p.write_text(TRACER_SCRIPT)
    return p


def run(repo: Path, tests: list[str] | None, out: Path) -> dict:
    repo = repo.resolve()
    out = out.resolve()

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        _write_sitecustomize(td)

        env = os.environ.copy()
        env["CODESENSE_TRACE_REPO"] = str(repo)
        env["CODESENSE_TRACE_OUT"] = str(out)
        # PYTHONPATH: tracer dir (for sitecustomize) + repo src/ layouts so the
        # test suite imports the LOCAL repo, not whatever's pip-installed.
        parts = [str(td)]
        src_candidate = repo / "src"
        if src_candidate.is_dir():
            parts.append(str(src_candidate))
        parts.append(str(repo))
        if env.get("PYTHONPATH"):
            parts.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(parts)

        argv = [sys.executable, "-m", "pytest", "-q", "--no-header", "-x",
                "--disable-warnings", "-p", "no:cacheprovider"]
        if tests:
            argv.extend(tests)
        try:
            proc = subprocess.run(
                argv, cwd=str(repo), env=env, capture_output=True, text=True,
                timeout=600,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            return {"ok": False, "reason": f"{type(e).__name__}: {e}",
                    "stdout": "", "stderr": ""}

    info = {
        "ok": out.exists(),
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-800:],
        "stderr_tail": (proc.stderr or "")[-800:],
    }
    if out.exists():
        data = json.loads(out.read_text())
        info["edge_count"] = len(data)
    return info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--tests", nargs="*", default=None,
                    help="pytest node-ids / file patterns; omit to run full suite")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    info = run(args.repo, args.tests, args.out)
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    main()
