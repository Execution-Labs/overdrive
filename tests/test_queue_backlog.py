"""Tests for POST /api/tasks/queue-backlog endpoint."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from overdrive.runtime.domain.models import Task
from overdrive.runtime.storage.container import Container
from overdrive.server.api import create_app


def _git_init(path: Path) -> None:
    """Initialize a minimal git repo for testing."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True, text=True)
    (path / "README.md").write_text("# init\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True, text=True)


def _client_and_container(tmp_path: Path) -> tuple[TestClient, Container]:
    _git_init(tmp_path)
    app = create_app(project_dir=str(tmp_path))
    client = TestClient(app)
    container = Container(tmp_path)
    return client, container


def test_queue_backlog_queues_all_backlog_tasks(tmp_path: Path) -> None:
    """All backlog tasks transition to queued and response has correct shape."""
    client, container = _client_and_container(tmp_path)

    t1 = Task(title="Task A", task_type="feature", status="backlog")
    t2 = Task(title="Task B", task_type="bug", status="backlog")
    t3 = Task(title="Task C", task_type="feature", status="done")
    container.tasks.upsert(t1)
    container.tasks.upsert(t2)
    container.tasks.upsert(t3)

    resp = client.post("/api/tasks/queue-backlog")
    assert resp.status_code == 200

    data = resp.json()
    assert data["queued_count"] == 2
    assert set(data["task_ids"]) == {t1.id, t2.id}
    assert "message" in data

    # Verify persisted state. Under the live scheduler a freshly queued task may
    # be claimed immediately, so accept either queued or in_progress here.
    stored1 = container.tasks.get(t1.id)
    stored2 = container.tasks.get(t2.id)
    stored3 = container.tasks.get(t3.id)
    assert stored1 is not None and stored1.status in {"queued", "in_progress"}
    assert stored2 is not None and stored2.status in {"queued", "in_progress"}
    assert stored3 is not None and stored3.status == "done"


def test_queue_backlog_emits_events(tmp_path: Path) -> None:
    """Emits task.updated for each task and queue.changed once."""
    client, container = _client_and_container(tmp_path)

    t1 = Task(title="Task A", task_type="feature", status="backlog")
    t2 = Task(title="Task B", task_type="bug", status="backlog")
    container.tasks.upsert(t1)
    container.tasks.upsert(t2)

    emitted: list[dict] = []

    with patch(
        "overdrive.runtime.events.bus.EventBus.emit",
        side_effect=lambda **kw: emitted.append(kw),
    ):
        resp = client.post("/api/tasks/queue-backlog")

    assert resp.status_code == 200

    task_updated_events = [e for e in emitted if e.get("event_type") == "task.updated"]
    queue_changed_events = [e for e in emitted if e.get("event_type") == "queue.changed"]

    assert len(task_updated_events) == 2
    assert {e["entity_id"] for e in task_updated_events} == {t1.id, t2.id}
    assert len(queue_changed_events) == 1
    assert queue_changed_events[0]["payload"]["queued_count"] == 2


def test_queue_backlog_empty_is_noop(tmp_path: Path) -> None:
    """No backlog tasks returns zero count and no queue.changed event."""
    client, container = _client_and_container(tmp_path)

    # Only non-backlog tasks exist
    t1 = Task(title="Task A", task_type="feature", status="queued")
    container.tasks.upsert(t1)

    emitted: list[dict] = []

    with patch(
        "overdrive.runtime.events.bus.EventBus.emit",
        side_effect=lambda **kw: emitted.append(kw),
    ):
        resp = client.post("/api/tasks/queue-backlog")

    assert resp.status_code == 200

    data = resp.json()
    assert data["queued_count"] == 0
    assert data["task_ids"] == []

    queue_changed_events = [e for e in emitted if e.get("event_type") == "queue.changed"]
    assert len(queue_changed_events) == 0
