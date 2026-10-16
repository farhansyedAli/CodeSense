"""Look up known vulnerabilities for parsed dependencies using OSV.dev.

Flow:
  1. POST /v1/querybatch with every unique (ecosystem, name, version). The
     response only contains advisory IDs, in the same order as the queries.
  2. GET /v1/vulns/{id} for each distinct ID to get the summary, severity and
     fixed versions.
  3. Compute a numeric CVSS score, classify severity, de-duplicate aliases
     (the same flaw often appears as a PYSEC, GHSA and CVE record).

OSV needs no API key. Any failure of the batch lookup raises OSVError so the
router can return a clear message; a failure to fetch one advisory only
degrades that advisory.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time

import httpx

from app.services.dependency_parser import Dependency

log = logging.getLogger(__name__)

OSV_BASE = "https://api.osv.dev/v1"
BATCH_SIZE = 500            # OSV accepts up to 1000 per request
MAX_PAGES = 5               # follow-up pages for queries with next_page_token
DETAIL_CONCURRENCY = 10
TIMEOUT_SECONDS = 20.0
CACHE_TTL_SECONDS = 3600

SEVERITY_ORDER = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1, "Unknown": 0}

_DETAIL_CACHE: dict[str, tuple[float, dict | None]] = {}


class OSVError(Exception):
    """OSV.dev could not be reached or returned unusable data."""


# ---------------------------------------------------------------- CVSS

_AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
_AC = {"L": 0.77, "H": 0.44}
_UI = {"N": 0.85, "R": 0.62}
_CIA = {"H": 0.56, "L": 0.22, "N": 0.0}


def _roundup(x: float) -> float:
    """CVSS v3.1 Roundup: smallest number with one decimal >= x."""
    i = int(round(x * 100000))
    if i % 10000 == 0:
        return i / 100000.0
    return (math.floor(i / 10000) + 1) / 10.0


def cvss3_base_score(vector: str) -> float | None:
    """Base score from a CVSS v3.0/v3.1 vector string, or None if unusable."""
    try:
        if not vector.startswith(("CVSS:3.0/", "CVSS:3.1/")):
            return None
        m = dict(part.split(":", 1) for part in vector.split("/")[1:])
        scope_changed = m["S"] == "C"
        pr_table = (
            {"N": 0.85, "L": 0.68, "H": 0.5}
            if scope_changed
            else {"N": 0.85, "L": 0.62, "H": 0.27}
        )
        iss = 1 - (1 - _CIA[m["C"]]) * (1 - _CIA[m["I"]]) * (1 - _CIA[m["A"]])
        if scope_changed:
            impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
        else:
            impact = 6.42 * iss
        exploitability = 8.22 * _AV[m["AV"]] * _AC[m["AC"]] * pr_table[m["PR"]] * _UI[m["UI"]]
        if impact <= 0:
            return 0.0
        if scope_changed:
            return _roundup(min(1.08 * (impact + exploitability), 10))
        return _roundup(min(impact + exploitability, 10))
    except (KeyError, ValueError):
        return None


def severity_from_score(score: float | None) -> str:
    if score is None:
        return "Unknown"
    if score >= 9.0:
        return "Critical"
    if score >= 7.0:
        return "High"
    if score >= 4.0:
        return "Medium"
    if score > 0.0:
        return "Low"
    return "Unknown"   # 0.0 means "None" in CVSS; nothing useful to show


_BUCKETS = {"CRITICAL": "Critical", "HIGH": "High", "MODERATE": "Medium",
            "MEDIUM": "Medium", "LOW": "Low"}


def extract_severity(vuln: dict) -> tuple[float | None, str, str | None]:
    """Return (score, severity, vector) for an OSV advisory.

    Order of preference: computed CVSS v3 score, then the severity word that
    GitHub advisories carry in database_specific. CVSS v4 vectors are kept for
    display only (they are not scored here).
    """
    vector = None
    for entry in vuln.get("severity") or []:
        raw = str(entry.get("score", "")).strip()
        if raw.startswith("CVSS:"):
            vector = vector or raw
            score = cvss3_base_score(raw)
            if score is not None:
                return score, severity_from_score(score), raw
        else:
            try:                              # some sources give a plain number
                score = float(raw)
                return score, severity_from_score(score), vector
            except ValueError:
                continue
    word = str((vuln.get("database_specific") or {}).get("severity", "")).upper()
    for entry in vuln.get("affected") or []:  # some feeds put it per package
        word = word or str((entry.get("database_specific") or {}).get("severity", "")).upper()
    return None, _BUCKETS.get(word, "Unknown"), vector


# ---------------------------------------------------------------- fixed version

def _vkey(version: str) -> tuple[int, ...]:
    """Crude version key: the numeric parts. Good enough to order release versions."""
    return tuple(int(n) for n in re.findall(r"\d+", version.split("+")[0]))


def _cmp(a: str, b: str) -> int:
    ka, kb = _vkey(a), _vkey(b)
    size = max(len(ka), len(kb))
    ka += (0,) * (size - len(ka))
    kb += (0,) * (size - len(kb))
    return (ka > kb) - (ka < kb)


def fixed_version(vuln: dict, ecosystem: str, name: str, installed: str) -> str | None:
    """Lowest fixed version above the installed one for this package."""
    candidates: list[str] = []
    for aff in vuln.get("affected") or []:
        pkg = aff.get("package") or {}
        if pkg.get("ecosystem") != ecosystem:
            continue
        pkg_name = str(pkg.get("name", ""))
        if ecosystem == "PyPI":
            pkg_name = re.sub(r"[-_.]+", "-", pkg_name).lower()
        if pkg_name != name:
            continue
        for rng in aff.get("ranges") or []:
            if rng.get("type") not in ("ECOSYSTEM", "SEMVER"):
                continue
            for event in rng.get("events") or []:
                fixed = event.get("fixed")
                if fixed:
                    candidates.append(str(fixed))
    try:
        above = [v for v in candidates if _cmp(v, installed) > 0]
    except Exception:
        return None
    if not above:
        return None
    best = above[0]
    for v in above[1:]:
        if _cmp(v, best) < 0:
            best = v
    return best


# ---------------------------------------------------------------- OSV calls

async def _post_json(client: httpx.AsyncClient, url: str, payload: dict) -> dict:
    try:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException as exc:
        raise OSVError("OSV.dev timed out. Try again in a moment.") from exc
    except httpx.HTTPStatusError as exc:
        raise OSVError(f"OSV.dev returned HTTP {exc.response.status_code}.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise OSVError("Could not reach OSV.dev. Check the server's internet connection.") from exc


async def _query_ids(client: httpx.AsyncClient, packages: list[tuple[str, str, str]]) -> dict:
    """Map (ecosystem, name, version) -> set of advisory IDs."""
    found: dict[tuple[str, str, str], set[str]] = {p: set() for p in packages}

    for start in range(0, len(packages), BATCH_SIZE):
        pending = [(p, None) for p in packages[start:start + BATCH_SIZE]]   # (package, page_token)
        for _ in range(MAX_PAGES):
            if not pending:
                break
            queries = []
            for (eco, name, ver), token in pending:
                q = {"package": {"name": name, "ecosystem": eco}, "version": ver}
                if token:
                    q["page_token"] = token
                queries.append(q)
            data = await _post_json(client, f"{OSV_BASE}/querybatch", {"queries": queries})
            results = data.get("results")
            if not isinstance(results, list) or len(results) != len(pending):
                raise OSVError("OSV.dev returned an unexpected response.")
            next_pending = []
            for (pkg, _), result in zip(pending, results):
                for v in (result or {}).get("vulns") or []:
                    if v.get("id"):
                        found[pkg].add(v["id"])
                token = (result or {}).get("next_page_token")
                if token:
                    next_pending.append((pkg, token))
            pending = next_pending
    return found


async def _get_detail(client: httpx.AsyncClient, vuln_id: str, sem: asyncio.Semaphore) -> dict | None:
    cached = _DETAIL_CACHE.get(vuln_id)
    if cached and time.time() - cached[0] < CACHE_TTL_SECONDS and cached[1] is not None:
        return cached[1]
    async with sem:
        try:
            resp = await client.get(f"{OSV_BASE}/vulns/{vuln_id}")
            resp.raise_for_status()
            detail = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("OSV detail fetch failed for %s: %s", vuln_id, exc)
            return None
    _DETAIL_CACHE[vuln_id] = (time.time(), detail)
    return detail


# ---------------------------------------------------------------- assembling

def _build_vuln(vuln_id: str, detail: dict | None, eco: str, name: str, version: str) -> dict:
    if detail is None:
        return {
            "id": vuln_id, "cve": vuln_id if vuln_id.startswith("CVE-") else None,
            "aliases": [], "summary": "Details could not be loaded from OSV.dev.",
            "score": None, "severity": "Unknown", "vector": None,
            "fixed_version": None, "url": f"https://osv.dev/vulnerability/{vuln_id}",
        }
    aliases = [a for a in detail.get("aliases") or [] if isinstance(a, str)]
    cve = next((a for a in [vuln_id, *aliases] if a.startswith("CVE-")), None)
    score, severity, vector = extract_severity(detail)
    summary = (detail.get("summary") or "").strip()
    if not summary:
        summary = (detail.get("details") or "").strip().split("\n")[0][:200] or "No description provided."
    return {
        "id": vuln_id,
        "cve": cve,
        "aliases": aliases,
        "summary": summary,
        "score": score,
        "severity": severity,
        "vector": vector,
        "fixed_version": fixed_version(detail, eco, name, version),
        "url": f"https://osv.dev/vulnerability/{vuln_id}",
    }


def _dedupe(vulns: list[dict]) -> list[dict]:
    """Merge records that are aliases of each other, keeping the best severity data."""
    merged: list[tuple[set[str], dict]] = []
    for v in vulns:
        ids = {v["id"], *v["aliases"]}
        for i, (seen, kept) in enumerate(merged):
            if ids & seen:
                if SEVERITY_ORDER[v["severity"]] > SEVERITY_ORDER[kept["severity"]] or (
                    v["score"] is not None and kept["score"] is None
                ):
                    v["cve"] = v["cve"] or kept["cve"]
                    v["fixed_version"] = v["fixed_version"] or kept["fixed_version"]
                    merged[i] = (seen | ids, v)
                else:
                    kept["cve"] = kept["cve"] or v["cve"]
                    kept["fixed_version"] = kept["fixed_version"] or v["fixed_version"]
                    merged[i] = (seen | ids, kept)
                break
        else:
            merged.append((ids, v))
    return [kept for _, kept in merged]


def _group(deps: list[Dependency]) -> dict[tuple, dict]:
    """One entry per unique package version, remembering where it was declared."""
    groups: dict[tuple, dict] = {}
    for d in deps:
        key = (d.ecosystem, d.name, d.version or f"declared:{d.declared}")
        g = groups.setdefault(key, {
            "ecosystem": d.ecosystem, "name": d.name, "version": d.version,
            "declared": d.declared, "exact": d.exact, "note": d.note,
            "source_files": [], "dev": True,
        })
        if d.source_file not in g["source_files"]:
            g["source_files"].append(d.source_file)
        g["dev"] = g["dev"] and d.dev
        g["exact"] = g["exact"] or d.exact
    return groups


async def scan_dependencies(
    deps: list[Dependency], client: httpx.AsyncClient | None = None
) -> dict:
    """Check dependencies against OSV.dev. Raises OSVError if OSV is unreachable."""
    groups = _group(deps)
    checkable = [(g["ecosystem"], g["name"], g["version"]) for g in groups.values() if g["version"]]

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        ids_by_pkg = await _query_ids(client, checkable) if checkable else {}
        all_ids = sorted({i for ids in ids_by_pkg.values() for i in ids})
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
        details = dict(zip(all_ids, await asyncio.gather(
            *(_get_detail(client, i, sem) for i in all_ids)
        )))
    finally:
        if own_client:
            await client.aclose()

    results = []
    for g in groups.values():
        entry = {**g, "vulnerabilities": [], "status": "unchecked", "max_severity": None}
        if g["version"]:
            pkg = (g["ecosystem"], g["name"], g["version"])
            vulns = _dedupe([
                _build_vuln(i, details.get(i), g["ecosystem"], g["name"], g["version"])
                for i in sorted(ids_by_pkg.get(pkg, ()))
            ])
            vulns.sort(key=lambda v: (-SEVERITY_ORDER[v["severity"]], -(v["score"] or 0), v["id"]))
            entry["vulnerabilities"] = vulns
            entry["status"] = "vulnerable" if vulns else "clean"
            if vulns:
                entry["max_severity"] = vulns[0]["severity"]
        results.append(entry)

    status_rank = {"vulnerable": 0, "unchecked": 1, "clean": 2}
    results.sort(key=lambda r: (
        status_rank[r["status"]], -SEVERITY_ORDER.get(r["max_severity"] or "Unknown", 0),
        r["ecosystem"], r["name"],
    ))

    severity_counts = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0, "Unknown": 0}
    for r in results:
        for v in r["vulnerabilities"]:
            severity_counts[v["severity"]] += 1

    return {
        "results": results,
        "summary": {
            "packages_total": len(results),
            "packages_vulnerable": sum(r["status"] == "vulnerable" for r in results),
            "packages_clean": sum(r["status"] == "clean" for r in results),
            "packages_unchecked": sum(r["status"] == "unchecked" for r in results),
            "vulnerabilities_total": sum(severity_counts.values()),
            "by_severity": severity_counts,
        },
    }