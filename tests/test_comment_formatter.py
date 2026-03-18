"""Tests for the comment context formatter."""

from __future__ import annotations

from agent_orchestrator.comments.formatter import format_comments_for_prompt
from agent_orchestrator.comments.models import PRComment


def _make_comment(
    *,
    author: str = "alice",
    body: str = "Fix this",
    path: str | None = None,
    line: int | None = None,
    created_at: str = "2026-01-15T10:00:00Z",
    resolved: bool = False,
    comment_id: str = "c1",
) -> PRComment:
    return PRComment(
        id=comment_id,
        author=author,
        body=body,
        path=path,
        line=line,
        created_at=created_at,
        resolved=resolved,
    )


class TestEmptyAndSingle:
    def test_empty_list(self) -> None:
        assert format_comments_for_prompt([]) == ""

    def test_single_general_comment(self) -> None:
        c = _make_comment(body="Please update docs")
        result = format_comments_for_prompt([c])
        assert "## General" in result
        assert "[@alice, 2026-01-15]" in result
        assert "(unresolved)" in result
        assert "Please update docs" in result

    def test_single_inline_comment(self) -> None:
        c = _make_comment(path="src/main.py", line=42, body="Off-by-one here")
        result = format_comments_for_prompt([c])
        assert "## src/main.py" in result
        assert "L42: Off-by-one here" in result
        assert "[@alice, 2026-01-15]" in result


class TestOrdering:
    def test_unresolved_before_resolved(self) -> None:
        resolved = _make_comment(
            comment_id="r1",
            body="resolved comment",
            resolved=True,
            created_at="2026-01-20T10:00:00Z",
        )
        unresolved = _make_comment(
            comment_id="u1",
            body="unresolved comment",
            resolved=False,
            created_at="2026-01-10T10:00:00Z",
        )
        result = format_comments_for_prompt([resolved, unresolved])
        pos_unresolved = result.index("unresolved comment")
        pos_resolved = result.index("resolved comment")
        assert pos_unresolved < pos_resolved

    def test_recent_first_within_group(self) -> None:
        old = _make_comment(
            comment_id="o1",
            body="old comment",
            created_at="2026-01-01T10:00:00Z",
        )
        new = _make_comment(
            comment_id="n1",
            body="new comment",
            created_at="2026-01-20T10:00:00Z",
        )
        result = format_comments_for_prompt([old, new])
        assert result.index("new comment") < result.index("old comment")


class TestGrouping:
    def test_grouped_by_file(self) -> None:
        c1 = _make_comment(comment_id="c1", path="a.py", line=1, body="Comment A")
        c2 = _make_comment(comment_id="c2", path="b.py", line=2, body="Comment B")
        result = format_comments_for_prompt([c1, c2])
        assert "## a.py" in result
        assert "## b.py" in result

    def test_no_inline_comments(self) -> None:
        comments = [
            _make_comment(comment_id=f"c{i}", body=f"General {i}")
            for i in range(3)
        ]
        result = format_comments_for_prompt(comments)
        assert "## General" in result
        # No file path headers.
        assert result.count("## ") == 1

    def test_all_resolved(self) -> None:
        comments = [
            _make_comment(comment_id=f"c{i}", body=f"Done {i}", resolved=True)
            for i in range(3)
        ]
        result = format_comments_for_prompt(comments)
        assert result.count("(resolved)") == 3
        assert "(unresolved)" not in result


class TestScaling:
    def test_10_comments(self) -> None:
        comments = [
            _make_comment(
                comment_id=f"c{i}",
                author="alice" if i % 2 == 0 else "bob",
                body=f"Comment number {i}",
                path=f"file{i % 3}.py" if i % 2 == 0 else None,
                line=i * 10 if i % 2 == 0 else None,
                created_at=f"2026-01-{15 + i:02d}T10:00:00Z",
                resolved=i < 3,
            )
            for i in range(10)
        ]
        result = format_comments_for_prompt(comments)
        assert len(result) <= 8000
        assert len(result) > 0
        # Unresolved (i>=3) should appear before resolved (i<3).
        for i in range(3, 10):
            assert f"Comment number {i}" in result

    def test_100_comments_within_budget(self) -> None:
        comments = [
            _make_comment(
                comment_id=f"c{i}",
                body=f"Short {i}",
                created_at=f"2026-01-{(i % 28) + 1:02d}T10:00:00Z",
            )
            for i in range(100)
        ]
        result = format_comments_for_prompt(comments)
        assert len(result) <= 8000


class TestTruncation:
    def test_truncation_drops_lowest_priority(self) -> None:
        # Create comments where total exceeds budget.
        comments = []
        for i in range(50):
            comments.append(
                _make_comment(
                    comment_id=f"u{i}",
                    body="U" * 100,
                    resolved=False,
                    created_at=f"2026-02-{(i % 28) + 1:02d}T10:00:00Z",
                )
            )
        for i in range(50):
            comments.append(
                _make_comment(
                    comment_id=f"r{i}",
                    body="R" * 100,
                    resolved=True,
                    created_at=f"2026-01-{(i % 28) + 1:02d}T10:00:00Z",
                )
            )
        result = format_comments_for_prompt(comments, max_chars=4000)
        assert len(result) <= 4000
        # Resolved comments should be dropped first.
        u_count = result.count("U" * 100)
        r_count = result.count("R" * 100)
        assert u_count >= r_count

    def test_truncation_notice(self) -> None:
        comments = [
            _make_comment(
                comment_id=f"c{i}",
                body="x" * 200,
                created_at=f"2026-01-{(i % 28) + 1:02d}T10:00:00Z",
            )
            for i in range(100)
        ]
        result = format_comments_for_prompt(comments, max_chars=2000)
        assert len(result) <= 2000
        assert "more comments truncated]" in result

    def test_truncation_boundary(self) -> None:
        # Two comments; set budget so only one fits plus truncation notice.
        c1 = _make_comment(comment_id="c1", body="First comment body here")
        c2 = _make_comment(comment_id="c2", body="Second comment body " + "x" * 100)
        # Render both and pick a budget between one and two comments.
        full = format_comments_for_prompt([c1, c2])
        one_only = format_comments_for_prompt([c1])
        # Budget large enough for one comment + notice, but not two full comments.
        budget = len(one_only) + 60
        assert budget < len(full), "budget must be less than full output"
        result = format_comments_for_prompt([c1, c2], max_chars=budget)
        assert len(result) <= budget
        assert "First comment body here" in result
        assert "more comments truncated]" in result

    def test_custom_max_chars(self) -> None:
        comments = [
            _make_comment(
                comment_id=f"c{i}",
                body="word " * 20,
                created_at=f"2026-01-{(i % 28) + 1:02d}T10:00:00Z",
            )
            for i in range(50)
        ]
        result = format_comments_for_prompt(comments, max_chars=500)
        assert len(result) <= 500

    def test_very_long_single_comment(self) -> None:
        c = _make_comment(body="A" * 20000)
        result = format_comments_for_prompt([c], max_chars=500)
        assert len(result) <= 500
        assert "…" in result


class TestImport:
    def test_import_from_package(self) -> None:
        from agent_orchestrator.comments import format_comments_for_prompt as fn

        assert callable(fn)
