from pathlib import Path
from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["file"])

REPOS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data" / "repos"


@router.get("/file")
async def get_file(repo_id: str, path: str, start: int = 1, end: int = 80):
    """Return the source lines of a file at a given line range."""
    repo_root = REPOS_DIR / repo_id
    if not repo_root.exists():
        raise HTTPException(status_code=404, detail="Repository not found.")

    file_path = repo_root / path
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    window = lines[max(0, start - 5):min(len(lines), end + 5)]

    return {
        "path": path,
        "start": start,
        "end": end,
        "lines": [
            {"n": start + i, "text": line}
            for i, line in enumerate(window)
        ],
    }