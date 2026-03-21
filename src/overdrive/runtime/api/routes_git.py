"""Git remote status and push route registration for the runtime API."""

from __future__ import annotations

import asyncio
import subprocess
import threading
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..orchestrator.git_remote import (
    PushCancelledError,
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
    # Mutable holder for the cancel event of the currently running push.
    _push_cancel: dict[str, threading.Event] = {}

    @router.get("/git/status")
    async def git_status(project_dir: Optional[str] = Query(None)) -> dict[str, Any]:
        """Return current branch name, remote tracking info, and ahead/behind counts."""
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
        """Push current branch to the remote."""
        container, bus, _ = deps.ctx(project_dir)
        pid = container.project_id

        # Resolve target branch
        target = body.target_branch
        if body.auto_name and not target:
            from ...workers.config import (
                get_workers_runtime_config,
                resolve_worker_for_step,
            )

            status = await asyncio.to_thread(get_branch_status, container.project_dir)
            cfg = container.config.load()
            runtime = get_workers_runtime_config(
                config=cfg, codex_command_fallback="codex exec",
            )
            spec = resolve_worker_for_step(runtime, "summarize")
            target = await asyncio.to_thread(
                generate_branch_name_llm,
                container.project_dir,
                status.commits,
                spec,
                container.state_root,
            )

        cancel_event = threading.Event()
        _push_cancel[pid] = cancel_event

        def _on_push_output(line: str) -> None:
            bus.emit(
                channel="system",
                event_type="git.push_output",
                entity_id=pid,
                payload={"line": line},
            )

        try:
            result = await asyncio.to_thread(
                push_to_remote, container.project_dir, target,
                _on_push_output, cancel_event,
            )
        except PushCancelledError:
            raise HTTPException(status_code=499, detail="Push cancelled")
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=504,
                detail="Push timed out — a pre-push hook (e.g. tests) may be taking too long",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Push failed: {exc}",
            ) from exc
        finally:
            _push_cancel.pop(pid, None)

        if not result.success:
            # If auto-fix is enabled, launch a worker to fix + commit + push.
            # The orchestrator doesn't manage the retry — the worker owns it.
            auto_fix_launched = False
            cfg = container.config.load()
            if (cfg.get("orchestrator") or {}).get("auto_fix_push", False):
                auto_fix_launched = await _launch_auto_fix_worker(
                    container, bus, pid, result.error or "", target,
                )

            if not auto_fix_launched:
                raise HTTPException(
                    status_code=400, detail=result.error or "Push failed",
                )

            return {
                "success": False,
                "error": result.error,
                "remote_branch": result.remote_branch or "",
                "pushed_commits": 0,
                "auto_fix": True,
            }

        bus.emit(
            channel="system",
            event_type="git.pushed",
            entity_id=pid,
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

    async def _launch_auto_fix_worker(
        container: Any, bus: Any, pid: str, error_output: str,
        target: str | None,
    ) -> bool:
        """Launch a worker agent to fix the push error, commit, and push.

        Returns True if the worker was launched, False if no worker available.
        The worker runs in the background — the orchestrator does not wait.
        """
        from ..orchestrator.git_remote import run_fix_and_push_worker

        try:
            from ...workers.config import (
                get_workers_runtime_config,
                resolve_worker_for_step,
            )

            cfg = container.config.load()
            runtime = get_workers_runtime_config(
                config=cfg, codex_command_fallback="codex exec",
            )
            spec = resolve_worker_for_step(runtime, "implement")
        except (ValueError, KeyError):
            return False

        def _on_output(line: str) -> None:
            bus.emit(
                channel="system",
                event_type="git.push_output",
                entity_id=pid,
                payload={"line": line},
            )

        def _on_done(success: bool, detail: str) -> None:
            if success:
                bus.emit(
                    channel="system",
                    event_type="git.pushed",
                    entity_id=pid,
                    payload={"auto_fix": True, "detail": detail},
                )
            else:
                bus.emit(
                    channel="system",
                    event_type="git.auto_fix_done",
                    entity_id=pid,
                    payload={"success": False, "detail": detail},
                )

        def _run_safe() -> None:
            """Wrapper ensuring on_done always fires."""
            try:
                run_fix_and_push_worker(
                    container.project_dir,
                    error_output,
                    target,
                    spec,
                    container.state_root,
                    _on_output,
                    _on_done,
                )
            except Exception as exc:
                _on_done(False, str(exc))

        _on_output("Auto-fix enabled — launching worker to fix and push...")

        # Fire and forget — worker handles fix + commit + push
        asyncio.get_event_loop().run_in_executor(None, _run_safe)
        return True

    @router.post("/git/push/cancel")
    async def git_push_cancel(
        project_dir: Optional[str] = Query(None),
    ) -> dict[str, Any]:
        """Cancel a running push operation."""
        container, _, _ = deps.ctx(project_dir)
        event = _push_cancel.pop(container.project_id, None)
        if event is None:
            return {"cancelled": False, "detail": "No push in progress"}
        event.set()
        return {"cancelled": True}

    @router.post("/git/suggest-branch-name")
    async def suggest_branch_name(
        project_dir: Optional[str] = Query(None),
    ) -> dict[str, Any]:
        """Use an LLM worker to suggest a branch name from ahead-of-remote commits."""
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
