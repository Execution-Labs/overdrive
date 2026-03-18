"""Pipeline execution tests for all 4 review modes and regression tests for legacy behavior.

Tests verify:
1. Correct step sequences execute for each review mode pipeline.
2. Orchestrator-side comment steps are dispatched (not sent to worker adapter).
3. Worker adapter receives only the worker-dispatched steps.
4. Legacy pr_review/mr_review tasks (no review_mode) produce identical behavior.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from overdrive.runtime.domain.models import RunRecord, Task, now_iso
from overdrive.runtime.events import EventBus
from overdrive.runtime.orchestrator import OrchestratorService
from overdrive.runtime.orchestrator.worker_adapter import StepResult
from overdrive.runtime.storage.container import Container


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service(
    tmp_path: Path,
    adapter: Any | None = None,
) -> tuple[Container, OrchestratorService, EventBus]:
    container = Container(tmp_path)
    bus = EventBus(container.events, container.project_id)
    service = OrchestratorService(container, bus, worker_adapter=adapter)
    return container, service, bus


def _step_names(container: Container, task_id: str) -> list[str]:
    """Collect ordered step names from the run associated with a task."""
    runs = container.runs.list()
    for run in runs:
        if run.task_id == task_id:
            return [step["step"] for step in (run.steps or [])]
    return []


def _step_statuses(container: Container, task_id: str) -> list[dict[str, str]]:
    """Collect step name→status pairs from the run."""
    runs = container.runs.list()
    for run in runs:
        if run.task_id == task_id:
            return [{"step": s["step"], "status": s["status"]} for s in (run.steps or [])]
    return []


def _make_review_task(
    task_type: str,
    pipeline_template: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Task:
    """Create a review task with sensible defaults."""
    meta = metadata or {}
    meta.setdefault("source_url", "https://github.com/org/repo/pull/42")
    meta.setdefault("source_description", "Test PR body")
    meta.setdefault("source_diff", "diff --git a/f.py b/f.py\n+hello\n")
    meta.setdefault("source_stat", " f.py | 1 +\n 1 file changed\n")
    meta.setdefault("source_pr_number", 42)
    meta.setdefault("comment_dry_run", True)
    return Task(
        title="Test review task",
        task_type=task_type,
        status="queued",
        hitl_mode="autopilot",
        pipeline_template=pipeline_template,
        metadata=meta,
    )


def _sample_comments_dicts(count: int = 3) -> list[dict[str, Any]]:
    return [
        {
            "id": f"comment-{i}",
            "author": f"user-{i}",
            "body": f"Comment body {i}",
            "path": "src/main.py" if i % 2 == 0 else None,
            "line": (10 + i) if i % 2 == 0 else None,
            "platform_id": str(100 + i),
        }
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# 1. Pipeline execution tests — pr_review_fix_only
# ---------------------------------------------------------------------------


class TestPrReviewFixOnlyPipeline:
    """pr_review_fix_only: pr_review → implement → verify → review → commit."""

    def test_step_sequence(self, tmp_path: Path) -> None:
        adapter = MagicMock()
        adapter.run_step = lambda *, task, step, attempt: StepResult(status="ok")
        container, service, _ = _service(tmp_path, adapter=adapter)

        task = _make_review_task("pr_review_fix_only")
        container.tasks.upsert(task)
        result = service.run_task(task.id)

        assert result.status == "done"
        steps = _step_names(container, task.id)
        assert steps == ["pr_review", "implement", "verify", "review", "commit"]

    def test_worker_adapter_receives_all_steps(self, tmp_path: Path) -> None:
        """No orchestrator comment steps — all steps go to worker adapter."""
        calls: list[str] = []

        def mock_run_step(*, task: Any, step: str, attempt: int) -> StepResult:
            calls.append(step)
            return StepResult(status="ok")

        adapter = MagicMock()
        adapter.run_step = mock_run_step
        container, service, _ = _service(tmp_path, adapter=adapter)

        task = _make_review_task("pr_review_fix_only")
        container.tasks.upsert(task)
        service.run_task(task.id)

        # pr_review, implement, verify go through worker; review and commit are special.
        assert "pr_review" in calls
        assert "implement" in calls
        assert "verify" in calls


# ---------------------------------------------------------------------------
# 2. Pipeline execution tests — pr_review_comment
# ---------------------------------------------------------------------------


class TestPrReviewCommentPipeline:
    """pr_review_comment: fetch_comments → pr_review_comment → post_comments."""

    @patch("overdrive.runtime.orchestrator.task_executor.fetch_pr_comments")
    @patch("overdrive.runtime.orchestrator.task_executor.post_comments_batch")
    def test_step_sequence(
        self, mock_post: MagicMock, mock_fetch: MagicMock, tmp_path: Path,
    ) -> None:
        from overdrive.comments.models import PRComment

        mock_fetch.return_value = [
            PRComment(id="c1", author="alice", body="Fix this", platform_id="100"),
        ]
        # post_comments_batch is not called in dry-run mode.

        worker_calls: list[str] = []

        def mock_run_step(*, task: Any, step: str, attempt: int) -> StepResult:
            worker_calls.append(step)
            # Worker step pr_review_comment must produce comments output.
            if step == "pr_review_comment":
                output = json.dumps({
                    "comments": [{"body": "Issue found", "path": "a.py", "line": 1}],
                    "summary": "1 issue",
                })
                task.metadata.setdefault("step_outputs", {})[step] = output
            return StepResult(status="ok")

        adapter = MagicMock()
        adapter.run_step = mock_run_step
        container, service, _ = _service(tmp_path, adapter=adapter)

        task = _make_review_task("pr_review_comment")
        container.tasks.upsert(task)
        result = service.run_task(task.id)

        assert result.status == "done"
        steps = _step_names(container, task.id)
        assert steps == ["fetch_comments", "pr_review_comment", "post_comments"]

        # fetch_comments and post_comments are orchestrator-side, not worker.
        assert "fetch_comments" not in worker_calls
        assert "post_comments" not in worker_calls
        assert "pr_review_comment" in worker_calls

    @patch("overdrive.runtime.orchestrator.task_executor.fetch_pr_comments")
    def test_fetch_comments_called_with_correct_args(
        self, mock_fetch: MagicMock, tmp_path: Path,
    ) -> None:
        from overdrive.comments.models import PRComment

        mock_fetch.return_value = [
            PRComment(id="c1", author="alice", body="Note", platform_id="100"),
        ]

        adapter = MagicMock()
        adapter.run_step = lambda *, task, step, attempt: StepResult(status="ok")
        container, service, _ = _service(tmp_path, adapter=adapter)

        task = _make_review_task("pr_review_comment")
        container.tasks.upsert(task)
        service.run_task(task.id)

        mock_fetch.assert_called_once()
        call_args = mock_fetch.call_args
        assert call_args[0][0] == "org"  # owner
        assert call_args[0][1] == "repo"  # repo
        assert call_args[0][2] == 42  # number


# ---------------------------------------------------------------------------
# 3. Pipeline execution tests — pr_review_summarize
# ---------------------------------------------------------------------------


class TestPrReviewSummarizePipeline:
    """pr_review_summarize: fetch_comments → pr_review_summarize."""

    @patch("overdrive.runtime.orchestrator.task_executor.fetch_pr_comments")
    def test_step_sequence(self, mock_fetch: MagicMock, tmp_path: Path) -> None:
        from overdrive.comments.models import PRComment

        mock_fetch.return_value = [
            PRComment(id="c1", author="alice", body="LGTM", platform_id="100"),
        ]

        worker_calls: list[str] = []

        def mock_run_step(*, task: Any, step: str, attempt: int) -> StepResult:
            worker_calls.append(step)
            return StepResult(status="ok")

        adapter = MagicMock()
        adapter.run_step = mock_run_step
        container, service, _ = _service(tmp_path, adapter=adapter)

        task = _make_review_task("pr_review_summarize")
        container.tasks.upsert(task)
        result = service.run_task(task.id)

        assert result.status == "done"
        steps = _step_names(container, task.id)
        assert steps == ["fetch_comments", "pr_review_summarize"]

        assert "fetch_comments" not in worker_calls
        assert "pr_review_summarize" in worker_calls

    @patch("overdrive.runtime.orchestrator.task_executor.fetch_pr_comments")
    def test_no_commit_step(self, mock_fetch: MagicMock, tmp_path: Path) -> None:
        """Summarize pipeline has no commit step."""
        from overdrive.comments.models import PRComment

        mock_fetch.return_value = []

        adapter = MagicMock()
        adapter.run_step = lambda *, task, step, attempt: StepResult(status="ok")
        container, service, _ = _service(tmp_path, adapter=adapter)

        task = _make_review_task("pr_review_summarize")
        container.tasks.upsert(task)
        result = service.run_task(task.id)

        steps = _step_names(container, task.id)
        assert "commit" not in steps
        assert "review" not in steps


# ---------------------------------------------------------------------------
# 4. Pipeline execution tests — pr_review_fix_respond
# ---------------------------------------------------------------------------


class TestPrReviewFixRespondPipeline:
    """pr_review_fix_respond: fetch_comments → pr_review_fix_respond → implement → verify → review → post_comment_responses → commit."""

    @patch("overdrive.runtime.orchestrator.task_executor.fetch_pr_comments")
    @patch("overdrive.runtime.orchestrator.task_executor.post_comments_batch")
    def test_step_sequence(
        self, mock_post: MagicMock, mock_fetch: MagicMock, tmp_path: Path,
    ) -> None:
        from overdrive.comments.models import PRComment

        mock_fetch.return_value = [
            PRComment(id="comment-0", author="reviewer", body="Fix this bug", platform_id="100"),
        ]

        worker_calls: list[str] = []

        def mock_run_step(*, task: Any, step: str, attempt: int) -> StepResult:
            worker_calls.append(step)
            if step == "pr_review_fix_respond":
                output = json.dumps({
                    "addressed_comments": [
                        {"original_comment_id": "comment-0", "response_body": "Fixed in abc123"},
                    ],
                })
                task.metadata.setdefault("step_outputs", {})[step] = output
            return StepResult(status="ok")

        adapter = MagicMock()
        adapter.run_step = mock_run_step
        container, service, _ = _service(tmp_path, adapter=adapter)

        task = _make_review_task("pr_review_fix_respond")
        container.tasks.upsert(task)
        result = service.run_task(task.id)

        assert result.status == "done"
        steps = _step_names(container, task.id)
        assert steps == [
            "fetch_comments",
            "pr_review_fix_respond",
            "implement",
            "verify",
            "review",
            "post_comment_responses",
            "commit",
        ]

        # Orchestrator-side steps not in worker calls.
        assert "fetch_comments" not in worker_calls
        assert "post_comment_responses" not in worker_calls
        # Worker-dispatched steps present.
        assert "pr_review_fix_respond" in worker_calls
        assert "implement" in worker_calls
        assert "verify" in worker_calls


# ---------------------------------------------------------------------------
# 5. Regression tests — legacy pr_review / mr_review
# ---------------------------------------------------------------------------


class TestLegacyPrReviewRegression:
    """Legacy pr_review task_type (no review_mode) matches fix_only behavior."""

    def test_legacy_pr_review_step_sequence(self, tmp_path: Path) -> None:
        adapter = MagicMock()
        adapter.run_step = lambda *, task, step, attempt: StepResult(status="ok")
        container, service, _ = _service(tmp_path, adapter=adapter)

        task = Task(
            title="Legacy PR Review",
            task_type="pr_review",
            status="queued",
            hitl_mode="autopilot",
            metadata={
                "source_description": "Legacy PR",
                "source_diff": "diff",
                "source_stat": "1 file",
                "source_pr_number": 42,
            },
        )
        container.tasks.upsert(task)
        result = service.run_task(task.id)

        assert result.status == "done"
        steps = _step_names(container, task.id)
        assert steps == ["pr_review", "implement", "verify", "review", "commit"]

    def test_legacy_mr_review_step_sequence(self, tmp_path: Path) -> None:
        adapter = MagicMock()
        adapter.run_step = lambda *, task, step, attempt: StepResult(status="ok")
        container, service, _ = _service(tmp_path, adapter=adapter)

        task = Task(
            title="Legacy MR Review",
            task_type="mr_review",
            status="queued",
            hitl_mode="autopilot",
            metadata={
                "source_description": "Legacy MR",
                "source_diff": "diff",
                "source_stat": "1 file",
                "source_mr_number": 15,
            },
        )
        container.tasks.upsert(task)
        result = service.run_task(task.id)

        assert result.status == "done"
        steps = _step_names(container, task.id)
        assert steps == ["mr_review", "implement", "verify", "review", "commit"]

    def test_legacy_pr_review_identical_to_fix_only(self, tmp_path: Path) -> None:
        """Legacy pr_review pipeline has same step structure as pr_review_fix_only."""
        from overdrive.pipelines.registry import PipelineRegistry

        registry = PipelineRegistry()
        legacy = registry.get("pr_review")
        fix_only = registry.get("pr_review_fix_only")

        # Both have the same step names except the first step name differs.
        legacy_steps = legacy.step_names()
        fix_only_steps = fix_only.step_names()

        # First step is pr_review in both.
        assert legacy_steps[0] == "pr_review"
        assert fix_only_steps[0] == "pr_review"
        # Remaining steps identical.
        assert legacy_steps[1:] == fix_only_steps[1:]

    def test_legacy_pr_review_no_comment_metadata(self, tmp_path: Path) -> None:
        """Legacy task has no review_mode, review_decision, or comment_dry_run in metadata."""
        adapter = MagicMock()
        adapter.run_step = lambda *, task, step, attempt: StepResult(status="ok")
        container, service, _ = _service(tmp_path, adapter=adapter)

        task = Task(
            title="Legacy PR Review",
            task_type="pr_review",
            status="queued",
            hitl_mode="autopilot",
            metadata={
                "source_description": "Legacy PR",
                "source_diff": "diff",
                "source_stat": "1 file",
            },
        )
        container.tasks.upsert(task)
        service.run_task(task.id)

        stored = container.tasks.get(task.id)
        assert stored is not None
        meta = stored.metadata or {}
        assert "review_mode" not in meta
        assert "review_decision" not in meta
        assert "comment_dry_run" not in meta
        assert "fetched_comments" not in meta


# ---------------------------------------------------------------------------
# 6. Pipeline template verification
# ---------------------------------------------------------------------------


class TestPipelineTemplateStored:
    """After execution, the pipeline_template field on the task matches the pipeline definition."""

    @pytest.mark.parametrize("task_type,expected_steps", [
        ("pr_review_fix_only", ["pr_review", "implement", "verify", "review", "commit"]),
        ("pr_review_comment", ["fetch_comments", "pr_review_comment", "post_comments"]),
        ("pr_review_summarize", ["fetch_comments", "pr_review_summarize"]),
        (
            "pr_review_fix_respond",
            ["fetch_comments", "pr_review_fix_respond", "implement", "verify", "review", "post_comment_responses", "commit"],
        ),
    ])
    @patch("overdrive.runtime.orchestrator.task_executor.fetch_pr_comments")
    def test_pipeline_template_matches_registry(
        self,
        mock_fetch: MagicMock,
        tmp_path: Path,
        task_type: str,
        expected_steps: list[str],
    ) -> None:
        from overdrive.comments.models import PRComment

        mock_fetch.return_value = []

        adapter = MagicMock()

        def mock_run_step(*, task: Any, step: str, attempt: int) -> StepResult:
            if step == "pr_review_comment":
                task.metadata.setdefault("step_outputs", {})[step] = json.dumps({
                    "comments": [], "summary": "",
                })
            elif step == "pr_review_fix_respond":
                task.metadata.setdefault("step_outputs", {})[step] = json.dumps({
                    "addressed_comments": [],
                })
            return StepResult(status="ok")

        adapter.run_step = mock_run_step
        container, service, _ = _service(tmp_path, adapter=adapter)

        task = _make_review_task(task_type)
        container.tasks.upsert(task)
        service.run_task(task.id)

        stored = container.tasks.get(task.id)
        assert stored is not None
        assert stored.pipeline_template == expected_steps


# ---------------------------------------------------------------------------
# 7. Review mode pipeline registry validation
# ---------------------------------------------------------------------------


class TestPipelineRegistryReviewModes:
    """Verify all review-mode pipelines are registered and resolve correctly."""

    @pytest.mark.parametrize("task_type", [
        "pr_review_fix_only",
        "pr_review_comment",
        "pr_review_summarize",
        "pr_review_fix_respond",
    ])
    def test_pipeline_resolves_for_task_type(self, task_type: str) -> None:
        from overdrive.pipelines.registry import PipelineRegistry

        registry = PipelineRegistry()
        template = registry.resolve_for_task_type(task_type)
        assert template is not None
        assert task_type in template.task_types

    @pytest.mark.parametrize("task_type,has_commit", [
        ("pr_review_fix_only", True),
        ("pr_review_comment", False),
        ("pr_review_summarize", False),
        ("pr_review_fix_respond", True),
    ])
    def test_commit_step_presence(self, task_type: str, has_commit: bool) -> None:
        from overdrive.pipelines.registry import PipelineRegistry

        registry = PipelineRegistry()
        template = registry.resolve_for_task_type(task_type)
        steps = template.step_names()
        if has_commit:
            assert "commit" in steps
        else:
            assert "commit" not in steps

    @pytest.mark.parametrize("task_type,has_review", [
        ("pr_review_fix_only", True),
        ("pr_review_comment", False),
        ("pr_review_summarize", False),
        ("pr_review_fix_respond", True),
    ])
    def test_review_step_presence(self, task_type: str, has_review: bool) -> None:
        from overdrive.pipelines.registry import PipelineRegistry

        registry = PipelineRegistry()
        template = registry.resolve_for_task_type(task_type)
        steps = template.step_names()
        if has_review:
            assert "review" in steps
        else:
            assert "review" not in steps

    @pytest.mark.parametrize("task_type,has_fetch", [
        ("pr_review_fix_only", False),
        ("pr_review_comment", True),
        ("pr_review_summarize", True),
        ("pr_review_fix_respond", True),
    ])
    def test_fetch_comments_step_presence(self, task_type: str, has_fetch: bool) -> None:
        from overdrive.pipelines.registry import PipelineRegistry

        registry = PipelineRegistry()
        template = registry.resolve_for_task_type(task_type)
        steps = template.step_names()
        if has_fetch:
            assert "fetch_comments" in steps
        else:
            assert "fetch_comments" not in steps


# ---------------------------------------------------------------------------
# 8. Fetch comments failure blocks pipeline
# ---------------------------------------------------------------------------


class TestFetchCommentsBlocksPipeline:
    """If fetch_comments fails, the pipeline should not continue."""

    @patch("overdrive.runtime.orchestrator.task_executor.fetch_pr_comments")
    def test_fetch_failure_blocks_task(self, mock_fetch: MagicMock, tmp_path: Path) -> None:
        from overdrive.comments.reader import CommentFetchError

        mock_fetch.side_effect = CommentFetchError("GitHub API unavailable")

        adapter = MagicMock()
        adapter.run_step = lambda *, task, step, attempt: StepResult(status="ok")
        container, service, _ = _service(tmp_path, adapter=adapter)

        task = _make_review_task("pr_review_comment")
        container.tasks.upsert(task)
        result = service.run_task(task.id)

        assert result.status == "blocked"
        steps = _step_names(container, task.id)
        assert steps == ["fetch_comments"]
        assert "GitHub API unavailable" in (result.error or "")


# ---------------------------------------------------------------------------
# 9. Verify fix_respond post_comment_responses dry-run in pipeline context
# ---------------------------------------------------------------------------


class TestFixRespondDryRunInPipeline:
    """In pipeline context, post_comment_responses respects comment_dry_run."""

    @patch("overdrive.runtime.orchestrator.task_executor.fetch_pr_comments")
    @patch("overdrive.runtime.orchestrator.task_executor.post_comments_batch")
    def test_dry_run_does_not_call_post_batch(
        self, mock_post: MagicMock, mock_fetch: MagicMock, tmp_path: Path,
    ) -> None:
        from overdrive.comments.models import PRComment

        mock_fetch.return_value = [
            PRComment(id="comment-0", author="reviewer", body="Fix", platform_id="100"),
        ]

        def mock_run_step(*, task: Any, step: str, attempt: int) -> StepResult:
            if step == "pr_review_fix_respond":
                output = json.dumps({
                    "addressed_comments": [
                        {"original_comment_id": "comment-0", "response_body": "Done"},
                    ],
                })
                task.metadata.setdefault("step_outputs", {})[step] = output
            return StepResult(status="ok")

        adapter = MagicMock()
        adapter.run_step = mock_run_step
        container, service, _ = _service(tmp_path, adapter=adapter)

        task = _make_review_task("pr_review_fix_respond", metadata={
            "source_url": "https://github.com/org/repo/pull/42",
            "source_description": "Test PR",
            "source_diff": "diff",
            "source_stat": "1 file",
            "source_pr_number": 42,
            "comment_dry_run": True,
        })
        container.tasks.upsert(task)
        result = service.run_task(task.id)

        assert result.status == "done"
        # post_comments_batch should NOT be called in dry-run mode.
        mock_post.assert_not_called()
