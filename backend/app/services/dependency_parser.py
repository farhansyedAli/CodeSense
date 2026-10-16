"""Parse dependency manifests (requirements*.txt, package.json) into a flat list.

Pure functions: no network access and no framework imports, so they are easy to
test. Nothing here raises on bad input; problems are returned in the `skipped`
list so the caller can show them to the user.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

PYPI = "PyPI"
NPM = "npm"

SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "env", "__pycache__",
    "dist", "build", "site-packages", ".tox",
}
MAX_DEPTH = 4            # how many folders below the repo root to search
MAX_MANIFESTS = 25       # safety cap on files scanned per repository
MAX_FILE_BYTES = 2_000_000


@dataclass
class Dependency:
    ecosystem: str            # "PyPI" or "npm"
    name: str                 # normalised name used for the lookup
    version: str | None       # version that will be checked; None if unknown
    declared: str             # what the file literally says, e.g. "^4.17.0"
    exact: bool               # True only for exact pins or lockfile versions
    source_file: str          # manifest path relative to the repo root
    dev: bool = False         # npm devDependencies
    note: str | None = None   # why the version is inferred or missing

    def to_dict(self) -> dict:
        return asdict(self)


def _skip(file: str, entry: str, reason: str) -> dict:
    return {"file": file, "entry": entry[:80], "reason": reason}


# ---------------------------------------------------------------- requirements.txt

_REQ_LINE = re.compile(
    r"^([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)\s*(?:\[[^\]]*\])?\s*(.*)$"
)
_SPEC = re.compile(r"(===|==|~=|>=|<=|!=|>|<)\s*([A-Za-z0-9][A-Za-z0-9._!+*-]*)")


def normalize_pypi_name(name: str) -> str:
    """PEP 503 normalisation: 'Sentence_Transformers' -> 'sentence-transformers'."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _logical_lines(text: str) -> list[str]:
    """Join lines that end with a backslash continuation."""
    lines, buf = [], ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buf += line[:-1] + " "
        else:
            lines.append(buf + line)
            buf = ""
    if buf:
        lines.append(buf)
    return lines


def parse_requirements(text: str, source_file: str) -> tuple[list[Dependency], list[dict]]:
    deps: list[Dependency] = []
    skipped: list[dict] = []

    for line in _logical_lines(text):
        line = re.sub(r"(^|\s)#.*$", "", line).strip()   # strip comments
        if not line:
            continue
        if line.startswith("-"):
            skipped.append(_skip(source_file, line, "pip option or include, not a package"))
            continue
        if "://" in line or line.startswith(("git+", ".", "/")):
            skipped.append(_skip(source_file, line, "URL or local path, cannot be looked up"))
            continue

        line = line.split(";", 1)[0]                      # drop environment markers
        line = re.sub(r"\s--hash=\S+", "", line).strip()  # drop hash options

        m = _REQ_LINE.match(line)
        if not m:
            skipped.append(_skip(source_file, line, "unrecognised line"))
            continue

        raw_name, rest = m.group(1), m.group(2)
        if rest and not rest.startswith(("=", "<", ">", "!", "~", "(")):
            skipped.append(_skip(source_file, line, "unrecognised line"))
            continue
        specs = _SPEC.findall(rest)
        declared = rest.strip()

        pinned = next((v for op, v in specs if op in ("==", "===") and "*" not in v), None)
        wildcard = next((v for op, v in specs if op == "==" and "*" in v), None)
        lower = next((v for op, v in specs if op in (">=", "~=", ">")), None)

        if pinned:
            version, exact, note = pinned, True, None
        elif wildcard:
            version = wildcard.replace(".*", "").replace("*", "") or None
            exact, note = False, "wildcard pin; base version checked"
        elif lower:
            version, exact = lower, False
            note = "version range; lowest allowed version checked"
        else:
            version, exact = None, False
            note = "no version specified, cannot check"

        deps.append(Dependency(
            ecosystem=PYPI,
            name=normalize_pypi_name(raw_name),
            version=version,
            declared=declared or "(any)",
            exact=exact,
            source_file=source_file,
            note=note,
        ))

    return deps, skipped


# ---------------------------------------------------------------- package.json

_NON_REGISTRY = re.compile(
    r"^(git|github:|gitlab:|bitbucket:|file:|link:|workspace:|catalog:|portal:|patch:|https?:)",
    re.IGNORECASE,
)
_NPM_VERSION = re.compile(
    r"^(?:\^|~|>=|>|=)?v?(\d+)(?:\.(\d+|[xX*]))?(?:\.(\d+|[xX*]))?(-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_NPM_EXACT = re.compile(r"^=?v?\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")


def _npm_base_version(spec: str) -> tuple[str | None, bool]:
    """Return (version, exact) for a semver range, or (None, False) if unusable."""
    s = spec.strip()
    if not s or s.lower() in ("*", "x", "latest", "next"):
        return None, False
    first = s.split("||")[0].strip()          # for "a || b" check the first alternative
    for token in first.split():
        if token.startswith("<"):             # upper bounds say nothing about the base
            continue
        m = _NPM_VERSION.match(token)
        if not m:
            continue
        major = m.group(1)
        minor = m.group(2) if m.group(2) and m.group(2).isdigit() else "0"
        patch = m.group(3) if m.group(3) and m.group(3).isdigit() else "0"
        version = f"{major}.{minor}.{patch}{m.group(4) or ''}"
        return version, bool(_NPM_EXACT.match(first))
    return None, False


def _lockfile_versions(lock_text: str) -> dict[str, str]:
    """Top-level package versions from package-lock.json (lockfile v1, v2 and v3)."""
    data = json.loads(lock_text)
    versions: dict[str, str] = {}
    packages = data.get("packages")
    if isinstance(packages, dict):
        prefix = "node_modules/"
        for key, info in packages.items():
            if key.startswith(prefix) and isinstance(info, dict):
                name = key[len(prefix):]
                if "node_modules/" not in name and info.get("version"):
                    versions[name] = str(info["version"])
    legacy = data.get("dependencies")
    if not versions and isinstance(legacy, dict):
        for name, info in legacy.items():
            if isinstance(info, dict) and info.get("version"):
                versions[name] = str(info["version"])
    return versions


def parse_package_json(
    text: str, source_file: str, lock_text: str | None = None
) -> tuple[list[Dependency], list[dict]]:
    deps: list[Dependency] = []
    skipped: list[dict] = []

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return [], [_skip(source_file, source_file, f"invalid JSON: {exc}")]
    if not isinstance(data, dict):
        return [], [_skip(source_file, source_file, "package.json is not a JSON object")]

    locked: dict[str, str] = {}
    lock_name = source_file.rsplit("/", 1)[0] + "/package-lock.json" if "/" in source_file else "package-lock.json"
    if lock_text:
        try:
            locked = _lockfile_versions(lock_text)
        except (json.JSONDecodeError, ValueError, AttributeError):
            skipped.append(_skip(lock_name, lock_name, "lockfile could not be parsed, using declared ranges"))

    sections = (
        ("dependencies", False),
        ("devDependencies", True),
        ("optionalDependencies", False),
    )
    for section, is_dev in sections:
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        for name, spec in block.items():
            if not isinstance(spec, str):
                skipped.append(_skip(source_file, f"{name}", "version is not a string"))
                continue
            real_name = name
            # alias form: "npm:real-package@^1.2.3"
            if spec.startswith("npm:") and "@" in spec[4:]:
                real_name, spec = spec[4:].rsplit("@", 1)
            if _NON_REGISTRY.match(spec) or "/" in spec:
                skipped.append(_skip(source_file, f"{name}: {spec}", "git, URL or local dependency, cannot be looked up"))
                continue

            if real_name in locked:
                version, exact = locked[real_name], True
                note = f"exact version taken from {lock_name}"
            else:
                version, exact = _npm_base_version(spec)
                if version is None:
                    note = "no usable version in range, cannot check"
                elif exact:
                    note = None
                else:
                    note = "version range; base version checked (no lockfile entry)"

            deps.append(Dependency(
                ecosystem=NPM,
                name=real_name,
                version=version,
                declared=spec or "(any)",
                exact=exact,
                source_file=source_file,
                dev=is_dev,
                note=note,
            ))

    return deps, skipped


# ---------------------------------------------------------------- discovery

def _is_requirements_file(path: Path) -> bool:
    name = path.name.lower()
    if not name.endswith(".txt"):
        return False
    if name.startswith("requirements") or name.endswith("-requirements.txt"):
        return True
    return path.parent.name.lower() == "requirements"


def discover_manifests(repo_root: Path) -> tuple[list[tuple[str, str]], bool]:
    """Find manifests under repo_root. Returns ([(relative_path, kind)], truncated)."""
    found: list[tuple[int, str, str]] = []
    root = str(repo_root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        for fname in filenames:
            full = Path(dirpath) / fname
            if full.is_symlink():
                continue
            kind = None
            if fname == "package.json":
                kind = "package.json"
            elif _is_requirements_file(full):
                kind = "requirements"
            if kind:
                rel = full.relative_to(repo_root).as_posix()
                found.append((depth, rel, kind))
    found.sort()
    truncated = len(found) > MAX_MANIFESTS
    return [(rel, kind) for _, rel, kind in found[:MAX_MANIFESTS]], truncated


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def collect_dependencies(repo_root: Path) -> dict:
    """Scan a cloned repository and return every dependency found."""
    manifests, truncated = discover_manifests(repo_root)
    dependencies: list[Dependency] = []
    skipped: list[dict] = []

    for rel, kind in manifests:
        text = _read(repo_root / rel)
        if text is None:
            skipped.append(_skip(rel, rel, "file too large or unreadable"))
            continue
        if kind == "requirements":
            d, s = parse_requirements(text, rel)
        else:
            lock_rel = rel.rsplit("/", 1)[0] + "/package-lock.json" if "/" in rel else "package-lock.json"
            lock_text = _read(repo_root / lock_rel)
            d, s = parse_package_json(text, rel, lock_text)
        dependencies.extend(d)
        skipped.extend(s)

    return {
        "manifests": [rel for rel, _ in manifests],
        "dependencies": dependencies,
        "skipped": skipped,
        "truncated": truncated,
    }