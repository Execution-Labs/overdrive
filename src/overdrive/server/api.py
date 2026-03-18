"""FastAPI app wiring for orchestrator-first runtime."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional, cast

from fastapi import FastAPI, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..runtime.api import create_router
from ..runtime.events import EventBus
from ..runtime.events import hub
from ..runtime.orchestrator import OrchestratorService, WorkerAdapter, create_orchestrator
from ..runtime.storage import Container


def _find_frontend_dist() -> Optional[Path]:
    """Locate the built frontend dist directory.

    Checks two locations:
    1. Bundled with the package: <package>/web_dist/
    2. Development repo layout: <repo_root>/web/dist/
    """
    # Bundled in the package (PyPI wheel)
    pkg_dist = Path(__file__).resolve().parent.parent / "web_dist"
    if (pkg_dist / "index.html").is_file():
        return pkg_dist

    # Development: repo root / web / dist
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    dev_dist = repo_root / "web" / "dist"
    if (dev_dist / "index.html").is_file():
        return dev_dist

    return None


def create_app(
    project_dir: Optional[Path] = None,
    enable_cors: bool = True,
    worker_adapter: Optional[WorkerAdapter] = None,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        project_dir (Optional[Path]): Default project directory used when request-level
            ``project_dir`` query parameters are not provided.
        enable_cors (bool): Whether to install permissive CORS middleware for browser
            clients.
        worker_adapter (Optional[WorkerAdapter]): Optional worker adapter forwarded to
            newly created orchestrator instances.

    Returns:
        FastAPI: Configured application instance with router endpoints, websocket
        bridge, and per-project container/orchestrator caches stored on
        ``app.state``.
    """
    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        hub.attach_loop(asyncio.get_running_loop())
        try:
            yield
        finally:
            orchestrators = list(getattr(app.state, "orchestrators", {}).values())
            for orchestrator in orchestrators:
                try:
                    orchestrator.shutdown(timeout=10.0)
                except Exception:
                    pass
            terminal_services = getattr(app.state, "terminal_services", {})
            for ts in terminal_services.values():
                try:
                    ts.shutdown()
                except Exception:
                    pass
            app.state.orchestrators = {}
            app.state.containers = {}
            app.state.import_jobs = {}
            app.state.terminal_services = {}

    app = FastAPI(
        title="Overdrive",
        description="Orchestrator-first AI engineering control center",
        version="3.0.0",
        lifespan=_lifespan,
    )

    if enable_cors:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.state.default_project_dir = project_dir
    app.state.containers = {}
    app.state.orchestrators = {}
    app.state.import_jobs = {}
    app.state.terminal_services = {}

    def _resolve_project_dir(project_dir_param: Optional[str] = None) -> Path:
        if project_dir_param:
            return Path(project_dir_param).expanduser().resolve()
        if app.state.default_project_dir:
            return Path(app.state.default_project_dir).resolve()
        return Path.cwd().resolve()

    def _resolve_container(project_dir_param: Optional[str] = None) -> Container:
        resolved = _resolve_project_dir(project_dir_param)
        key = str(resolved)
        cache = cast(dict[str, Container], app.state.containers)
        if key not in cache:
            cache[key] = Container(resolved)
        return cache[key]

    def _resolve_orchestrator(project_dir_param: Optional[str] = None) -> OrchestratorService:
        resolved = _resolve_project_dir(project_dir_param)
        key = str(resolved)
        cache = cast(dict[str, OrchestratorService], app.state.orchestrators)
        if key not in cache:
            container = _resolve_container(project_dir_param)
            bus_factory = cast(Any, app.state.bus_factory)
            cache[key] = create_orchestrator(container, bus=bus_factory(container), worker_adapter=worker_adapter)
        return cache[key]

    app.state.bus_factory = lambda container: EventBus(container.events, container.project_id)

    app.include_router(create_router(_resolve_container, _resolve_orchestrator, app.state.import_jobs, app.state.terminal_services))

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"status": "ok", "version": "3.0.0"}

    @app.get("/readyz")
    async def readyz(project_dir: Optional[str] = Query(None)) -> dict[str, object]:
        container = _resolve_container(project_dir)
        return {
            "status": "ready",
            "project": str(container.project_dir),
            "project_id": container.project_id,
            "orchestrators": len(app.state.orchestrators),
        }

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await hub.handle_connection(websocket)

    # Serve built frontend if web/dist/ exists, otherwise serve JSON at /
    _frontend_dist = _find_frontend_dist()
    if _frontend_dist is not None:
        _index_html = _frontend_dist / "index.html"
        _assets_dir = _frontend_dist / "assets"
        if _assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="static-assets")

        @app.get("/", include_in_schema=False)
        async def serve_index() -> FileResponse:
            return FileResponse(str(_index_html))

        @app.get("/{path:path}", include_in_schema=False)
        async def spa_fallback(path: str) -> FileResponse:
            # Serve actual static files (favicon, images, etc.)
            candidate = _frontend_dist / path
            if path and candidate.is_file() and ".." not in path:
                return FileResponse(str(candidate))
            # Everything else gets index.html (SPA client-side routing)
            return FileResponse(str(_index_html))
    else:
        @app.get("/")
        async def root(project_dir: Optional[str] = Query(None)) -> dict[str, object]:
            container = _resolve_container(project_dir)
            cfg = container.config.load()
            schema_version = cfg.get("schema_version")
            try:
                schema = int(schema_version) if schema_version is not None else 4
            except (TypeError, ValueError):
                schema = 4
            storage_backend = str(cfg.get("storage_backend") or "sqlite")
            return {
                "name": "Overdrive",
                "version": "3.0.0",
                "project": str(container.project_dir),
                "project_id": container.project_id,
                "schema_version": schema,
                "storage_backend": storage_backend,
            }

    return app


app = create_app()
