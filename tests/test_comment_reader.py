"""Tests for the GitHub PR comment reader."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agent_orchestrator.comments.reader import (
    _map_issue_comment,
    _map_review_comment,
    _map_review_decision,
    _parse_paginated_json,
    fetch_pr_comments,
)

# ---------------------------------------------------------------------------
# JSON fixtures
# ---------------------------------------------------------------------------


def _review_comment(
    n: int,
    *,
    path: str = "src/main.py",
    line: int = 10,
    in_reply_to_id: int | None = None,
) -> dict[str, Any]:
    """Generate a single GitHub review comment dict."""
    d: dict[str, Any] = {
        "id": 100 + n,
        "user": {"login": f"reviewer-{n}"},
        "body": f"Review comment {n}",
        "path": path,
        "line": line + n,
        "created_at": f"2026-03-17T0{n % 10}:00:00Z",
    }
    if in_reply_to_id is not None:
        d["in_reply_to_id"] = in_reply_to_id
    return d


def _issue_comment(n: int) -> dict[str, Any]:
    """Generate a single GitHub issue comment dict."""
    return {
        "id": 200 + n,
        "user": {"login": f"commenter-{n}"},
        "body": f"Issue comment {n}",
        "created_at": f"2026-03-17T0{n % 10}:10:00Z",
    }


def _review_decision(n: int, *, state: str = "APPROVED", body: str = "") -> dict[str, Any]:
    """Generate a single GitHub review decision dict."""
    return {
        "id": 300 + n,
        "user": {"login": f"approver-{n}"},
        "body": body or f"Review decision {n}",
        "state": state,
        "submitted_at": f"2026-03-17T0{n % 10}:20:00Z",
    }


FIXTURE_REVIEW_COMMENTS_0 = "[]"
FIXTURE_REVIEW_COMMENTS_5 = json.dumps([_review_comment(i) for i in range(5)])
FIXTURE_ISSUE_COMMENTS_5 = json.dumps([_issue_comment(i) for i in range(5)])
FIXTURE_REVIEWS_5 = json.dumps([_review_decision(i) for i in range(5)])

# 50+ comments via two paginated pages (30 + 20).
_page1 = json.dumps([_review_comment(i) for i in range(30)])
_page2 = json.dumps([_review_comment(30 + i) for i in range(20)])
FIXTURE_PAGINATED_50 = _page1 + _page2


# ---------------------------------------------------------------------------
# _parse_paginated_json
# ---------------------------------------------------------------------------


class TestParsePaginatedJson:
    def test_empty_array(self) -> None:
        assert _parse_paginated_json("[]") == []

    def test_single_page(self) -> None:
        items = [{"a": 1}, {"b": 2}]
        result = _parse_paginated_json(json.dumps(items))
        assert result == items

    def test_multi_page(self) -> None:
        page_a = [{"x": 1}]
        page_b = [{"y": 2}, {"z": 3}]
        raw = json.dumps(page_a) + json.dumps(page_b)
        result = _parse_paginated_json(raw)
        assert result == page_a + page_b

    def test_multi_page_with_whitespace(self) -> None:
        raw = "[{\"a\":1}]  \n  [{\"b\":2}]"
        result = _parse_paginated_json(raw)
        assert len(result) == 2

    def test_invalid_json(self) -> None:
        assert _parse_paginated_json("not json at all") == []

    def test_empty_string(self) -> None:
        assert _parse_paginated_json("") == []

    def test_whitespace_only(self) -> None:
        assert _parse_paginated_json("   \n  ") == []

    def test_non_array_json(self) -> None:
        assert _parse_paginated_json('{"key": "value"}') == []


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------


class TestMappers:
    def test_map_review_comment(self) -> None:
        raw = _review_comment(1, path="lib/foo.py", line=42, in_reply_to_id=99)
        c = _map_review_comment(raw)
        assert c.author == "reviewer-1"
        assert c.body == "Review comment 1"
        assert c.path == "lib/foo.py"
        assert c.line == 43  # line + n
        assert c.in_reply_to == "99"
        assert c.platform_id == "101"
        assert c.created_at == "2026-03-17T01:00:00Z"

    def test_map_review_comment_missing_line(self) -> None:
        raw = _review_comment(0)
        del raw["line"]
        c = _map_review_comment(raw)
        assert c.line is None

    def test_map_review_comment_original_line_fallback(self) -> None:
        raw = _review_comment(0)
        del raw["line"]
        raw["original_line"] = 55
        c = _map_review_comment(raw)
        assert c.line == 55

    def test_map_review_comment_no_in_reply_to(self) -> None:
        raw = _review_comment(0)
        c = _map_review_comment(raw)
        assert c.in_reply_to is None

    def test_map_issue_comment(self) -> None:
        raw = _issue_comment(3)
        c = _map_issue_comment(raw)
        assert c.author == "commenter-3"
        assert c.body == "Issue comment 3"
        assert c.path is None
        assert c.line is None
        assert c.platform_id == "203"

    def test_map_review_decision(self) -> None:
        raw = _review_decision(2, state="CHANGES_REQUESTED", body="Please fix")
        c = _map_review_decision(raw)
        assert c.author == "approver-2"
        assert c.body == "[CHANGES_REQUESTED] Please fix"
        assert c.path is None
        assert c.line is None
        assert c.created_at == "2026-03-17T02:20:00Z"

    def test_map_review_decision_empty_body(self) -> None:
        raw = _review_decision(0, state="APPROVED", body="")
        # body is empty string, but _review_decision helper fills it if empty.
        raw["body"] = ""
        c = _map_review_decision(raw)
        assert c.body == "[APPROVED] "

    def test_map_review_decision_missing_user(self) -> None:
        raw = {"id": 999, "body": "ok", "state": "APPROVED", "submitted_at": "2026-01-01T00:00:00Z"}
        c = _map_review_decision(raw)
        assert c.author == ""


# ---------------------------------------------------------------------------
# fetch_pr_comments
# ---------------------------------------------------------------------------


def _mock_subprocess_run(responses: dict[str, str]):
    """Create a side_effect function for subprocess.run that maps endpoint to output."""
    def side_effect(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        endpoint = cmd[3]  # ["gh", "api", "--paginate", endpoint]
        if endpoint in responses:
            return subprocess.CompletedProcess(cmd, 0, stdout=responses[endpoint], stderr="")
        raise subprocess.CalledProcessError(1, cmd, stderr="not found")
    return side_effect


class TestFetchPrComments:
    def test_zero_comments(self, tmp_path: Path) -> None:
        responses = {
            "repos/owner/repo/pulls/1/comments": "[]",
            "repos/owner/repo/issues/1/comments": "[]",
            "repos/owner/repo/pulls/1/reviews": "[]",
        }
        with patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=_mock_subprocess_run(responses)):
            result = fetch_pr_comments("owner", "repo", 1, tmp_path)
        assert result == []

    def test_five_comments_per_type(self, tmp_path: Path) -> None:
        responses = {
            "repos/o/r/pulls/42/comments": FIXTURE_REVIEW_COMMENTS_5,
            "repos/o/r/issues/42/comments": FIXTURE_ISSUE_COMMENTS_5,
            "repos/o/r/pulls/42/reviews": FIXTURE_REVIEWS_5,
        }
        with patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=_mock_subprocess_run(responses)):
            result = fetch_pr_comments("o", "r", 42, tmp_path)
        assert len(result) == 15
        # All should be PRComment instances.
        for c in result:
            assert c.id.startswith("comment-")
            assert c.platform_id != ""

    def test_fifty_plus_comments_paginated(self, tmp_path: Path) -> None:
        responses = {
            "repos/o/r/pulls/10/comments": FIXTURE_PAGINATED_50,
            "repos/o/r/issues/10/comments": "[]",
            "repos/o/r/pulls/10/reviews": "[]",
        }
        with patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=_mock_subprocess_run(responses)):
            result = fetch_pr_comments("o", "r", 10, tmp_path)
        assert len(result) == 50

    def test_gh_not_found(self, tmp_path: Path) -> None:
        with patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=FileNotFoundError("gh not found")):
            result = fetch_pr_comments("o", "r", 1, tmp_path)
        assert result == []

    def test_gh_nonzero_exit(self, tmp_path: Path) -> None:
        with patch(
            "agent_orchestrator.comments.reader.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["gh"], stderr="auth required"),
        ):
            result = fetch_pr_comments("o", "r", 1, tmp_path)
        assert result == []

    def test_gh_timeout(self, tmp_path: Path) -> None:
        with patch(
            "agent_orchestrator.comments.reader.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["gh"], 60),
        ):
            result = fetch_pr_comments("o", "r", 1, tmp_path)
        assert result == []

    def test_invalid_json_response(self, tmp_path: Path) -> None:
        responses = {
            "repos/o/r/pulls/1/comments": "not json",
            "repos/o/r/issues/1/comments": "not json",
            "repos/o/r/pulls/1/reviews": "not json",
        }
        with patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=_mock_subprocess_run(responses)):
            result = fetch_pr_comments("o", "r", 1, tmp_path)
        assert result == []

    def test_partial_failure(self, tmp_path: Path) -> None:
        """One endpoint fails, others succeed — returns comments from successful endpoints."""
        call_count = 0

        def side_effect(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal call_count
            call_count += 1
            endpoint = cmd[3]
            if "pulls" in endpoint and "comments" in endpoint:
                raise subprocess.CalledProcessError(1, cmd, stderr="error")
            if "issues" in endpoint:
                return subprocess.CompletedProcess(cmd, 0, stdout=FIXTURE_ISSUE_COMMENTS_5, stderr="")
            # reviews
            return subprocess.CompletedProcess(cmd, 0, stdout=FIXTURE_REVIEWS_5, stderr="")

        with patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=side_effect):
            result = fetch_pr_comments("o", "r", 1, tmp_path)
        assert len(result) == 10  # 5 issue + 5 review, 0 from failed endpoint

    def test_results_sorted_by_created_at(self, tmp_path: Path) -> None:
        """Verify chronological ordering across comment types."""
        responses = {
            "repos/o/r/pulls/1/comments": FIXTURE_REVIEW_COMMENTS_5,
            "repos/o/r/issues/1/comments": FIXTURE_ISSUE_COMMENTS_5,
            "repos/o/r/pulls/1/reviews": FIXTURE_REVIEWS_5,
        }
        with patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=_mock_subprocess_run(responses)):
            result = fetch_pr_comments("o", "r", 1, tmp_path)
        dates = [c.created_at for c in result]
        assert dates == sorted(dates)
