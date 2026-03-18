"""Tests for the PR/MR comment writer module."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from overdrive.comments.writer import (
    CommentPostResult,
    _extract_id_from_response,
    _run_gh_api_post,
    _run_glab_api_post,
    parse_source_url,
    post_comments_batch,
    post_mr_comment,
    post_mr_review_decision,
    post_pr_comment,
    post_pr_review_decision,
)

GIT_DIR = Path("/tmp/fake-repo")


# ---------------------------------------------------------------------------
# parse_source_url
# ---------------------------------------------------------------------------


class TestParseSourceUrl:
    def test_github_pr_url(self) -> None:
        result = parse_source_url("https://github.com/owner/repo/pull/123")
        assert result == {"platform": "github", "owner": "owner", "repo": "repo", "number": 123}

    def test_github_pr_url_with_trailing(self) -> None:
        result = parse_source_url("https://github.com/my-org/my-repo/pull/42/files")
        assert result["platform"] == "github"
        assert result["owner"] == "my-org"
        assert result["repo"] == "my-repo"
        assert result["number"] == 42

    def test_gitlab_mr_url(self) -> None:
        result = parse_source_url("https://gitlab.com/group/project/-/merge_requests/456")
        assert result == {"platform": "gitlab", "project_id": "group%2Fproject", "number": 456}

    def test_gitlab_mr_subgroup(self) -> None:
        result = parse_source_url("https://gitlab.example.com/org/sub/repo/-/merge_requests/7")
        assert result["platform"] == "gitlab"
        assert result["project_id"] == "org%2Fsub%2Frepo"
        assert result["number"] == 7

    def test_invalid_url(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse"):
            parse_source_url("https://example.com/not-a-pr")

    def test_empty_url(self) -> None:
        with pytest.raises(ValueError):
            parse_source_url("")


# ---------------------------------------------------------------------------
# _extract_id_from_response
# ---------------------------------------------------------------------------


class TestExtractIdFromResponse:
    def test_valid_json(self) -> None:
        assert _extract_id_from_response('{"id": 42, "url": "..."}') == "42"

    def test_missing_id(self) -> None:
        assert _extract_id_from_response('{"url": "..."}') == ""

    def test_invalid_json(self) -> None:
        assert _extract_id_from_response("not json") == ""


# ---------------------------------------------------------------------------
# _run_gh_api_post / _run_glab_api_post
# ---------------------------------------------------------------------------


class TestRunGhApiPost:
    @patch("overdrive.comments.writer.subprocess.run")
    def test_success(self, mock_run: object) -> None:
        from unittest.mock import MagicMock
        mock_run_fn = mock_run  # type: ignore[assignment]
        mock_run_fn.return_value = MagicMock(returncode=0, stdout='{"id": 1}', stderr="")
        ok, resp = _run_gh_api_post("repos/o/r/issues/1/comments", {"body": "hi"}, GIT_DIR)
        assert ok is True
        assert '"id": 1' in resp

    @patch("overdrive.comments.writer.subprocess.run")
    def test_failure(self, mock_run: object) -> None:
        from unittest.mock import MagicMock
        mock_run_fn = mock_run  # type: ignore[assignment]
        mock_run_fn.return_value = MagicMock(returncode=1, stdout="", stderr="Not Found")
        ok, resp = _run_gh_api_post("repos/o/r/issues/999/comments", {"body": "hi"}, GIT_DIR)
        assert ok is False
        assert "Not Found" in resp

    @patch("overdrive.comments.writer.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=60))
    def test_timeout(self, mock_run: object) -> None:
        ok, resp = _run_gh_api_post("repos/o/r/issues/1/comments", {"body": "hi"}, GIT_DIR)
        assert ok is False
        assert "timed out" in resp


class TestRunGlabApiPost:
    @patch("overdrive.comments.writer.subprocess.run")
    def test_success(self, mock_run: object) -> None:
        from unittest.mock import MagicMock
        mock_run_fn = mock_run  # type: ignore[assignment]
        mock_run_fn.return_value = MagicMock(returncode=0, stdout='{"id": 10}', stderr="")
        ok, resp = _run_glab_api_post("projects/1/merge_requests/1/notes", {"body": "hi"}, GIT_DIR)
        assert ok is True

    @patch("overdrive.comments.writer.subprocess.run")
    def test_failure(self, mock_run: object) -> None:
        from unittest.mock import MagicMock
        mock_run_fn = mock_run  # type: ignore[assignment]
        mock_run_fn.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        ok, resp = _run_glab_api_post("projects/1/merge_requests/1/notes", {"body": "hi"}, GIT_DIR)
        assert ok is False


# ---------------------------------------------------------------------------
# post_pr_comment
# ---------------------------------------------------------------------------


class TestPostPrComment:
    @patch("overdrive.comments.writer._run_gh_api_post")
    def test_general_comment(self, mock_post: object) -> None:
        from unittest.mock import MagicMock
        mock_post_fn = mock_post  # type: ignore[assignment]
        mock_post_fn.return_value = (True, '{"id": 100}')
        result = post_pr_comment("owner", "repo", 1, body="Hello", git_dir=GIT_DIR)
        assert result.success is True
        assert result.platform_id == "100"
        # Should use issue comment endpoint.
        call_args = mock_post_fn.call_args
        assert "issues/1/comments" in call_args[0][0]

    @patch("overdrive.comments.writer._run_gh_api_post")
    def test_inline_comment(self, mock_post: object) -> None:
        from unittest.mock import MagicMock
        mock_post_fn = mock_post  # type: ignore[assignment]
        mock_post_fn.return_value = (True, '{"id": 200}')
        result = post_pr_comment("owner", "repo", 1, path="src/a.py", line=10, body="Fix", git_dir=GIT_DIR)
        assert result.success is True
        # Should use reviews endpoint.
        call_args = mock_post_fn.call_args
        assert "pulls/1/reviews" in call_args[0][0]

    @patch("overdrive.comments.writer._run_gh_api_post")
    def test_reply_comment(self, mock_post: object) -> None:
        from unittest.mock import MagicMock
        mock_post_fn = mock_post  # type: ignore[assignment]
        mock_post_fn.return_value = (True, '{"id": 300}')
        result = post_pr_comment("owner", "repo", 1, body="Thanks", git_dir=GIT_DIR, in_reply_to=50)
        assert result.success is True
        call_args = mock_post_fn.call_args
        assert "comments/50/replies" in call_args[0][0]

    @patch("overdrive.comments.writer._run_gh_api_post")
    def test_failure(self, mock_post: object) -> None:
        from unittest.mock import MagicMock
        mock_post_fn = mock_post  # type: ignore[assignment]
        mock_post_fn.return_value = (False, "API error")
        result = post_pr_comment("owner", "repo", 1, body="Hello", git_dir=GIT_DIR)
        assert result.success is False
        assert result.error == "API error"


# ---------------------------------------------------------------------------
# post_pr_review_decision
# ---------------------------------------------------------------------------


class TestPostPrReviewDecision:
    @patch("overdrive.comments.writer._run_gh_api_post")
    def test_approve(self, mock_post: object) -> None:
        from unittest.mock import MagicMock
        mock_post_fn = mock_post  # type: ignore[assignment]
        mock_post_fn.return_value = (True, '{"id": 500}')
        result = post_pr_review_decision("owner", "repo", 1, decision="approve", body="LGTM", git_dir=GIT_DIR)
        assert result.success is True
        payload = mock_post_fn.call_args[0][1]
        assert payload["event"] == "APPROVE"

    @patch("overdrive.comments.writer._run_gh_api_post")
    def test_request_changes(self, mock_post: object) -> None:
        from unittest.mock import MagicMock
        mock_post_fn = mock_post  # type: ignore[assignment]
        mock_post_fn.return_value = (True, '{"id": 501}')
        result = post_pr_review_decision("owner", "repo", 1, decision="request_changes", body="Needs work", git_dir=GIT_DIR)
        assert result.success is True
        payload = mock_post_fn.call_args[0][1]
        assert payload["event"] == "REQUEST_CHANGES"


# ---------------------------------------------------------------------------
# post_mr_comment
# ---------------------------------------------------------------------------


class TestPostMrComment:
    @patch("overdrive.comments.writer._run_glab_api_post")
    def test_general_note(self, mock_post: object) -> None:
        from unittest.mock import MagicMock
        mock_post_fn = mock_post  # type: ignore[assignment]
        mock_post_fn.return_value = (True, '{"id": 10}')
        result = post_mr_comment("group%2Frepo", 5, body="Note", cwd=GIT_DIR)
        assert result.success is True
        call_args = mock_post_fn.call_args
        assert "/notes" in call_args[0][0]

    @patch("overdrive.comments.writer._run_glab_api_post")
    def test_inline_discussion(self, mock_post: object) -> None:
        from unittest.mock import MagicMock
        mock_post_fn = mock_post  # type: ignore[assignment]
        mock_post_fn.return_value = (True, '{"id": 11}')
        result = post_mr_comment("group%2Frepo", 5, path="src/b.py", line=20, body="Issue", cwd=GIT_DIR)
        assert result.success is True
        call_args = mock_post_fn.call_args
        assert "/discussions" in call_args[0][0]


# ---------------------------------------------------------------------------
# post_mr_review_decision
# ---------------------------------------------------------------------------


class TestPostMrReviewDecision:
    @patch("overdrive.comments.writer._run_glab_api_post")
    def test_approve(self, mock_post: object) -> None:
        from unittest.mock import MagicMock
        mock_post_fn = mock_post  # type: ignore[assignment]
        mock_post_fn.return_value = (True, '{"id": 20}')
        result = post_mr_review_decision("group%2Frepo", 5, decision="approve", body="Approved", cwd=GIT_DIR)
        assert result.success is True
        payload = mock_post_fn.call_args[0][1]
        assert "[APPROVE]" in payload["body"]


# ---------------------------------------------------------------------------
# post_comments_batch
# ---------------------------------------------------------------------------


class TestPostCommentsBatch:
    @patch("overdrive.comments.writer.post_pr_comment")
    @patch("overdrive.comments.writer.time.sleep")
    def test_batch_github(self, mock_sleep: object, mock_post: object) -> None:
        from unittest.mock import MagicMock
        mock_post_fn = mock_post  # type: ignore[assignment]
        mock_post_fn.return_value = CommentPostResult(success=True, platform_id="1")
        platform = {"platform": "github", "owner": "o", "repo": "r", "number": 1}
        comments = [
            {"body": "A", "path": "f.py", "line": 1},
            {"body": "B"},
        ]
        results = post_comments_batch(platform, comments, git_dir=GIT_DIR)
        assert len(results) == 2
        assert all(r.success for r in results)
        # Sleep called between comments.
        assert mock_sleep.call_count == 1  # type: ignore[union-attr]

    @patch("overdrive.comments.writer.post_mr_comment")
    @patch("overdrive.comments.writer.time.sleep")
    def test_batch_gitlab(self, mock_sleep: object, mock_post: object) -> None:
        from unittest.mock import MagicMock
        mock_post_fn = mock_post  # type: ignore[assignment]
        mock_post_fn.return_value = CommentPostResult(success=True, platform_id="2")
        platform = {"platform": "gitlab", "project_id": "g%2Fr", "number": 5}
        comments = [{"body": "A"}]
        results = post_comments_batch(platform, comments, git_dir=GIT_DIR)
        assert len(results) == 1
        assert results[0].success is True

    def test_unsupported_platform(self) -> None:
        platform = {"platform": "bitbucket", "number": 1}
        results = post_comments_batch(platform, [{"body": "A"}], git_dir=GIT_DIR)
        assert len(results) == 1
        assert results[0].success is False
        assert "Unsupported" in (results[0].error or "")
