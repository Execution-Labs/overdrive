"""Periodic/manual reconciler wrapper around runtime invariants."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .invariants import apply_runtime_invariants

if TYPE_CHECKING:
    from .service import OrchestratorService

logger = logging.getLogger(__name__)

_DEFAULT_STALE_FUTURE_THRESHOLD_SECONDS = 900  # 15 min


def _latest_log_mtime(active_logs: dict[str, Any]) -> float | None:
    """Return the latest mtime epoch of stdout/stderr log files, or None."""
    latest: float | None = None
    for key in ("stdout_path", "stderr_path"):
        path_str = active_logs.get(key)
        if not path_str:
            continue
        try:
            mtime = Path(path_str).stat().st_mtime
        except OSError:
            continue
        if latest is None or mtime > latest:
            latest = mtime
    return latest


class OrchestratorReconciler:
    """Apply runtime invariant checks and deterministic self-heal actions."""

    def __init__(self, service: "OrchestratorService") -> None:
        self._service = service

    def _stale_future_threshold_seconds(self) -> int:
        """Return the log-staleness threshold for detecting hung futures."""
        cfg = self._service.container.config.load()
        orch_cfg = cfg.get("orchestrator") if isinstance(cfg, dict) else {}
        orch_cfg = orch_cfg if isinstance(orch_cfg, dict) else {}
        raw = orch_cfg.get("stale_future_threshold_seconds")
        if raw is not None:
            try:
                val = int(raw)
                if val >= 60:
                    return val
            except (ValueError, TypeError):
                pass
        return _DEFAULT_STALE_FUTURE_THRESHOLD_SECONDS

    def _detect_stale_futures(self, active_ids: set[str]) -> set[str]:
        """Find futures whose worker log output has gone stale.

        A future is considered stale when all of:
        - The task is ``in_progress`` with ``active_logs`` metadata
        - The execution lease has expired (heartbeats stopped)
        - stdout/stderr files haven't been modified for > threshold seconds
        """
        if not active_ids:
            return set()
        threshold = self._stale_future_threshold_seconds()
        now = time.time()
        stale: set[str] = set()
        for task_id in active_ids:
            task = self._service.container.tasks.get(task_id)
            if not task or task.status != "in_progress":
                continue
            active_logs = (task.metadata or {}).get("active_logs")
            if not isinstance(active_logs, dict):
                continue
            if self._service._execution_lease_active(task):
                continue
            last_mtime = _latest_log_mtime(active_logs)
            if last_mtime is None:
                continue
            age = now - last_mtime
            if age > threshold:
                logger.warning(
                    "Stale future detected for task %s: log output idle for %.0fs "
                    "(threshold %ds)",
                    task_id,
                    age,
                    threshold,
                )
                stale.add(task_id)
        return stale

    def _cancel_stale_future(self, task_id: str) -> None:
        """Signal cancellation for a stale worker subprocess."""
        if hasattr(self._service.worker_adapter, "signal_cancel"):
            try:
                self._service.worker_adapter.signal_cancel(task_id)
            except Exception:
                pass

    def run_once(self, *, source: Literal["startup", "automatic", "manual"]) -> dict[str, Any]:
        """Run one reconciliation pass and return summary details."""
        with self._service._futures_lock:
            active_ids = {
                task_id
                for task_id, future in self._service._futures.items()
                if not future.done()
            }

        # Detect futures whose worker output has gone stale.  Remove them
        # from active_ids so invariants will requeue the task, and signal
        # the worker subprocess to terminate.
        stale_ids = self._detect_stale_futures(active_ids)
        active_ids -= stale_ids
        for task_id in stale_ids:
            self._cancel_stale_future(task_id)

        return apply_runtime_invariants(
            self._service,
            active_future_task_ids=active_ids,
            source=source,
        )
