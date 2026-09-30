from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

router = APIRouter(tags=["repositories"])


@router.get("/repositories")
async def list_repositories(db: AsyncSession = Depends(get_db)):
    """List all connected repositories.

    Sprint 0: returns an empty list.
    Sprint 3: reads from PostgreSQL.
    """
    return {"data": []}


@router.post("/repositories")
async def connect_repository(payload: dict, db: AsyncSession = Depends(get_db)):
    """Connect a new repository.

    Sprint 0: acknowledges the request without doing real work.
    Sprint 1: clones and indexes the repository.
    """
    return {
        "status": "received",
        "url": payload.get("url"),
        "message": "Indexing not yet implemented — Sprint 1 deliverable",
    }