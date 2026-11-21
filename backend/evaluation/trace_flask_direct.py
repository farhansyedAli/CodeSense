"""Direct-exercise variant of Phase 2 for Flask.

The pytest + sys.setprofile combination is prohibitively slow on Windows
(>3 min for one test). Instead we exercise Flask's core request lifecycle
in-process and trace THAT — same mechanism, same output format, just a
faster path to a non-trivial set of dynamic edges.

The traced scenarios walk the public API: create an app, register routes,
dispatch requests, render templates, set and read cookies, read JSON. The
observed edges are ground truth for whatever got executed.

Output matches the pytest-driven tracer so downstream analytics don't care
which path produced the data.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


REPO = Path("data/repos/fa1df9ec-61f6-4497-ab7d-184bbc30aee5").resolve()
OUT  = Path("backend/evaluation/results/dynamic_edges_flask.json").resolve()


observed: set[tuple[str, str]] = set()


def _qualify(frame) -> str | None:
    code = frame.f_code
    try:
        abs_fn = Path(code.co_filename).resolve()
    except (OSError, ValueError):
        return None
    try:
        rel = abs_fn.relative_to(REPO)
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


def exercise() -> None:
    # Import flask from the repo, not from site-packages.
    sys.path.insert(0, str(REPO / "src"))
    # Flush any cached flask module (site-packages one).
    for mod in [m for m in list(sys.modules) if m == "flask" or m.startswith("flask.")]:
        del sys.modules[mod]

    sys.setprofile(_profiler)
    try:
        import flask  # noqa: F401
        from flask import Flask, jsonify, request, redirect, url_for, render_template_string

        app = Flask(__name__)
        app.config["SECRET_KEY"] = "test"

        @app.route("/")
        def index():
            return "hello"

        @app.route("/json", methods=["GET", "POST"])
        def j():
            if request.method == "POST":
                return jsonify(request.get_json(silent=True) or {})
            return jsonify({"ok": True})

        @app.route("/redir")
        def r():
            return redirect(url_for("index"))

        @app.route("/tmpl")
        def t():
            return render_template_string("hi {{ name }}", name="world")

        @app.errorhandler(404)
        def nf(e):
            return "nope", 404

        # Register a blueprint BEFORE the first request; Flask refuses after.
        from flask import Blueprint
        bp = Blueprint("bp", __name__, url_prefix="/bp")

        @bp.route("/x")
        def bpx():
            return "bpx"

        app.register_blueprint(bp)

        client = app.test_client()
        client.get("/")
        client.get("/json")
        client.post("/json", json={"a": 1})
        client.get("/redir", follow_redirects=False)
        client.get("/tmpl")
        client.get("/missing")
        client.get("/bp/x")

        with app.app_context():
            with app.test_request_context("/"):
                _ = request.path
    finally:
        sys.setprofile(None)


def main() -> None:
    exercise()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = [{"caller": c, "callee": e} for c, e in sorted(observed)]
    OUT.write_text(json.dumps(data, indent=2))
    print(json.dumps({
        "edge_count": len(data),
        "sample": data[:5],
    }, indent=2))


if __name__ == "__main__":
    main()
