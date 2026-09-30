from fastapi import APIRouter
from app.config import settings

router = APIRouter()


@router.get("/health", tags=["health"])
async def health_check():
    """Health check endpoint for deployment verification."""
    return {
        "status": "healthy",
        "app": settings.app_name,
        "version": settings.app_version,
    }