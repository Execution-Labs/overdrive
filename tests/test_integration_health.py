"""Unit tests for the post-merge integration health checker."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_orchestrator.runtime.domain.models import Task, now_iso
from agent_orchestrator.runtime.events.bus import EventBus
from agent_orchestrator.runtime.orchestrator.integration_health import (
    HealthCheckResult,
    IntegrationHealthChecker,
    _truncate,
)
from agent_orchestrator.runtime.orchestrator.invariants import apply_runtime_invariants
from agent_orchestrator.runtime.orchestrator.service import OrchestratorService
from agent_orchestrator.runtime.orchestrator.worker_adapter import DefaultWorkerAdapter
from agent_orchestrator.runtime.storage.container import Container


def _make_service(tmp_path: Path) -> OrchestratorService:
    container = Container(tmp_path)
    bus = EventBus(container.events, container.project_id)
    return OrchestratorService(container, bus, worker_adapter=DefaultWorkerAdapter())


# ------------------------------------------------------------------
# Truncation helper
# ------------------------------------------------------------------


def test_truncate_short_string() -> None:
    assert _truncate("hello", 100) == "hello"


def test_truncate_long_string() -> None:
    text = "x" * 200
    result = _truncate(text, 50)
    assert len(result) < 200
    assert result.startswith("x" * 50)
    assert "truncated" in result


# ------------------------------------------------------------------
# State persistence round-trip
# ------------------------------------------------------------------


def test_state_round_trip(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    health = svc._integration_health

    health._status = "degraded"
    health._last_check_at = "2025-01-01T00:00:00Z"
    health._last_check_task_id = "task-abc"
    health._merge_count_since_check = 3
    health._failure_summary = "FAIL"
    health._fix_task_id = "task-fix"

    state = health.persist_state()
    assert state["status"] == "degraded"
    assert state["merge_count_since_check"] == 3

    health2 = IntegrationHealthChecker(svc)
    health2.load_state({"integration_health": state})
    assert health2._status == "degraded"
    assert health2._last_check_task_id == "task-abc"
    assert health2._merge_count_since_check == 3
    assert health2._fix_task_id == "task-fix"


def test_load_state_handles_empty(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    health = IntegrationHealthChecker(svc)
    health.load_state({})
    assert health._status == "healthy"
    assert health._merge_count_since_check == 0


# ------------------------------------------------------------------
# should_run logic
# ------------------------------------------------------------------


def test_should_run_mode_off(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    health = svc._integration_health
    # Default config has mode=off
    assert health.should_run() is False


def test_should_run_mode_always(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    cfg = svc.container.config.load()
    orch = dict(cfg.get("orchestrator") or {})
    orch["integration_health"] = {"mode": "always"}
    cfg["orchestrator"] = orch
    svc.container.config.save(cfg)

    assert svc._integration_health.should_run() is True


def test_should_run_mode_periodic(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    cfg = svc.container.config.load()
    orch = dict(cfg.get("orchestrator") or {})
    orch["integration_health"] = {"mode": "periodic", "periodic_interval": 3}
    cfg["orchestrator"] = orch
    svc.container.config.save(cfg)

    health = svc._integration_health
    health._merge_count_since_check = 2
    assert health.should_run() is False

    health._merge_count_since_check = 3
    assert health.should_run() is True


# ------------------------------------------------------------------
# record_merge
# ------------------------------------------------------------------


def test_record_merge_increments(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    health = svc._integration_health
    assert health._merge_count_since_check == 0
    health.record_merge()
    health.record_merge()
    assert health._merge_count_since_check == 2


# ------------------------------------------------------------------
# run_check — pass path
# ------------------------------------------------------------------


def test_run_check_pass(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    cfg = svc.container.config.load()
    orch = dict(cfg.get("orchestrator") or {})
    orch["integration_health"] = {"mode": "always"}
    cfg["orchestrator"] = orch
    svc.container.config.save(cfg)

    _mod = "agent_orchestrator.runtime.orchestrator.integration_health.subprocess.run"
    with patch(_mod) as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
        with patch.object(svc._integration_health, "_resolve_test_command", return_value="pytest"):
            result = svc._integration_health.run_check("task-1")

    assert result is not None
    assert result.passed is True
    assert result.exit_code == 0
    assert svc._integration_health._status == "healthy"
    assert svc._integration_health._merge_count_since_check == 0


# ------------------------------------------------------------------
# run_check — fail path
# ------------------------------------------------------------------


def test_run_check_fail_creates_fix_task(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    cfg = svc.container.config.load()
    orch = dict(cfg.get("orchestrator") or {})
    orch["integration_health"] = {"mode": "always", "auto_fix_task": True}
    cfg["orchestrator"] = orch
    svc.container.config.save(cfg)

    _mod = "agent_orchestrator.runtime.orchestrator.integration_health.subprocess.run"
    with patch(_mod) as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="FAILED test_foo", stderr="")
        with patch.object(svc._integration_health, "_resolve_test_command", return_value="pytest"):
            result = svc._integration_health.run_check("task-trigger")

    assert result is not None
    assert result.passed is False
    assert svc._integration_health._status == "degraded"
    assert svc._integration_health.is_degraded() is True

    # Fix task should have been created
    tasks = svc.container.tasks.list()
    fix_tasks = [
        t for t in tasks
        if isinstance(t.metadata, dict) and t.metadata.get("generated_from") == "integration_health_check"
    ]
    assert len(fix_tasks) == 1
    fix = fix_tasks[0]
    assert fix.status == "queued"
    assert fix.priority == "P0"
    assert fix.source == "generated"
    assert "task-trigger" in fix.description


# ------------------------------------------------------------------
# Deduplication — no double fix task
# ------------------------------------------------------------------


def test_no_duplicate_fix_task(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    cfg = svc.container.config.load()
    orch = dict(cfg.get("orchestrator") or {})
    orch["integration_health"] = {"mode": "always", "auto_fix_task": True}
    cfg["orchestrator"] = orch
    svc.container.config.save(cfg)

    # Pre-create an open fix task
    existing_fix = Task(
        title="Fix integration regressions on run branch",
        task_type="chore",
        status="queued",
        source="generated",
        metadata={"generated_from": "integration_health_check"},
    )
    svc.container.tasks.upsert(existing_fix)

    _mod = "agent_orchestrator.runtime.orchestrator.integration_health.subprocess.run"
    with patch(_mod) as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="FAILED", stderr="")
        with patch.object(svc._integration_health, "_resolve_test_command", return_value="pytest"):
            svc._integration_health.run_check("task-2")

    tasks = svc.container.tasks.list()
    fix_tasks = [
        t for t in tasks
        if isinstance(t.metadata, dict) and t.metadata.get("generated_from") == "integration_health_check"
    ]
    assert len(fix_tasks) == 1  # still only the original one


# ------------------------------------------------------------------
# run_check skipped when mode=off
# ------------------------------------------------------------------


def test_run_check_skipped_when_off(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    # Default mode is off
    result = svc._integration_health.run_check("task-x")
    assert result is None


# ------------------------------------------------------------------
# run_check skipped when no test command resolved
# ------------------------------------------------------------------


def test_run_check_skipped_when_no_command(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    cfg = svc.container.config.load()
    orch = dict(cfg.get("orchestrator") or {})
    orch["integration_health"] = {"mode": "always"}
    cfg["orchestrator"] = orch
    svc.container.config.save(cfg)

    # No language markers -> no test command
    result = svc._integration_health.run_check("task-y")
    assert result is None


# ------------------------------------------------------------------
# Dispatch gating
# ------------------------------------------------------------------


def test_dispatch_blocked_when_degraded_and_blocking(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    cfg = svc.container.config.load()
    orch = dict(cfg.get("orchestrator") or {})
    orch["integration_health"] = {"mode": "always", "blocking": True}
    cfg["orchestrator"] = orch
    svc.container.config.save(cfg)

    svc._integration_health._status = "degraded"
    # No fix task exists — dispatch should be blocked
    task = Task(title="test", status="queued")
    svc.container.tasks.upsert(task)

    dispatched = svc.tick_once()
    assert dispatched is False
    assert svc._dispatch_blocked_reason == "integration_degraded"


def test_dispatch_allows_fix_task_when_blocking(tmp_path: Path) -> None:
    """Fix task must be dispatchable even when blocking=true, otherwise deadlock."""
    svc = _make_service(tmp_path)
    cfg = svc.container.config.load()
    orch = dict(cfg.get("orchestrator") or {})
    orch["integration_health"] = {"mode": "always", "blocking": True}
    cfg["orchestrator"] = orch
    svc.container.config.save(cfg)

    svc._integration_health._status = "degraded"

    # Create the fix task (queued) and point the health checker to it
    fix_task = Task(
        title="Fix integration regressions",
        status="queued",
        source="generated",
        metadata={"generated_from": "integration_health_check"},
    )
    svc.container.tasks.upsert(fix_task)
    svc._integration_health._fix_task_id = fix_task.id

    # tick_once should NOT be blocked — the fix task needs to be dispatched
    dispatched = svc.tick_once()
    assert dispatched is True

    # Verify the fix task was claimed
    refreshed = svc.container.tasks.get(fix_task.id)
    assert refreshed is not None
    assert refreshed.status == "in_progress"


def test_dispatch_blocks_again_after_fix_task_claimed(tmp_path: Path) -> None:
    """Once the fix task is in_progress, dispatch should block other tasks."""
    svc = _make_service(tmp_path)
    cfg = svc.container.config.load()
    orch = dict(cfg.get("orchestrator") or {})
    orch["integration_health"] = {"mode": "always", "blocking": True}
    cfg["orchestrator"] = orch
    svc.container.config.save(cfg)

    svc._integration_health._status = "degraded"

    # Fix task already running
    fix_task = Task(
        title="Fix integration regressions",
        status="in_progress",
        source="generated",
        metadata={"generated_from": "integration_health_check"},
    )
    svc.container.tasks.upsert(fix_task)
    svc._integration_health._fix_task_id = fix_task.id

    # Other queued task should be blocked
    other = Task(title="other work", status="queued")
    svc.container.tasks.upsert(other)

    dispatched = svc.tick_once()
    assert dispatched is False
    assert svc._dispatch_blocked_reason == "integration_degraded"


def test_dispatch_not_blocked_when_degraded_but_not_blocking(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    cfg = svc.container.config.load()
    orch = dict(cfg.get("orchestrator") or {})
    orch["integration_health"] = {"mode": "always", "blocking": False}
    cfg["orchestrator"] = orch
    svc.container.config.save(cfg)

    svc._integration_health._status = "degraded"
    # tick_once should NOT be blocked by integration health
    # (it may return False for other reasons like no queued tasks)
    svc.tick_once()
    assert svc._dispatch_blocked_reason != "integration_degraded"


# ------------------------------------------------------------------
# clear_degraded
# ------------------------------------------------------------------


def test_clear_degraded(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    health = svc._integration_health
    health._status = "degraded"
    health._failure_summary = "error"
    health._fix_task_id = "task-fix"

    health.clear_degraded()
    assert health._status == "healthy"
    assert health._failure_summary is None
    assert health._fix_task_id is None


# ------------------------------------------------------------------
# Reconciler invariant: clear degraded when fix task is done
# ------------------------------------------------------------------


def test_reconciler_clears_degraded_when_fix_done(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    health = svc._integration_health
    health._status = "degraded"
    health._failure_summary = "test failures"

    fix_task = Task(title="Fix integration", status="done", source="generated")
    svc.container.tasks.upsert(fix_task)
    health._fix_task_id = fix_task.id

    result = apply_runtime_invariants(
        svc,
        active_future_task_ids=set(),
        source="test",
    )
    assert health._status == "healthy"
    cleared = [r for r in result["items"] if r.get("code") == "integration_health_cleared"]
    assert len(cleared) == 1


def test_reconciler_clears_degraded_when_fix_cancelled(tmp_path: Path) -> None:
    """Cancelled fix task should also clear degraded to avoid deadlock."""
    svc = _make_service(tmp_path)
    health = svc._integration_health
    health._status = "degraded"
    health._failure_summary = "test failures"

    fix_task = Task(title="Fix integration", status="cancelled", source="generated")
    svc.container.tasks.upsert(fix_task)
    health._fix_task_id = fix_task.id

    result = apply_runtime_invariants(
        svc,
        active_future_task_ids=set(),
        source="test",
    )
    assert health._status == "healthy"
    cleared = [r for r in result["items"] if r.get("code") == "integration_health_cleared"]
    assert len(cleared) == 1


def test_reconciler_keeps_degraded_when_fix_not_done(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    health = svc._integration_health
    health._status = "degraded"

    fix_task = Task(title="Fix integration", status="in_progress", source="generated")
    svc.container.tasks.upsert(fix_task)
    health._fix_task_id = fix_task.id

    apply_runtime_invariants(
        svc,
        active_future_task_ids=set(),
        source="test",
    )
    assert health._status == "degraded"


# ------------------------------------------------------------------
# Status exposure
# ------------------------------------------------------------------


def test_status_includes_integration_health(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    status = svc.status()
    assert "integration_health" in status
    assert status["integration_health"]["status"] == "healthy"


# ------------------------------------------------------------------
# Persist state round-trip via service
# ------------------------------------------------------------------


def test_service_persists_health_state(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    svc._integration_health._status = "degraded"
    svc._integration_health._merge_count_since_check = 7
    svc._persist_runtime_state(force=True)

    # Create new service and verify state is rehydrated
    svc2 = _make_service(tmp_path)
    assert svc2._integration_health._status == "degraded"
    assert svc2._integration_health._merge_count_since_check == 7


# ------------------------------------------------------------------
# Timeout handling
# ------------------------------------------------------------------


def test_run_check_timeout(tmp_path: Path) -> None:
    import subprocess as _subprocess

    svc = _make_service(tmp_path)
    cfg = svc.container.config.load()
    orch = dict(cfg.get("orchestrator") or {})
    orch["integration_health"] = {"mode": "always", "timeout_seconds": 30}
    cfg["orchestrator"] = orch
    svc.container.config.save(cfg)

    _mod = "agent_orchestrator.runtime.orchestrator.integration_health.subprocess.run"
    with patch(_mod) as mock_run:
        mock_run.side_effect = _subprocess.TimeoutExpired(cmd="pytest", timeout=30)
        with patch.object(svc._integration_health, "_resolve_test_command", return_value="pytest"):
            result = svc._integration_health.run_check("task-timeout")

    assert result is not None
    assert result.passed is False
    assert result.exit_code == -1
    assert "timed out" in result.stderr
    assert svc._integration_health._status == "degraded"


# ------------------------------------------------------------------
# auto_fix_task disabled
# ------------------------------------------------------------------


def test_no_fix_task_when_disabled(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    cfg = svc.container.config.load()
    orch = dict(cfg.get("orchestrator") or {})
    orch["integration_health"] = {"mode": "always", "auto_fix_task": False}
    cfg["orchestrator"] = orch
    svc.container.config.save(cfg)

    _mod = "agent_orchestrator.runtime.orchestrator.integration_health.subprocess.run"
    with patch(_mod) as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="FAILED", stderr="")
        with patch.object(svc._integration_health, "_resolve_test_command", return_value="pytest"):
            result = svc._integration_health.run_check("task-no-fix")

    assert result is not None
    assert result.passed is False
    assert svc._integration_health._status == "degraded"

    # No fix task should be created
    tasks = svc.container.tasks.list()
    fix_tasks = [
        t for t in tasks
        if isinstance(t.metadata, dict) and t.metadata.get("generated_from") == "integration_health_check"
    ]
    assert len(fix_tasks) == 0


# ------------------------------------------------------------------
# Dedup updates _fix_task_id to existing task
# ------------------------------------------------------------------


def test_dedup_updates_fix_task_id(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    cfg = svc.container.config.load()
    orch = dict(cfg.get("orchestrator") or {})
    orch["integration_health"] = {"mode": "always", "auto_fix_task": True}
    cfg["orchestrator"] = orch
    svc.container.config.save(cfg)

    # Pre-create an open fix task
    existing_fix = Task(
        title="Fix integration regressions on run branch",
        task_type="chore",
        status="in_progress",
        source="generated",
        metadata={"generated_from": "integration_health_check"},
    )
    svc.container.tasks.upsert(existing_fix)

    # Clear any prior fix_task_id reference
    svc._integration_health._fix_task_id = None

    _mod = "agent_orchestrator.runtime.orchestrator.integration_health.subprocess.run"
    with patch(_mod) as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="FAILED", stderr="")
        with patch.object(svc._integration_health, "_resolve_test_command", return_value="pytest"):
            svc._integration_health.run_check("task-dedup")

    # _fix_task_id should now point to the existing task
    assert svc._integration_health._fix_task_id == existing_fix.id
