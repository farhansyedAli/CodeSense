import uuid
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import Repository
from indexing.pipeline import INDEXES_DIR, REPOS_DIR, index_repository, remove_dir

ACTIVE_STATUSES = ("pending", "indexing")


def _repo_name_from_url(url: str) -> str:
    """Extract 'owner/name' from a GitHub URL."""
    cleaned = url.rstrip("/").removesuffix(".git")
    parts = cleaned.split("github.com/")[-1].split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else cleaned


def normalize_url(url: str) -> str:
    """https://github.com/Owner/Repo(.git)(/) -> https://github.com/owner/repo"""
    name = _repo_name_from_url(url.strip())
    return f"https://github.com/{name}".lower()


async def find_by_url(db: AsyncSession, url: str) -> Repository | None:
    """Find an existing record for the same GitHub repository (case-insensitive)."""
    name = _repo_name_from_url(url.strip()).lower()
    result = await db.execute(
        select(Repository)
        .where(func.lower(Repository.name) == name)
        .order_by(Repository.created_at.desc())
    )
    return result.scalars().first()


async def reset_for_reindex(db: AsyncSession, repo: Repository) -> Repository:
    """Put an existing record back to 'pending' so the same row is re-indexed."""
    repo.status = "pending"
    repo.stage = None
    repo.progress = 0
    repo.error_message = None
    await db.commit()
    await db.refresh(repo)
    return repo


async def delete_repository(db: AsyncSession, repo: Repository) -> None:
    """Delete the DB record plus the cloned source and the index files on disk."""
    repo_id = repo.id
    await db.delete(repo)
    await db.commit()
    for folder in (REPOS_DIR / repo_id, INDEXES_DIR / repo_id):
        if folder.exists():
            remove_dir(folder)


async def create_repository(db: AsyncSession, url: str) -> Repository:
    """Insert a new repository record with status 'pending'."""
    repo_id = str(uuid.uuid4())
    repo = Repository(
        id=repo_id,
        url=url,
        name=_repo_name_from_url(url),
        status="pending",
        progress=0,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return repo


async def get_repository(db: AsyncSession, repo_id: str) -> Repository | None:
    result = await db.execute(select(Repository).where(Repository.id == repo_id))
    return result.scalar_one_or_none()


async def list_repositories(db: AsyncSession) -> list[Repository]:
    result = await db.execute(
        select(Repository).order_by(Repository.created_at.desc())
    )
    return list(result.scalars().all())


async def run_indexing(repo_id: str, url: str) -> None:
    """
    Background task: run the pipeline and update the DB as it progresses.

    This function opens its own DB session because background tasks run
    after the request session has been closed.
    """
    async with async_session() as db:
        repo = await get_repository(db, repo_id)
        if not repo:
            return
        repo.status = "indexing"
        repo.stage = "starting"
        repo.progress = 1
        await db.commit()

    # Progress callback runs synchronously inside the pipeline; we bridge
    # it back into async DB writes using a small helper.
    loop = None

    def on_progress(stage: str, percent: int):
        import asyncio
        nonlocal loop
        if loop is None:
            loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(
            _update_progress(repo_id, stage, percent), loop
        )

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        summary = await asyncio.to_thread(
            index_repository, repo_id, url, on_progress
        )

        async with async_session() as db:
            repo = await get_repository(db, repo_id)
            if repo:
                repo.status = "ready"
                repo.stage = "complete"
                repo.progress = 100
                repo.chunk_count = summary["chunk_count"]
                repo.indexed_at = datetime.utcnow()
                await db.commit()

    except Exception as e:
        async with async_session() as db:
            repo = await get_repository(db, repo_id)
            if repo:
                repo.status = "failed"
                repo.stage = "error"
                repo.error_message = str(e)[:500]
                await db.commit()


async def _update_progress(repo_id: str, stage: str, percent: int) -> None:
    async with async_session() as db:
        repo = await get_repository(db, repo_id)
        if repo:
            repo.stage = stage
            repo.progress = percent
            await db.commit()