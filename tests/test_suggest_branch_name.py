"""Unit tests for generate_branch_name_llm()."""

from __future__ import annotations

from dataclasses import field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from overdrive.runtime.orchestrator.git_remote import (
    CommitInfo,
    generate_branch_name_llm,
)
from overdrive.workers.run import WorkerRunResult


def _make_result(response_text: str = "") -> WorkerRunResult:
    """Create a minimal WorkerRunResult with the given response_text."""
    return WorkerRunResult(
        provider="test",
        prompt_path="",
        stdout_path="",
        stderr_path="",
        start_time="",
        end_time="",
        runtime_seconds=0,
        exit_code=0,
        timed_out=False,
        no_heartbeat=False,
        response_text=response_text,
    )


_COMMITS = [
    CommitInfo(sha="abc1234567890", message="Fix auth token expiry bug"),
    CommitInfo(sha="def4567890123", message="Add retry logic for token refresh"),
]


@patch("overdrive.workers.run.run_worker")
@patch("overdrive.prompts.load", return_value="prompt {commits}")
def test_happy_path(mock_load: MagicMock, mock_worker: MagicMock, tmp_path: Path) -> None:
    """Worker returns valid JSON with a branch name."""
    mock_worker.return_value = _make_result('{"branch_name": "fix-auth-flow"}')
    spec = MagicMock()

    result = generate_branch_name_llm(tmp_path, _COMMITS, spec, tmp_path)

    assert result == "push/fix-auth-flow"
    mock_worker.assert_called_once()


@patch("overdrive.workers.run.run_worker")
@patch("overdrive.prompts.load", return_value="prompt {commits}")
def test_sanitization(mock_load: MagicMock, mock_worker: MagicMock, tmp_path: Path) -> None:
    """Branch name with spaces, slashes, special chars, and length >50 is sanitized."""
    long_name = "Fix/Auth Token Expiry & Add Retry!!! Logic For All Users Everywhere In The System"
    mock_worker.return_value = _make_result(f'{{"branch_name": "{long_name}"}}')
    spec = MagicMock()

    result = generate_branch_name_llm(tmp_path, _COMMITS, spec, tmp_path)

    # Should be lowercase, hyphens for spaces/slashes, no special chars, <=50 chars
    name = result.removeprefix("push/")
    assert len(name) <= 50
    assert name == name.lower()
    assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789._-" for c in name)
    assert "--" not in name


@patch("overdrive.workers.run.run_worker")
@patch("overdrive.prompts.load", return_value="prompt {commits}")
def test_malformed_json(mock_load: MagicMock, mock_worker: MagicMock, tmp_path: Path) -> None:
    """Non-JSON response without matching pattern raises ValueError."""
    mock_worker.return_value = _make_result("I think the branch should be called fix-auth")
    spec = MagicMock()

    with pytest.raises(ValueError, match="Failed to parse branch name"):
        generate_branch_name_llm(tmp_path, _COMMITS, spec, tmp_path)


@patch("overdrive.workers.run.run_worker")
@patch("overdrive.prompts.load", return_value="prompt {commits}")
def test_empty_name_after_sanitization(mock_load: MagicMock, mock_worker: MagicMock, tmp_path: Path) -> None:
    """Branch name that becomes empty after sanitization raises ValueError."""
    mock_worker.return_value = _make_result('{"branch_name": "!!!"}')
    spec = MagicMock()

    with pytest.raises(ValueError, match="empty branch name"):
        generate_branch_name_llm(tmp_path, _COMMITS, spec, tmp_path)


@patch("overdrive.workers.run.run_worker")
@patch("overdrive.prompts.load", return_value="prompt {commits}")
def test_worker_exception(mock_load: MagicMock, mock_worker: MagicMock, tmp_path: Path) -> None:
    """Worker raising an exception is wrapped as ValueError."""
    mock_worker.side_effect = RuntimeError("Worker crashed")
    spec = MagicMock()

    with pytest.raises(ValueError, match="Branch name worker failed"):
        generate_branch_name_llm(tmp_path, _COMMITS, spec, tmp_path)


@patch("overdrive.workers.run.run_worker")
@patch("overdrive.prompts.load", return_value="prompt {commits}")
def test_regex_fallback(mock_load: MagicMock, mock_worker: MagicMock, tmp_path: Path) -> None:
    """JSON embedded in surrounding text is extracted via regex fallback."""
    mock_worker.return_value = _make_result(
        'Here is my suggestion: {"branch_name": "my-branch"} hope that helps!'
    )
    spec = MagicMock()

    result = generate_branch_name_llm(tmp_path, _COMMITS, spec, tmp_path)

    assert result == "push/my-branch"
