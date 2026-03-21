"""Overseer (God Mode) API endpoints."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..events.bus import EventBus
from ..overseer.service import OverseerService
from ..storage.container import Container
from .deps import RouteDeps


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class OverseerStartRequest(BaseModel):  # noqa: D101
    objective: str
    advice: list[str] = Field(default_factory=list)


class OverseerAdviceRequest(BaseModel):  # noqa: D101
    text: str


class OverseerUnblockRequest(BaseModel):  # noqa: D101
    response: str


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

# Per-project overseer service cache (same pattern as terminal_services)
_overseer_services: dict[str, OverseerService] = {}


def _get_overseer(container: Container, bus: EventBus) -> OverseerService:
    key = str(container.project_dir)
    service = _overseer_services.get(key)
    if service is None:
        service = OverseerService(container, bus)
        _overseer_services[key] = service
    return service


def get_overseer_services() -> dict[str, OverseerService]:
    """Expose the service cache for lifespan shutdown."""
    return _overseer_services


def register_overseer_routes(router: APIRouter, deps: RouteDeps) -> None:
    """Register God Mode endpoints."""

    def _ctx(project_dir: Optional[str]) -> tuple[Container, EventBus, OverseerService]:
        container, bus, _orch = deps.ctx(project_dir)
        return container, bus, _get_overseer(container, bus)

    @router.post("/overseer/start")
    async def overseer_start(
        body: OverseerStartRequest,
        project_dir: Optional[str] = Query(None),
    ) -> dict[str, Any]:
        _container, _bus, service = _ctx(project_dir)
        try:
            state = service.start(body.objective, body.advice)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"overseer": state.to_dict()}

    @router.post("/overseer/stop")
    async def overseer_stop(
        project_dir: Optional[str] = Query(None),
    ) -> dict[str, Any]:
        _container, _bus, service = _ctx(project_dir)
        state = service.stop()
        return {"overseer": state.to_dict()}

    @router.get("/overseer/status")
    async def overseer_status(
        project_dir: Optional[str] = Query(None),
    ) -> dict[str, Any]:
        _container, _bus, service = _ctx(project_dir)
        state = service.get_state()
        return {"overseer": state.to_dict()}

    @router.post("/overseer/advice")
    async def add_advice(
        body: OverseerAdviceRequest,
        project_dir: Optional[str] = Query(None),
    ) -> dict[str, Any]:
        _container, _bus, service = _ctx(project_dir)
        state = service.add_advice(body.text)
        return {"overseer": state.to_dict()}

    @router.delete("/overseer/advice/{index}")
    async def remove_advice(
        index: int,
        project_dir: Optional[str] = Query(None),
    ) -> dict[str, Any]:
        _container, _bus, service = _ctx(project_dir)
        state = service.remove_advice(index)
        return {"overseer": state.to_dict()}

    @router.post("/overseer/unblock")
    async def unblock(
        body: OverseerUnblockRequest,
        project_dir: Optional[str] = Query(None),
    ) -> dict[str, Any]:
        _container, _bus, service = _ctx(project_dir)
        try:
            state = service.unblock(body.response)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"overseer": state.to_dict()}
