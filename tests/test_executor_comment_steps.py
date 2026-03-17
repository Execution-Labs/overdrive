"""Tests for orchestrator-side comment steps in TaskExecutor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_orchestrator.comments.models import CommentPostResult, PRComment
from agent_orchestrator.comments.reader import CommentFetchError
from agent_orchestrator.runtime.domain.models import RunRecord, Task, now_iso
from agent_orchestrator.runtime.orchestrator.task_executor import TaskExecutor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROJECT_DIR = Path("/tmp/fake-project")


def _make_service_mock() -> MagicMock:
    """Create a mock OrchestratorService with required attributes."""
    svc = MagicMock()
    svc.container.project_dir = _PROJECT_DIR
    svc._step_project_dir.return_value = _PROJECT_DIR
    svc._workdoc_canonical_path.return_value = _PROJECT_DIR / ".workdoc.md"
    svc._emit_task_blocked = MagicMock()
    svc.bus.emit = MagicMock()
    return svc


def _make_task(metadata: dict[str, Any] | None = None) -> Task:
    """Create a task with sensible defaults for comment step tests."""
    return Task(
        id="task-test-001",
        title="Test PR Review",
        task_type="pr_review_comment",
        status="in_progress",
        metadata=metadata or {},
    )


def _make_run(task_id: str = "task-test-001") -> RunRecord:
    return RunRecord(task_id=task_id, status="in_progress", started_at=now_iso(), steps=[])


def _sample_comments(count: int = 3) -> list[PRComment]:
    return [
        PRComment(
            id=f"comment-{i}",
            author=f"user-{i}",
            body=f"Comment body {i}",
            path="src/main.py" if i % 2 == 0 else None,
            line=(10 + i) if i % 2 == 0 else None,
            platform_id=str(100 + i),
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# fetch_comments
# ---------------------------------------------------------------------------


class TestExecuteFetchComments:
    def _make_executor(self) -> tuple[TaskExecutor, MagicMock]:
        svc = _make_service_mock()
        executor = TaskExecutor(svc)
        return executor, svc

    @patch("agent_orchestrator.runtime.orchestrator.task_executor.fetch_pr_comments")
    def test_success_github(self, mock_fetch: MagicMock) -> None:
        comments = _sample_comments(3)
        mock_fetch.return_value = comments
        executor, svc = self._make_executor()
        task = _make_task({"source_url": "https://github.com/owner/repo/pull/42"})
        run = _make_run()

        result = executor._execute_fetch_comments(task, run)

        assert result == "ok"
        assert len(task.metadata["fetched_comments"]) == 3
        assert task.metadata["formatted_comments"] != ""
        assert task.metadata["comment_platform"]["platform"] == "github"
        assert task.metadata["comment_platform"]["number"] == 42
        # Step recorded in run.
        assert len(run.steps) == 1
        assert run.steps[0]["step"] == "fetch_comments"
        assert run.steps[0]["status"] == "ok"
        assert run.steps[0]["comment_count"] == 3

    @patch("agent_orchestrator.runtime.orchestrator.task_executor.fetch_mr_comments")
    def test_success_gitlab(self, mock_fetch: MagicMock) -> None:
        comments = _sample_comments(2)
        mock_fetch.return_value = comments
        executor, svc = self._make_executor()
        task = _make_task({"source_url": "https://gitlab.com/group/project/-/merge_requests/10"})
        run = _make_run()

        result = executor._execute_fetch_comments(task, run)

        assert result == "ok"
        assert task.metadata["comment_platform"]["platform"] == "gitlab"
        assert len(task.metadata["fetched_comments"]) == 2

    @patch("agent_orchestrator.runtime.orchestrator.task_executor.fetch_pr_comments")
    def test_empty_comments(self, mock_fetch: MagicMock) -> None:
        mock_fetch.return_value = []
        executor, svc = self._make_executor()
        task = _make_task({"source_url": "https://github.com/o/r/pull/1"})
        run = _make_run()

        result = executor._execute_fetch_comments(task, run)

        assert result == "ok"
        assert task.metadata["fetched_comments"] == []
        assert task.metadata["formatted_comments"] == ""

    @patch("agent_orchestrator.runtime.orchestrator.task_executor.fetch_pr_comments")
    def test_fetch_error_blocks(self, mock_fetch: MagicMock) -> None:
        mock_fetch.side_effect = CommentFetchError("API error")
        executor, svc = self._make_executor()
        task = _make_task({"source_url": "https://github.com/o/r/pull/1"})
        run = _make_run()

        result = executor._execute_fetch_comments(task, run)

        assert result == "blocked"
        assert task.status == "blocked"
        assert "API error" in (task.error or "")
        assert run.steps[0]["status"] == "error"
        svc._emit_task_blocked.assert_called_once()

    def test_missing_source_url_blocks(self) -> None:
        executor, svc = self._make_executor()
        task = _make_task({})  # No source_url, no source_pr_number
        run = _make_run()

        result = executor._execute_fetch_comments(task, run)

        assert result == "blocked"
        assert task.status == "blocked"
        assert "source_url" in (task.error or "").lower() or "source" in (task.error or "").lower()

    @patch("agent_orchestrator.runtime.orchestrator.task_executor.fetch_pr_comments")
    def test_fallback_to_pr_number(self, mock_fetch: MagicMock) -> None:
        comments = _sample_comments(1)
        mock_fetch.return_value = comments
        executor, svc = self._make_executor()

        # Mock the git remote inference.
        with patch.object(executor, "_infer_github_owner_repo", return_value=("inferred-owner", "inferred-repo")):
            task = _make_task({"source_pr_number": 99})
            run = _make_run()

            result = executor._execute_fetch_comments(task, run)

        assert result == "ok"
        mock_fetch.assert_called_once_with("inferred-owner", "inferred-repo", 99, _PROJECT_DIR)


# ---------------------------------------------------------------------------
# post_comments
# ---------------------------------------------------------------------------


class TestExecutePostComments:
    def _make_executor(self) -> tuple[TaskExecutor, MagicMock]:
        svc = _make_service_mock()
        # workdoc doesn't exist in test env — prevent file write errors.
        svc._workdoc_canonical_path.return_value = Path("/tmp/nonexistent-workdoc.md")
        executor = TaskExecutor(svc)
        return executor, svc

    @patch("agent_orchestrator.runtime.orchestrator.task_executor.post_comments_batch")
    def test_success(self, mock_batch: MagicMock) -> None:
        mock_batch.return_value = [
            CommentPostResult(success=True, platform_id="1001"),
            CommentPostResult(success=True, platform_id="1002"),
        ]
        executor, svc = self._make_executor()
        worker_output = json.dumps({
            "comments": [
                {"path": "src/a.py", "line": 10, "body": "Fix this", "severity": "high"},
                {"body": "General note", "severity": "low"},
            ],
            "summary": "Found 2 issues",
        })
        task = _make_task({
            "comment_platform": {"platform": "github", "owner": "o", "repo": "r", "number": 1},
            "step_outputs": {"pr_review_comment": worker_output},
        })
        run = _make_run()

        result = executor._execute_post_comments(task, run)

        assert result == "ok"
        assert len(task.metadata["posted_comments"]) == 2
        assert run.steps[0]["posted_count"] == 2
        assert run.steps[0]["failed_count"] == 0
        assert run.steps[0]["dry_run"] is False

    def test_dry_run(self) -> None:
        executor, svc = self._make_executor()
        worker_output = json.dumps({
            "comments": [{"body": "Comment 1"}],
            "summary": "Summary",
        })
        task = _make_task({
            "comment_platform": {"platform": "github", "owner": "o", "repo": "r", "number": 1},
            "step_outputs": {"pr_review_comment": worker_output},
            "dry_run": True,
        })
        run = _make_run()

        result = executor._execute_post_comments(task, run)

        assert result == "ok"
        assert run.steps[0]["dry_run"] is True
        assert run.steps[0]["posted_count"] == 1
        # No actual posting should have happened.
        assert task.metadata["posted_comments"][0]["platform_id"] == "dry_run"

    @patch("agent_orchestrator.runtime.orchestrator.task_executor.post_comments_batch")
    def test_total_failure_blocks(self, mock_batch: MagicMock) -> None:
        mock_batch.return_value = [
            CommentPostResult(success=False, error="401 Unauthorized"),
        ]
        executor, svc = self._make_executor()
        worker_output = json.dumps({
            "comments": [{"body": "Comment 1"}],
            "summary": "Summary",
        })
        task = _make_task({
            "comment_platform": {"platform": "github", "owner": "o", "repo": "r", "number": 1},
            "step_outputs": {"pr_review_comment": worker_output},
        })
        run = _make_run()

        result = executor._execute_post_comments(task, run)

        assert result == "blocked"
        assert task.status == "blocked"
        svc._emit_task_blocked.assert_called_once()

    @patch("agent_orchestrator.runtime.orchestrator.task_executor.post_comments_batch")
    def test_partial_failure_ok(self, mock_batch: MagicMock) -> None:
        mock_batch.return_value = [
            CommentPostResult(success=True, platform_id="1001"),
            CommentPostResult(success=False, error="rate limited"),
        ]
        executor, svc = self._make_executor()
        worker_output = json.dumps({
            "comments": [{"body": "A"}, {"body": "B"}],
            "summary": "Summary",
        })
        task = _make_task({
            "comment_platform": {"platform": "github", "owner": "o", "repo": "r", "number": 1},
            "step_outputs": {"pr_review_comment": worker_output},
        })
        run = _make_run()

        result = executor._execute_post_comments(task, run)

        assert result == "ok"
        assert run.steps[0]["posted_count"] == 1
        assert run.steps[0]["failed_count"] == 1

    def test_missing_platform_blocks(self) -> None:
        executor, svc = self._make_executor()
        task = _make_task({"step_outputs": {"pr_review_comment": "{}"}})
        run = _make_run()

        result = executor._execute_post_comments(task, run)

        assert result == "blocked"
        assert "comment_platform" in (task.error or "")

    def test_malformed_output_blocks(self) -> None:
        executor, svc = self._make_executor()
        task = _make_task({
            "comment_platform": {"platform": "github", "owner": "o", "repo": "r", "number": 1},
            "step_outputs": {"pr_review_comment": "not valid json"},
        })
        run = _make_run()

        result = executor._execute_post_comments(task, run)

        assert result == "blocked"
        assert "parse" in (task.error or "").lower()

    @patch("agent_orchestrator.runtime.orchestrator.task_executor.post_comments_batch")
    @patch("agent_orchestrator.runtime.orchestrator.task_executor.post_pr_review_decision")
    def test_review_decision_posted(self, mock_decision: MagicMock, mock_batch: MagicMock) -> None:
        mock_batch.return_value = [CommentPostResult(success=True, platform_id="1001")]
        mock_decision.return_value = CommentPostResult(success=True, platform_id="2001")
        executor, svc = self._make_executor()
        worker_output = json.dumps({
            "comments": [{"body": "Issue"}],
            "summary": "Needs changes",
        })
        task = _make_task({
            "comment_platform": {"platform": "github", "owner": "o", "repo": "r", "number": 1},
            "step_outputs": {"pr_review_comment": worker_output},
            "review_decision": {"decision": "request_changes", "body": "Please fix"},
        })
        run = _make_run()

        result = executor._execute_post_comments(task, run)

        assert result == "ok"
        mock_decision.assert_called_once()
        assert task.metadata["review_decision_result"]["success"] is True


# ---------------------------------------------------------------------------
# post_comment_responses
# ---------------------------------------------------------------------------


class TestExecutePostCommentResponses:
    def _make_executor(self) -> tuple[TaskExecutor, MagicMock]:
        svc = _make_service_mock()
        svc._workdoc_canonical_path.return_value = Path("/tmp/nonexistent-workdoc.md")
        executor = TaskExecutor(svc)
        return executor, svc

    @patch("agent_orchestrator.runtime.orchestrator.task_executor.post_comments_batch")
    def test_success(self, mock_batch: MagicMock) -> None:
        mock_batch.return_value = [CommentPostResult(success=True, platform_id="3001")]
        executor, svc = self._make_executor()
        worker_output = json.dumps({
            "addressed_comments": [
                {"original_comment_id": "comment-0", "response_body": "Fixed in abc123"},
            ],
        })
        task = _make_task({
            "comment_platform": {"platform": "github", "owner": "o", "repo": "r", "number": 1},
            "step_outputs": {"pr_review_fix_respond": worker_output},
            "fetched_comments": [
                {"id": "comment-0", "platform_id": "100", "author": "user", "body": "Fix this"},
            ],
        })
        run = _make_run()

        result = executor._execute_post_comment_responses(task, run)

        assert result == "ok"
        assert len(task.metadata["posted_responses"]) == 1
        assert run.steps[0]["posted_count"] == 1
        # Verify in_reply_to was resolved to platform_id 100.
        call_args = mock_batch.call_args
        posted_comments = call_args[0][1]
        assert posted_comments[0]["in_reply_to"] == 100

    def test_dry_run(self) -> None:
        executor, svc = self._make_executor()
        worker_output = json.dumps({
            "addressed_comments": [
                {"original_comment_id": "comment-0", "response_body": "Done"},
            ],
        })
        task = _make_task({
            "comment_platform": {"platform": "github", "owner": "o", "repo": "r", "number": 1},
            "step_outputs": {"pr_review_fix_respond": worker_output},
            "fetched_comments": [{"id": "comment-0", "platform_id": "100"}],
            "dry_run": True,
        })
        run = _make_run()

        result = executor._execute_post_comment_responses(task, run)

        assert result == "ok"
        assert run.steps[0]["dry_run"] is True
        assert task.metadata["posted_responses"][0]["platform_id"] == "dry_run"

    def test_missing_original_comment_posts_as_top_level(self) -> None:
        executor, svc = self._make_executor()
        worker_output = json.dumps({
            "addressed_comments": [
                {"original_comment_id": "nonexistent-id", "response_body": "Reply"},
            ],
        })
        task = _make_task({
            "comment_platform": {"platform": "github", "owner": "o", "repo": "r", "number": 1},
            "step_outputs": {"pr_review_fix_respond": worker_output},
            "fetched_comments": [],  # No matching comment.
            "dry_run": True,
        })
        run = _make_run()

        result = executor._execute_post_comment_responses(task, run)

        # Should still succeed (posts as top-level in dry_run mode).
        assert result == "ok"
        assert run.steps[0]["posted_count"] == 1

    def test_skips_empty_response_body(self) -> None:
        executor, svc = self._make_executor()
        worker_output = json.dumps({
            "addressed_comments": [
                {"original_comment_id": "comment-0", "response_body": ""},
                {"original_comment_id": "comment-1", "response_body": "Fixed"},
            ],
        })
        task = _make_task({
            "comment_platform": {"platform": "github", "owner": "o", "repo": "r", "number": 1},
            "step_outputs": {"pr_review_fix_respond": worker_output},
            "fetched_comments": [
                {"id": "comment-0", "platform_id": "100"},
                {"id": "comment-1", "platform_id": "101"},
            ],
            "dry_run": True,
        })
        run = _make_run()

        result = executor._execute_post_comment_responses(task, run)

        assert result == "ok"
        assert run.steps[0]["skipped_count"] == 1
        assert run.steps[0]["posted_count"] == 1

    @patch("agent_orchestrator.runtime.orchestrator.task_executor.post_comments_batch")
    def test_total_failure_blocks(self, mock_batch: MagicMock) -> None:
        mock_batch.return_value = [CommentPostResult(success=False, error="403")]
        executor, svc = self._make_executor()
        worker_output = json.dumps({
            "addressed_comments": [
                {"original_comment_id": "comment-0", "response_body": "Done"},
            ],
        })
        task = _make_task({
            "comment_platform": {"platform": "github", "owner": "o", "repo": "r", "number": 1},
            "step_outputs": {"pr_review_fix_respond": worker_output},
            "fetched_comments": [{"id": "comment-0", "platform_id": "100"}],
        })
        run = _make_run()

        result = executor._execute_post_comment_responses(task, run)

        assert result == "blocked"
        assert task.status == "blocked"
        svc._emit_task_blocked.assert_called_once()

    def test_missing_platform_blocks(self) -> None:
        executor, svc = self._make_executor()
        task = _make_task({
            "step_outputs": {"pr_review_fix_respond": "{}"},
        })
        run = _make_run()

        result = executor._execute_post_comment_responses(task, run)

        assert result == "blocked"
        assert "comment_platform" in (task.error or "")
