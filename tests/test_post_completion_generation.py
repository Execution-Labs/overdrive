from __future__ import annotations

from pathlib import Path

from agent_orchestrator.runtime.domain.models import Task
from agent_orchestrator.runtime.events.bus import EventBus
from agent_orchestrator.runtime.orchestrator.service import OrchestratorService
from agent_orchestrator.runtime.orchestrator.worker_adapter import DefaultWorkerAdapter
from agent_orchestrator.runtime.storage.container import Container


def _make_service(tmp_path: Path) -> OrchestratorService:
    container = Container(tmp_path)
    bus = EventBus(container.events, container.project_id)
    return OrchestratorService(container, bus, worker_adapter=DefaultWorkerAdapter())


def test_supports_post_completion_generation_done_research(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    task = Task(title="Research task", task_type="research", status="done")
    assert service.supports_post_completion_generation(task) is True


def test_supports_post_completion_generation_done_review(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    task = Task(title="Review task", task_type="review", status="done")
    assert service.supports_post_completion_generation(task) is True


def test_supports_post_completion_generation_done_spike(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    task = Task(title="Spike task", task_type="spike", status="done")
    assert service.supports_post_completion_generation(task) is True


def test_supports_post_completion_generation_done_verify_only(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    task = Task(title="Verify task", task_type="verify_only", status="done")
    assert service.supports_post_completion_generation(task) is True


def test_rejects_non_done_research(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    task = Task(title="Research task", task_type="research", status="in_progress")
    assert service.supports_post_completion_generation(task) is False


def test_rejects_backlog_research(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    task = Task(title="Research task", task_type="research", status="backlog")
    assert service.supports_post_completion_generation(task) is False


def test_rejects_done_standard_task(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    task = Task(title="Standard task", task_type="feature", status="done")
    assert service.supports_post_completion_generation(task) is False


def test_rejects_done_plan_only_task(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    task = Task(title="Plan task", task_type="plan_only", status="done")
    assert service.supports_post_completion_generation(task) is False


def test_rejects_done_research_with_existing_children(tmp_path: Path) -> None:
    service = _make_service(tmp_path)
    task = Task(
        title="Research task",
        task_type="research",
        status="done",
        children_ids=["existing-child"],
    )
    assert service.supports_post_completion_generation(task) is False


def test_generate_tasks_endpoint_rejects_done_task_with_children(tmp_path: Path) -> None:
    """The generate-tasks endpoint should return 400 for a done research task that already has children."""
    from starlette.testclient import TestClient

    from agent_orchestrator.server.api import create_app

    container = Container(tmp_path)
    task = Task(title="Research done", task_type="research", status="done", children_ids=["child-1"])
    container.tasks.upsert(task)

    app = create_app(project_dir=str(tmp_path))
    client = TestClient(app)

    resp = client.post(f"/api/tasks/{task.id}/generate-tasks", json={"source": "latest"})
    assert resp.status_code == 400
    assert "does not support task generation" in resp.json()["detail"]
