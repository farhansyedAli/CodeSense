from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import repository_service as svc

router = APIRouter(tags=["repositories"])


@router.get("/repositories")
async def list_repositories(db: AsyncSession = Depends(get_db)):
    repos = await svc.list_repositories(db)
    return {
        "data": [
            {
                "id": r.id,
                "url": r.url,
                "name": r.name,
                "status": r.status,
                "stage": r.stage,
                "progress": r.progress,
                "chunk_count": r.chunk_count,
                "error": r.error_message,
                "created_at": r.created_at.isoformat(),
            }
            for r in repos
        ]
    }


@router.post("/repositories", status_code=202)
async def connect_repository(
    payload: dict,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    url = payload.get("url", "").strip()
    if not url or "github.com" not in url:
        raise HTTPException(status_code=400, detail="A valid GitHub URL is required.")

    # Connecting a repository that is already in the list re-uses its row
    # instead of creating a duplicate.
    repo = await svc.find_by_url(db, url)
    if repo and repo.status in svc.ACTIVE_STATUSES:
        return {"id": repo.id, "name": repo.name, "status": repo.status, "message": "Already indexing."}
    if repo:
        repo = await svc.reset_for_reindex(db, repo)
    else:
        repo = await svc.create_repository(db, url)
    background.add_task(svc.run_indexing, repo.id, repo.url)

    return {
        "id": repo.id,
        "name": repo.name,
        "status": repo.status,
        "message": "Indexing started.",
    }


@router.post("/repositories/{repo_id}/reindex", status_code=202)
async def reindex_repository(
    repo_id: str,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Index an existing repository again, updating the same row."""
    repo = await svc.get_repository(db, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found.")
    if repo.status in svc.ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="This repository is already being indexed.")
    repo = await svc.reset_for_reindex(db, repo)
    background.add_task(svc.run_indexing, repo.id, repo.url)
    return {"id": repo.id, "name": repo.name, "status": repo.status, "message": "Re-indexing started."}


@router.delete("/repositories/{repo_id}", status_code=204)
async def delete_repository(repo_id: str, db: AsyncSession = Depends(get_db)):
    """Remove a repository, its cloned source and its index files."""
    repo = await svc.get_repository(db, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found.")
    if repo.status in svc.ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="Wait for indexing to finish before deleting this repository.")
    await svc.delete_repository(db, repo)
    return Response(status_code=204)


@router.get("/repositories/{repo_id}/status")
async def get_status(repo_id: str, db: AsyncSession = Depends(get_db)):
    repo = await svc.get_repository(db, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found.")
    return {
        "id": repo.id,
        "status": repo.status,
        "stage": repo.stage,
        "progress": repo.progress,
        "error": repo.error_message,
    }