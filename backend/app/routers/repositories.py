from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
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

    repo = await svc.create_repository(db, url)
    background.add_task(svc.run_indexing, repo.id, url)

    return {
        "id": repo.id,
        "name": repo.name,
        "status": repo.status,
        "message": "Indexing started.",
    }


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