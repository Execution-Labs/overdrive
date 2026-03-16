"""Tests for early pipeline completion when a step signals no action needed."""
from __future__ import annotations

import subprocess
from pathlib import Path

from agent_orchestrator.runtime.domain.models import Task
from agent_orchestrator.runtime.events import EventBus
from agent_orchestrator.runtime.orchestrator import OrchestratorService
from agent_orchestrator.runtime.orchestrator.worker_adapter import (
    DefaultWorkerAdapter,
    StepResult,
)
from agent_orchestrator.runtime.storage.container import Container


def _service(
    tmp_path: Path,
    worker_adapter: DefaultWorkerAdapter | None = None,
) -> tuple[Container, OrchestratorService, EventBus]:
    container = Container(tmp_path)
    bus = EventBus(container.events, container.project_id)
    service = OrchestratorService(container, bus, worker_adapter=worker_adapter)
    return container, service, bus


def _step_names(container: Container, task_id: str) -> list[str]:
    runs = container.runs.list()
    for run in runs:
        if run.task_id == task_id:
            return [step["step"] for step in (run.steps or [])]
    return []


def _step_statuses(container: Container, task_id: str) -> list[tuple[str, str]]:
    runs = container.runs.list()
    for run in runs:
        if run.task_id == task_id:
            return [(step["step"], step["status"]) for step in (run.steps or [])]
    return []


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True, text=True)
    (path / "README.md").write_text("# init\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# StepResult field
# ---------------------------------------------------------------------------


def test_step_result_no_action_needed_default() -> None:
    """StepResult defaults to no_action_needed=False."""
    result = StepResult()
    assert result.no_action_needed is False


def test_step_result_no_action_needed_true() -> None:
    """StepResult can be created with no_action_needed=True."""
    result = StepResult(no_action_needed=True)
    assert result.no_action_needed is True


# ---------------------------------------------------------------------------
# _is_no_action_needed helper
# ---------------------------------------------------------------------------


def test_no_action_needed_detection_commit_review_no_issues() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import (
        _is_no_action_needed,
    )

    assert _is_no_action_needed("commit_review", "No issues found — commit looks correct.") is True


def test_no_action_needed_detection_case_insensitive() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import (
        _is_no_action_needed,
    )

    assert _is_no_action_needed("commit_review", "NO ISSUES FOUND in this commit.") is True


def test_no_action_needed_detection_with_issues() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import (
        _is_no_action_needed,
    )

    assert _is_no_action_needed("commit_review", "Found 3 issues: ...") is False


def test_no_action_needed_detection_none_summary() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import (
        _is_no_action_needed,
    )

    assert _is_no_action_needed("commit_review", None) is False


def test_no_action_needed_detection_non_early_complete_step() -> None:
    """Steps not in _EARLY_COMPLETE_STEPS should never trigger no_action_needed."""
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import (
        _is_no_action_needed,
    )

    assert _is_no_action_needed("implement", "No issues found") is False
    assert _is_no_action_needed("review", "No issues found") is False
    assert _is_no_action_needed("scan_deps", "No issues found") is False


# ---------------------------------------------------------------------------
# Autopilot: commit_review with no issues → task done
# ---------------------------------------------------------------------------


def test_commit_review_no_issues_autopilot(tmp_path: Path) -> None:
    """When commit_review finds no issues in autopilot mode, the task completes
    without running implement/verify/review/commit steps."""
    _git_init(tmp_path)
    container, service, _ = _service(tmp_path)
    task = Task(
        title="Review commit",
        task_type="commit_review",
        status="queued",
        hitl_mode="autopilot",
        metadata={
            "scripted_steps": {
                "commit_review": {
                    "status": "ok",
                    "summary": "No issues found — commit looks correct.",
                    "no_action_needed": True,
                },
            },
        },
    )
    container.tasks.upsert(task)

    result = service.run_task(task.id)

    assert result.status == "done"
    step_statuses = _step_statuses(container, task.id)
    # commit_review ran, remaining steps were skipped
    assert step_statuses[0] == ("commit_review", "ok")
    skipped = [s for s, st in step_statuses if st == "skipped"]
    assert "implement" in skipped
    assert "verify" in skipped
    assert "review" in skipped
    assert "commit" in skipped

    # Run summary should indicate early completion
    runs = container.runs.list()
    run = [r for r in runs if r.task_id == task.id][0]
    assert run.status == "done"
    assert "no action needed" in (run.summary or "").lower()


# ---------------------------------------------------------------------------
# Supervised: commit_review with no issues → pauses at before_done gate
# ---------------------------------------------------------------------------


def test_commit_review_no_issues_supervised(tmp_path: Path) -> None:
    """In supervised mode with no issues, the task should pause at the
    before_done gate rather than completing immediately."""
    _git_init(tmp_path)
    container, service, _ = _service(tmp_path)
    task = Task(
        title="Review commit supervised",
        task_type="commit_review",
        status="queued",
        hitl_mode="supervised",
        metadata={
            "scripted_steps": {
                "commit_review": {
                    "status": "ok",
                    "summary": "No issues found — commit looks correct.",
                    "no_action_needed": True,
                },
            },
        },
    )
    container.tasks.upsert(task)

    result = service.run_task(task.id)

    # Task should be paused at before_done gate (status in_review or queued
    # depending on gate implementation), not done
    assert result.status != "done"
    assert result.metadata.get("early_complete") is True


# ---------------------------------------------------------------------------
# commit_review with issues found → normal pipeline flow (unchanged)
# ---------------------------------------------------------------------------


def test_commit_review_with_issues_unchanged(tmp_path: Path) -> None:
    """When commit_review finds issues, the pipeline proceeds normally
    with implement/verify/review/commit steps."""
    _git_init(tmp_path)
    container, service, _ = _service(tmp_path)
    task = Task(
        title="Review commit with issues",
        task_type="commit_review",
        status="queued",
        hitl_mode="autopilot",
        metadata={
            "scripted_steps": {
                "commit_review": {
                    "status": "ok",
                    "summary": "Found 2 issues:\n1. Missing error handling\n2. Unused import",
                },
            },
            "scripted_files": {
                "fix.txt": "fixed\n",
            },
        },
    )
    container.tasks.upsert(task)

    result = service.run_task(task.id)

    assert result.status == "done"
    step_names = _step_names(container, task.id)
    # All steps should have run
    assert "commit_review" in step_names
    assert "implement" in step_names
    assert "verify" in step_names
    assert "review" in step_names
    assert "commit" in step_names
    # No steps should be skipped
    step_statuses = _step_statuses(container, task.id)
    assert not any(st == "skipped" for _, st in step_statuses)


# ---------------------------------------------------------------------------
# Run log: early completion shows skipped steps
# ---------------------------------------------------------------------------


def test_early_complete_run_log(tmp_path: Path) -> None:
    """Run log should show the completed step and all subsequent steps as skipped."""
    _git_init(tmp_path)
    container, service, _ = _service(tmp_path)
    task = Task(
        title="Review commit log check",
        task_type="commit_review",
        status="queued",
        hitl_mode="autopilot",
        metadata={
            "scripted_steps": {
                "commit_review": {
                    "status": "ok",
                    "summary": "No issues found — commit looks correct.",
                    "no_action_needed": True,
                },
            },
        },
    )
    container.tasks.upsert(task)

    service.run_task(task.id)

    step_statuses = _step_statuses(container, task.id)
    # First step ran successfully
    assert step_statuses[0] == ("commit_review", "ok")
    # All subsequent steps are skipped
    for step, status in step_statuses[1:]:
        assert status == "skipped", f"Expected {step} to be skipped, got {status}"


# ---------------------------------------------------------------------------
# Expanded _is_no_action_needed: diagnose, scan_code, profile
# ---------------------------------------------------------------------------


def test_no_action_needed_diagnose_no_issues() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import _is_no_action_needed
    assert _is_no_action_needed("diagnose", "No issues found — behavior is expected.") is True


def test_no_action_needed_diagnose_no_bug_found() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import _is_no_action_needed
    assert _is_no_action_needed("diagnose", "No bug found after investigation.") is True


def test_no_action_needed_diagnose_no_bug_identified() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import _is_no_action_needed
    assert _is_no_action_needed("diagnose", "No bug identified in the codebase.") is True


def test_no_action_needed_diagnose_with_root_cause() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import _is_no_action_needed
    assert _is_no_action_needed("diagnose", "Root cause: null pointer in parser.py") is False


def test_no_action_needed_diagnose_none_summary() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import _is_no_action_needed
    assert _is_no_action_needed("diagnose", None) is False


def test_no_action_needed_diagnose_empty_summary() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import _is_no_action_needed
    assert _is_no_action_needed("diagnose", "") is False


def test_no_action_needed_scan_code_no_issues() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import _is_no_action_needed
    assert _is_no_action_needed("scan_code", "No issues found — codebase is clean.") is True


def test_no_action_needed_scan_code_with_issues() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import _is_no_action_needed
    assert _is_no_action_needed("scan_code", "Found 3 vulnerabilities") is False


def test_no_action_needed_scan_deps_excluded() -> None:
    """scan_deps is intentionally NOT in _EARLY_COMPLETE_STEPS."""
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import _is_no_action_needed
    assert _is_no_action_needed("scan_deps", "No issues found") is False


def test_no_action_needed_profile_no_issues() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import _is_no_action_needed
    assert _is_no_action_needed("profile", "No performance issues detected.") is True


def test_no_action_needed_profile_generic_no_issues() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import _is_no_action_needed
    assert _is_no_action_needed("profile", "No issues found — within acceptable thresholds.") is True


def test_no_action_needed_profile_with_bottleneck() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import _is_no_action_needed
    assert _is_no_action_needed("profile", "Bottleneck: database query in get_users") is False


def test_early_complete_steps_membership() -> None:
    from agent_orchestrator.runtime.orchestrator.live_worker_adapter import _EARLY_COMPLETE_STEPS
    assert _EARLY_COMPLETE_STEPS == {
        "commit_review", "pr_review", "mr_review",
        "diagnose", "scan_code", "profile",
    }


# ---------------------------------------------------------------------------
# Integration: bug_fix pipeline early completion
# ---------------------------------------------------------------------------


def test_bug_fix_no_issues_autopilot(tmp_path: Path) -> None:
    """Bug_fix pipeline: diagnose finds no bug → task done, remaining steps skipped."""
    _git_init(tmp_path)
    container, service, _ = _service(tmp_path)
    task = Task(
        title="Fix reported bug",
        task_type="bug",
        status="queued",
        hitl_mode="autopilot",
        metadata={
            "scripted_steps": {
                "diagnose": {
                    "status": "ok",
                    "summary": "No issues found — the reported behavior is expected.",
                    "no_action_needed": True,
                },
            },
        },
    )
    container.tasks.upsert(task)
    result = service.run_task(task.id)
    assert result.status == "done"
    step_statuses = _step_statuses(container, task.id)
    assert step_statuses[0] == ("diagnose", "ok")
    skipped = [s for s, st in step_statuses if st == "skipped"]
    assert "implement" in skipped
    assert "verify" in skipped
    assert "review" in skipped
    assert "commit" in skipped


def test_bug_fix_no_issues_supervised(tmp_path: Path) -> None:
    """Bug_fix pipeline in supervised mode: diagnose finds no bug → pauses at before_done gate."""
    _git_init(tmp_path)
    container, service, _ = _service(tmp_path)
    task = Task(
        title="Fix reported bug supervised",
        task_type="bug",
        status="queued",
        hitl_mode="supervised",
        metadata={
            "scripted_steps": {
                "diagnose": {
                    "status": "ok",
                    "summary": "No issues found — behavior is expected.",
                    "no_action_needed": True,
                },
            },
        },
    )
    container.tasks.upsert(task)
    result = service.run_task(task.id)
    assert result.status != "done"
    assert result.metadata.get("early_complete") is True


# ---------------------------------------------------------------------------
# _request_changes_step_for_gate fix for early-completed tasks
# ---------------------------------------------------------------------------


def test_request_changes_before_done_early_complete() -> None:
    """When requesting changes at before_done for an early-completed task,
    retry from the triggering step (current_step), not steps[-1]."""
    from agent_orchestrator.runtime.api.routes_tasks import _request_changes_step_for_gate
    task = Task(
        title="test",
        task_type="bug",
        status="in_progress",
        metadata={"early_complete": True},
        pipeline_template=["diagnose", "implement", "verify", "review", "commit"],
    )
    task.current_step = "diagnose"
    result = _request_changes_step_for_gate(task, "before_done")
    assert result == "diagnose"


def test_request_changes_before_done_non_early_complete() -> None:
    """When requesting changes at before_done for a non-early-completed task,
    behavior unchanged: returns steps[-1]."""
    from agent_orchestrator.runtime.api.routes_tasks import _request_changes_step_for_gate
    task = Task(
        title="test",
        task_type="review",
        status="in_progress",
        metadata={},
        pipeline_template=["analyze", "review"],
    )
    result = _request_changes_step_for_gate(task, "before_done")
    assert result == "review"


def test_request_changes_before_done_existing_commit_review_early_complete() -> None:
    """Regression: commit_review early-complete + request changes → retry from commit_review."""
    from agent_orchestrator.runtime.api.routes_tasks import _request_changes_step_for_gate
    task = Task(
        title="test",
        task_type="commit_review",
        status="in_progress",
        metadata={"early_complete": True},
        pipeline_template=["commit_review", "implement", "verify", "review", "commit"],
    )
    task.current_step = "commit_review"
    result = _request_changes_step_for_gate(task, "before_done")
    assert result == "commit_review"
