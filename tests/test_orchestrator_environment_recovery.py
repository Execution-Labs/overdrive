from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from overdrive.runtime.domain.models import RunRecord, Task, now_iso
from overdrive.runtime.events.bus import EventBus
from overdrive.runtime.orchestrator.service import OrchestratorService
from overdrive.runtime.storage.container import Container


def _make_service(tmp_path: Path) -> tuple[Container, OrchestratorService]:
    container = Container(tmp_path)
    bus = EventBus(container.events, project_id=tmp_path.name)
    service = OrchestratorService(container, bus)
    return container, service


def test_recoverable_environment_failure_requeues_task(tmp_path: Path) -> None:
    container, service = _make_service(tmp_path)
    task = Task(
        title="Recoverable env task",
        status="in_progress",
        metadata={
            "environment_preflight": {
                "issues": [{"code": "node_deps_missing", "summary": "Node dependencies are missing."}]
            }
        },
    )
    run = RunRecord(task_id=task.id, status="in_progress", started_at=now_iso())
    task.run_ids.append(run.id)
    container.tasks.upsert(task)
    container.runs.upsert(run)

    handled = service._handle_recoverable_environment_failure(
        task,
        run,
        step="implement",
        summary="Environment preflight failed: Node dependencies are missing.",
    )

    assert handled is True
    saved = container.tasks.get(task.id)
    assert saved is not None
    assert saved.status == "queued"
    assert saved.pending_gate is None
    assert isinstance(saved.metadata.get("environment_last_auto_recovery"), dict)
    attempts = saved.metadata.get("environment_recovery_attempts_by_step")
    assert isinstance(attempts, dict)
    assert int(attempts.get("implement") or 0) == 1
    assert isinstance(saved.metadata.get("environment_next_retry_at"), str)
    assert int(saved.metadata.get("environment_recovery_backoff_seconds") or 0) > 0

    saved_run = container.runs.get(run.id)
    assert saved_run is not None
    assert saved_run.status == "error"


def test_environment_auto_requeue_honors_not_before_claim_window(tmp_path: Path) -> None:
    container, _service = _make_service(tmp_path)
    future_retry = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    task = Task(
        title="Cooldown task",
        status="queued",
        metadata={"environment_next_retry_at": future_retry},
    )
    container.tasks.upsert(task)

    claimed = container.tasks.claim_next_runnable(max_in_progress=4)
    assert claimed is None


def test_clear_environment_recovery_tracking_resets_step_counter(tmp_path: Path) -> None:
    _container, service = _make_service(tmp_path)
    task = Task(
        title="Counter reset",
        status="in_progress",
        metadata={
            "environment_auto_requeue_pending": True,
            "environment_next_retry_at": now_iso(),
            "environment_recovery_backoff_seconds": 30,
            "environment_recovery_attempts_by_step": {"verify": 2, "implement": 1},
        },
    )

    service._clear_environment_recovery_tracking(task, step="verify")

    assert task.metadata.get("environment_auto_requeue_pending") is None
    assert task.metadata.get("environment_next_retry_at") is None
    assert task.metadata.get("environment_recovery_backoff_seconds") is None
    attempts = task.metadata.get("environment_recovery_attempts_by_step")
    assert isinstance(attempts, dict)
    assert "verify" not in attempts
    assert int(attempts.get("implement") or 0) == 1


def test_recoverable_environment_failure_requeues_review_step(tmp_path: Path) -> None:
    container, service = _make_service(tmp_path)
    task = Task(
        title="Recoverable review env task",
        status="in_progress",
        metadata={
            "environment_preflight": {
                "issues": [{"code": "docker_unavailable", "summary": "Docker unavailable."}]
            }
        },
    )
    run = RunRecord(task_id=task.id, status="in_progress", started_at=now_iso())
    task.run_ids.append(run.id)
    container.tasks.upsert(task)
    container.runs.upsert(run)

    handled = service._handle_recoverable_environment_failure(
        task,
        run,
        step="review",
        summary="Environment preflight failed: Docker daemon/socket is unavailable.",
    )

    assert handled is True
    saved = container.tasks.get(task.id)
    assert saved is not None
    assert saved.status == "queued"
    attempts = saved.metadata.get("environment_recovery_attempts_by_step")
    assert isinstance(attempts, dict)
    assert int(attempts.get("review") or 0) == 1


def test_environment_recovery_limit_escalates_to_human_intervention(tmp_path: Path) -> None:
    container, service = _make_service(tmp_path)
    cfg = container.config.load()
    workers = dict(cfg.get("workers") or {})
    workers["environment"] = {
        "max_auto_retries": 1,
        "auto_prepare": True,
        "capability_fallback": True,
        "required_capabilities_by_step": {},
    }
    cfg["workers"] = workers
    container.config.save(cfg)

    task = Task(
        title="Exhausted env retries",
        status="in_progress",
        metadata={
            "environment_preflight": {
                "issues": [{"code": "docker_unavailable", "summary": "Docker unavailable."}]
            },
            "environment_recovery_attempts_by_step": {"verify": 1},
        },
    )
    run = RunRecord(task_id=task.id, status="in_progress", started_at=now_iso())
    task.run_ids.append(run.id)
    container.tasks.upsert(task)
    container.runs.upsert(run)

    handled = service._handle_recoverable_environment_failure(
        task,
        run,
        step="verify",
        summary="Environment preflight failed: Docker daemon/socket is unavailable.",
    )

    assert handled is True
    saved = container.tasks.get(task.id)
    assert saved is not None
    assert saved.status == "blocked"
    assert saved.pending_gate == service._HUMAN_INTERVENTION_GATE

    saved_run = container.runs.get(run.id)
    assert saved_run is not None
    assert saved_run.status == "blocked"
