import asyncio
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import osv_service
from app.services.dependency_parser import collect_dependencies

log = logging.getLogger(__name__)

router = APIRouter(tags=["security"])

REPOS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "repos"
_REPO_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class AnalyzeRequest(BaseModel):
    repo_id: str


def _empty_summary() -> dict:
    return {
        "packages_total": 0,
        "packages_vulnerable": 0,
        "packages_clean": 0,
        "packages_unchecked": 0,
        "vulnerabilities_total": 0,
        "by_severity": {"Critical": 0, "High": 0, "Medium": 0, "Low": 0, "Unknown": 0},
    }


def _by_ecosystem(results: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for r in results:
        g = groups.setdefault(
            r["ecosystem"],
            {"packages": 0, "vulnerable": 0, "clean": 0, "unchecked": 0, "vulnerabilities": 0},
        )
        g["packages"] += 1
        g[r["status"]] += 1
        g["vulnerabilities"] += len(r["vulnerabilities"])
    return groups


@router.post("/security/analyze")
async def analyze_dependencies(payload: AnalyzeRequest):
    """Check a repository's declared dependencies against OSV.dev.

    Reads requirements*.txt and package.json from the cloned repository. Source
    code is never scanned; only package names and versions are looked up.
    """
    repo_id = payload.repo_id.strip()
    if not _REPO_ID.match(repo_id):
        raise HTTPException(status_code=400, detail="Invalid repository id.")

    repo_root = REPOS_DIR / repo_id
    if not repo_root.is_dir():
        raise HTTPException(
            status_code=404,
            detail="Repository files not found. Make sure the repository finished indexing.",
        )

    found = await asyncio.to_thread(collect_dependencies, repo_root)
    base = {
        "repo_id": repo_id,
        "source": "OSV.dev",
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "manifests": found["manifests"],
        "truncated": found["truncated"],
        "skipped": found["skipped"],
    }

    if not found["dependencies"]:
        if found["manifests"]:
            message = "Dependency files were found, but no packages could be read from them."
        else:
            message = "No requirements.txt or package.json found in this repository."
        return {
            **base,
            "status": "no_dependencies",
            "message": message,
            "summary": _empty_summary(),
            "by_ecosystem": {},
            "results": [],
        }

    try:
        scan = await osv_service.scan_dependencies(found["dependencies"])
    except osv_service.OSVError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("Security analysis failed for repo %s", repo_id)
        raise HTTPException(
            status_code=500,
            detail="Security analysis failed unexpectedly. Check the server log.",
        )

    return {
        **base,
        "status": "ok",
        "message": None,
        "summary": scan["summary"],
        "by_ecosystem": _by_ecosystem(scan["results"]),
        "results": scan["results"],
    }