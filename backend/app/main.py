from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings
from app.database import engine, Base
from app.routers import health, repositories, langgraph_test
from app.models import Repository
from app.routers import health, repositories, query, file, langgraph_test

from app.routers import graph

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[CodeSense] Database tables ready.")
    yield
    await engine.dispose()
    print("[CodeSense] Shutdown complete.")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=settings.allowed_methods,
        allow_headers=settings.allowed_headers,
    )

    # API routes
    app.include_router(health.router)
    app.include_router(repositories.router, prefix="/api")
    app.include_router(langgraph_test.router)
    app.include_router(health.router)
    app.include_router(repositories.router, prefix="/api")
    app.include_router(query.router, prefix="/api")
    app.include_router(langgraph_test.router)
    app.include_router(file.router, prefix="/api")
    app.include_router(graph.router, prefix="/api")

    # Serve the existing HTML/CSS frontend (Sprint 0 only)
    # Path: Sprint 1/frontend/  (sibling of backend/)
    frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

        @app.get("/", include_in_schema=False)
        async def root():
            index = frontend_dir / "index.html"
            if index.exists():
                return FileResponse(str(index))
            return {"status": "ok", "message": "CodeSense API running"}

    return app


app = create_app()