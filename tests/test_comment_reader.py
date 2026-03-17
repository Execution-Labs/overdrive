"""Unit tests for the GitLab MR comment reader."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_orchestrator.comments import CommentFetchError, fetch_mr_comments

FIXTURES = Path(__file__).parent / "fixtures" / "gitlab_notes"


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _mock_run_ok(stdout: str):
    """Return a mock subprocess result with the given stdout."""

    def _side_effect(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")

    return _side_effect


class TestFetchMrCommentsBasic:
    def test_filters_system_notes_and_parses_fields(self) -> None:
        fixture = _load_fixture("mr_notes_page1.json")
        with (
            patch("agent_orchestrator.comments.reader.shutil.which", return_value="/usr/bin/glab"),
            patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=_mock_run_ok(fixture)),
        ):
            comments = fetch_mr_comments("123", 1)

        assert len(comments) == 2

        general = comments[0]
        assert general.platform_id == "101"
        assert general.author == "reviewer1"
        assert general.body == "Great work on this feature!"
        assert general.path is None
        assert general.line is None
        assert general.resolved is False

        inline = comments[1]
        assert inline.platform_id == "102"
        assert inline.path == "src/main.py"
        assert inline.line == 42
        assert inline.resolved is True


class TestFetchMrCommentsInlineFallback:
    def test_falls_back_to_old_path_and_old_line(self) -> None:
        note = json.dumps([
            {
                "id": 301,
                "body": "Old-side comment",
                "author": {"username": "dev"},
                "created_at": "2026-03-10T09:00:00.000Z",
                "system": False,
                "resolved": False,
                "position": {
                    "new_path": None,
                    "old_path": "old.py",
                    "new_line": None,
                    "old_line": 15,
                },
            }
        ])
        with (
            patch("agent_orchestrator.comments.reader.shutil.which", return_value="/usr/bin/glab"),
            patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=_mock_run_ok(note)),
        ):
            comments = fetch_mr_comments("123", 1)

        assert len(comments) == 1
        assert comments[0].path == "old.py"
        assert comments[0].line == 15


class TestFetchMrCommentsPagination:
    def test_handles_newline_separated_json_arrays(self) -> None:
        page1 = _load_fixture("mr_notes_page1.json").strip()
        page2 = _load_fixture("mr_notes_page2.json").strip()
        paginated = page1 + "\n" + page2
        with (
            patch("agent_orchestrator.comments.reader.shutil.which", return_value="/usr/bin/glab"),
            patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=_mock_run_ok(paginated)),
        ):
            comments = fetch_mr_comments("123", 1)

        # 2 user notes from page1 (system note filtered) + 1 from page2
        assert len(comments) == 3
        assert comments[-1].platform_id == "201"


class TestFetchMrCommentsEmpty:
    def test_empty_notes_returns_empty_list(self) -> None:
        fixture = _load_fixture("mr_notes_empty.json")
        with (
            patch("agent_orchestrator.comments.reader.shutil.which", return_value="/usr/bin/glab"),
            patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=_mock_run_ok(fixture)),
        ):
            comments = fetch_mr_comments("123", 1)

        assert comments == []


class TestFetchMrCommentsGlabNotInstalled:
    def test_raises_when_glab_missing(self) -> None:
        with patch("agent_orchestrator.comments.reader.shutil.which", return_value=None):
            with pytest.raises(CommentFetchError, match="not installed"):
                fetch_mr_comments("123", 1)


class TestFetchMrCommentsGlabError:
    def test_raises_on_subprocess_error(self) -> None:
        with (
            patch("agent_orchestrator.comments.reader.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "agent_orchestrator.comments.reader.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "glab", stderr="not found"),
            ),
        ):
            with pytest.raises(CommentFetchError, match="glab api failed"):
                fetch_mr_comments("123", 1)


class TestFetchMrCommentsTimeout:
    def test_raises_on_timeout(self) -> None:
        with (
            patch("agent_orchestrator.comments.reader.shutil.which", return_value="/usr/bin/glab"),
            patch(
                "agent_orchestrator.comments.reader.subprocess.run",
                side_effect=subprocess.TimeoutExpired("glab", 60),
            ),
        ):
            with pytest.raises(CommentFetchError, match="timed out"):
                fetch_mr_comments("123", 1)


class TestFetchMrCommentsInvalidJson:
    def test_raises_on_bad_json(self) -> None:
        with (
            patch("agent_orchestrator.comments.reader.shutil.which", return_value="/usr/bin/glab"),
            patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=_mock_run_ok("not json {{")),
        ):
            with pytest.raises(CommentFetchError, match="Failed to parse"):
                fetch_mr_comments("123", 1)


class TestFetchMrCommentsMalformedNote:
    def test_skips_malformed_notes_with_warning(self) -> None:
        notes = json.dumps([
            {
                "id": 401,
                "body": "Valid note",
                "author": {"username": "dev"},
                "created_at": "2026-03-10T09:00:00.000Z",
                "system": False,
                "resolved": False,
                "position": None,
            },
            {
                "id": 402,
                "body": "Missing author field",
                "created_at": "2026-03-10T10:00:00.000Z",
                "system": False,
                "resolved": False,
                "position": None,
            },
        ])
        with (
            patch("agent_orchestrator.comments.reader.shutil.which", return_value="/usr/bin/glab"),
            patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=_mock_run_ok(notes)),
        ):
            comments = fetch_mr_comments("123", 1)

        assert len(comments) == 1
        assert comments[0].platform_id == "401"


class TestFetchMrCommentsResolvedNull:
    def test_resolved_null_coerced_to_false(self) -> None:
        """GitLab returns ``"resolved": null`` for non-resolvable notes."""
        notes = json.dumps([
            {
                "id": 601,
                "body": "General comment",
                "author": {"username": "dev"},
                "created_at": "2026-03-10T09:00:00.000Z",
                "system": False,
                "resolved": None,
                "position": None,
            }
        ])
        with (
            patch("agent_orchestrator.comments.reader.shutil.which", return_value="/usr/bin/glab"),
            patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=_mock_run_ok(notes)),
        ):
            comments = fetch_mr_comments("123", 1)

        assert len(comments) == 1
        assert comments[0].resolved is False


class TestFetchMrCommentsSortedByDate:
    def test_results_sorted_ascending(self) -> None:
        notes = json.dumps([
            {
                "id": 502,
                "body": "Second",
                "author": {"username": "b"},
                "created_at": "2026-03-10T12:00:00.000Z",
                "system": False,
                "resolved": False,
                "position": None,
            },
            {
                "id": 501,
                "body": "First",
                "author": {"username": "a"},
                "created_at": "2026-03-10T08:00:00.000Z",
                "system": False,
                "resolved": False,
                "position": None,
            },
        ])
        with (
            patch("agent_orchestrator.comments.reader.shutil.which", return_value="/usr/bin/glab"),
            patch("agent_orchestrator.comments.reader.subprocess.run", side_effect=_mock_run_ok(notes)),
        ):
            comments = fetch_mr_comments("123", 1)

        assert comments[0].platform_id == "501"
        assert comments[1].platform_id == "502"
