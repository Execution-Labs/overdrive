"""Shared helpers for task repository scheduling logic.

Functions here are used by both file-backed and SQLite-backed task
repositories to avoid duplicating scheduling predicates.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..domain.models import Task


def priority_rank(priority: str) -> int:
    """Map a priority label to a numeric rank for sorting.

    Args:
        priority: Priority label such as ``"P0"`` through ``"P3"``.

    Returns:
        Integer rank where lower values mean higher priority.
    """
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(priority, 99)


def is_retry_backoff_elapsed(task: Task) -> bool:
    """Return whether a task's retry backoff window has elapsed.

    Args:
        task: The task to check.

    Returns:
        ``True`` when the task is safe to dispatch (no active backoff).
    """
    if task.status != "queued" or not isinstance(task.metadata, dict):
        return True
    raw_not_before = str(task.metadata.get("environment_next_retry_at") or "").strip()
    if not raw_not_before:
        return True
    try:
        not_before = datetime.fromisoformat(raw_not_before.replace("Z", "+00:00"))
    except Exception:
        return True
    return datetime.now(timezone.utc) >= not_before


def is_resume_requested(task: Task) -> bool:
    """Return whether an in-progress task has a pending resume request.

    Args:
        task: The task to check.

    Returns:
        ``True`` when the task carries a non-empty ``resume_requested_at``
        checkpoint value.
    """
    if task.status != "in_progress" or task.pending_gate:
        return False
    if not isinstance(task.metadata, dict):
        return False
    checkpoint = task.metadata.get("execution_checkpoint")
    if not isinstance(checkpoint, dict):
        return False
    return bool(str(checkpoint.get("resume_requested_at") or "").strip())
