"""Git remote status and push route registration for the runtime API."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..orchestrator.git_remote import (
    generate_branch_name,
    generate_branch_name_llm,
    get_branch_status,
    push_to_remote,
)
from .deps import RouteDeps


class PushRequest(BaseModel):
    """Request body for pushing commits to a remote branch."""

    target_branch: Optional[str] = None
    auto_name: bool = False


def register_git_routes(router: APIRouter, deps: RouteDeps) -> None:
    """Register git status and push endpoints."""

    @router.get("/git/status")
    async def git_status(project_dir: Optional[str] = Query(None)) -> dict[str, Any]:
        """Return current branch name, remote tracking info, and ahead/behind counts.

        Args:
            project_dir: Optional project directory used to resolve runtime state.

        Returns:
            Branch status payload with commit list.
        """
        container, _, _ = deps.ctx(project_dir)
        status = await asyncio.to_thread(get_branch_status, container.project_dir)
        return {
            "branch": status.branch,
            "remote_branch": status.remote_branch,
            "ahead_count": status.ahead_count,
            "behind_count": status.behind_count,
            "commits": [
                {"sha": c.sha, "message": c.message} for c in status.commits
            ],
            "has_remote": status.has_remote,
        }

    @router.post("/git/push")
    async def git_push(
        body: PushRequest,
        project_dir: Optional[str] = Query(None),
    ) -> dict[str, Any]:
        """Push current branch to the remote.

        Args:
            body: Push configuration (target branch, auto-name flag).
            project_dir: Optional project directory used to resolve runtime state.

        Returns:
            Push result payload.

        Raises:
            HTTPException: If no remote is configured or push fails.
        """
        container, bus, _ = deps.ctx(project_dir)

        # Resolve target branch
        target = body.target_branch
        if body.auto_name and not target:
            target = await asyncio.to_thread(
                generate_branch_name, container.project_dir,
            )

        result = await asyncio.to_thread(
            push_to_remote, container.project_dir, target,
        )

        if not result.success:
            raise HTTPException(status_code=400, detail=result.error or "Push failed")

        bus.emit(
            channel="system",
            event_type="git.pushed",
            entity_id=container.project_id,
            payload={
                "remote_branch": result.remote_branch,
                "pushed_commits": result.pushed_commits,
            },
        )

        return {
            "success": result.success,
            "error": result.error,
            "remote_branch": result.remote_branch,
            "pushed_commits": result.pushed_commits,
        }

    @router.post("/git/suggest-branch-name")
    async def suggest_branch_name(
        project_dir: Optional[str] = Query(None),
    ) -> dict[str, Any]:
        """Use an LLM worker to suggest a branch name from ahead-of-remote commits.

        Args:
            project_dir: Optional project directory used to resolve runtime state.

        Returns:
            Object with ``branch_name`` (string or null) and ``error`` (string or null).
        """
        container, _, _ = deps.ctx(project_dir)

        status = await asyncio.to_thread(get_branch_status, container.project_dir)
        if status.ahead_count == 0 or not status.commits:
            return {"branch_name": None, "error": "No commits ahead of remote"}

        try:
            from ...workers.config import (
                get_workers_runtime_config,
                resolve_worker_for_step,
            )

            cfg = container.config.load()
            runtime = get_workers_runtime_config(
                config=cfg, codex_command_fallback="codex exec",
            )
            spec = resolve_worker_for_step(runtime, "summarize")
        except (ValueError, KeyError) as exc:
            return {
                "branch_name": None,
                "error": f"No worker configured for branch name generation: {exc}",
            }

        try:
            name = await asyncio.to_thread(
                generate_branch_name_llm,
                container.project_dir,
                status.commits,
                spec,
                container.state_root,
            )
        except Exception as exc:
            return {"branch_name": None, "error": str(exc)}

        return {"branch_name": name, "error": None}
