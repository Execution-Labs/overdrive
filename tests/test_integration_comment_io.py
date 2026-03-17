"""Integration tests for real gh/glab comment read/write.

These tests require:
- AGENT_ORCHESTRATOR_RUN_INTEGRATION=1
- A valid gh CLI session (authenticated with GitHub)
- A test repository with an open PR (set via env vars below)
- Optionally, a valid glab CLI session for GitLab tests

Environment variables:
- AGENT_ORCHESTRATOR_RUN_INTEGRATION: Set to "1" to run these tests.
- INTEGRATION_GH_OWNER: GitHub repo owner (default: skipped).
- INTEGRATION_GH_REPO: GitHub repo name (default: skipped).
- INTEGRATION_GH_PR_NUMBER: Open PR number to read/write comments (default: skipped).
- INTEGRATION_GLAB_PROJECT_ID: GitLab project ID for MR tests (default: skipped).
- INTEGRATION_GLAB_MR_NUMBER: Open MR number (default: skipped).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.comments.models import CommentPostResult, PRComment
from agent_orchestrator.comments.reader import (
    CommentFetchError,
    fetch_mr_comments,
    fetch_pr_comments,
)
from agent_orchestrator.comments.writer import (
    parse_source_url,
    post_comments_batch,
    post_pr_review_decision,
)

RUN_INTEGRATION = os.getenv("AGENT_ORCHESTRATOR_RUN_INTEGRATION", "0") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_INTEGRATION,
    reason="Set AGENT_ORCHESTRATOR_RUN_INTEGRATION=1 to run integration tests",
)


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _glab_available() -> bool:
    return shutil.which("glab") is not None


def _gh_env() -> tuple[str, str, int] | None:
    """Return (owner, repo, pr_number) from env vars, or None if not set."""
    owner = os.getenv("INTEGRATION_GH_OWNER", "")
    repo = os.getenv("INTEGRATION_GH_REPO", "")
    pr_num = os.getenv("INTEGRATION_GH_PR_NUMBER", "")
    if owner and repo and pr_num:
        return owner, repo, int(pr_num)
    return None


def _glab_env() -> tuple[str, int] | None:
    """Return (project_id, mr_number) from env vars, or None if not set."""
    project_id = os.getenv("INTEGRATION_GLAB_PROJECT_ID", "")
    mr_num = os.getenv("INTEGRATION_GLAB_MR_NUMBER", "")
    if project_id and mr_num:
        return project_id, int(mr_num)
    return None


def _git_dir() -> Path:
    """Return a git directory for CLI context.

    Uses the current working directory if it's a git repo, otherwise tmp.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return Path.cwd()


# ---------------------------------------------------------------------------
# GitHub comment reading
# ---------------------------------------------------------------------------


class TestGitHubCommentRead:
    """Read comments from a real GitHub PR via gh CLI."""

    def test_fetch_pr_comments_returns_list(self) -> None:
        if not _gh_available():
            pytest.skip("gh CLI not available")
        env = _gh_env()
        if env is None:
            pytest.skip("INTEGRATION_GH_OWNER/REPO/PR_NUMBER not set")

        owner, repo, pr_number = env
        comments = fetch_pr_comments(owner, repo, pr_number, _git_dir())

        assert isinstance(comments, list)
        for c in comments:
            assert isinstance(c, PRComment)
            assert c.id
            assert c.author  # Should have an author

    def test_fetch_pr_comments_nonexistent_pr_raises(self) -> None:
        if not _gh_available():
            pytest.skip("gh CLI not available")
        env = _gh_env()
        if env is None:
            pytest.skip("INTEGRATION_GH_OWNER/REPO/PR_NUMBER not set")

        owner, repo, _ = env
        with pytest.raises(CommentFetchError):
            fetch_pr_comments(owner, repo, 999999, _git_dir())


# ---------------------------------------------------------------------------
# GitHub comment writing
# ---------------------------------------------------------------------------


class TestGitHubCommentWrite:
    """Post a comment to a real GitHub PR and verify it succeeds.

    WARNING: This creates real comments on the specified PR.
    """

    def test_post_single_issue_comment(self) -> None:
        if not _gh_available():
            pytest.skip("gh CLI not available")
        env = _gh_env()
        if env is None:
            pytest.skip("INTEGRATION_GH_OWNER/REPO/PR_NUMBER not set")

        owner, repo, pr_number = env
        platform_info = {
            "platform": "github",
            "owner": owner,
            "repo": repo,
            "number": pr_number,
        }
        comments = [{"body": "[integration-test] Automated test comment — safe to ignore"}]

        results = post_comments_batch(platform_info, comments, git_dir=_git_dir())

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].platform_id  # Should have a platform-assigned ID


# ---------------------------------------------------------------------------
# GitLab comment reading
# ---------------------------------------------------------------------------


class TestGitLabCommentRead:
    """Read comments from a real GitLab MR via glab CLI."""

    def test_fetch_mr_comments_returns_list(self) -> None:
        if not _glab_available():
            pytest.skip("glab CLI not available")
        env = _glab_env()
        if env is None:
            pytest.skip("INTEGRATION_GLAB_PROJECT_ID/MR_NUMBER not set")

        project_id, mr_number = env
        comments = fetch_mr_comments(project_id, mr_number, cwd=_git_dir())

        assert isinstance(comments, list)
        for c in comments:
            assert isinstance(c, PRComment)
            assert c.id


# ---------------------------------------------------------------------------
# GitLab comment writing
# ---------------------------------------------------------------------------


class TestGitLabCommentWrite:
    """Post a comment to a real GitLab MR via glab CLI.

    WARNING: This creates real comments on the specified MR.
    """

    def test_post_single_mr_comment(self) -> None:
        if not _glab_available():
            pytest.skip("glab CLI not available")
        env = _glab_env()
        if env is None:
            pytest.skip("INTEGRATION_GLAB_PROJECT_ID/MR_NUMBER not set")

        project_id, mr_number = env
        platform_info = {
            "platform": "gitlab",
            "project_id": project_id,
            "number": mr_number,
        }
        comments = [{"body": "[integration-test] Automated test comment — safe to ignore"}]

        results = post_comments_batch(platform_info, comments, git_dir=_git_dir())

        assert len(results) == 1
        assert results[0].success is True


# ---------------------------------------------------------------------------
# URL parsing round-trip
# ---------------------------------------------------------------------------


class TestParseSourceUrl:
    """Verify parse_source_url produces correct platform_info for real URLs."""

    def test_github_url(self) -> None:
        info = parse_source_url("https://github.com/octocat/Hello-World/pull/123")
        assert info["platform"] == "github"
        assert info["owner"] == "octocat"
        assert info["repo"] == "Hello-World"
        assert info["number"] == 123

    def test_gitlab_url(self) -> None:
        info = parse_source_url("https://gitlab.com/group/project/-/merge_requests/456")
        assert info["platform"] == "gitlab"
        assert info["number"] == 456

    def test_invalid_url_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse"):
            parse_source_url("https://example.com/not-a-pr")
