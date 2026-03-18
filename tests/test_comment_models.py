"""Unit tests for comment data models."""

from __future__ import annotations

from datetime import datetime

from agent_orchestrator.comments import (
    CommentPostResult,
    PRComment,
    ReviewDecision,
    ReviewDecisionType,
)


class TestPRComment:
    def test_defaults(self) -> None:
        c = PRComment()
        assert c.id.startswith("comment-")
        assert len(c.id) == len("comment-") + 10
        assert c.author == ""
        assert c.body == ""
        assert c.path is None
        assert c.line is None
        assert c.resolved is False
        assert c.in_reply_to is None
        assert c.platform_id == ""
        # created_at should be a valid ISO-8601 string
        datetime.fromisoformat(c.created_at)

    def test_explicit_construction(self) -> None:
        c = PRComment(
            id="comment-abc",
            author="alice",
            body="Looks good",
            path="src/main.py",
            line=42,
            created_at="2026-01-01T00:00:00+00:00",
            resolved=True,
            in_reply_to="comment-parent",
            platform_id="gh-123",
        )
        assert c.id == "comment-abc"
        assert c.author == "alice"
        assert c.body == "Looks good"
        assert c.path == "src/main.py"
        assert c.line == 42
        assert c.created_at == "2026-01-01T00:00:00+00:00"
        assert c.resolved is True
        assert c.in_reply_to == "comment-parent"
        assert c.platform_id == "gh-123"

    def test_to_dict_round_trip(self) -> None:
        c = PRComment(
            id="comment-test",
            author="bob",
            body="Fix this",
            path="file.py",
            line=10,
            created_at="2026-01-01T00:00:00+00:00",
            resolved=False,
            in_reply_to=None,
            platform_id="pl-1",
        )
        d = c.to_dict()
        assert d["id"] == "comment-test"
        assert d["author"] == "bob"
        assert d["body"] == "Fix this"
        assert d["path"] == "file.py"
        assert d["line"] == 10
        assert d["resolved"] is False
        assert d["in_reply_to"] is None
        assert d["platform_id"] == "pl-1"

    def test_from_dict_complete(self) -> None:
        data = {
            "id": "comment-xyz",
            "author": "carol",
            "body": "LGTM",
            "path": "lib.py",
            "line": 5,
            "created_at": "2026-02-01T12:00:00+00:00",
            "resolved": True,
            "in_reply_to": "comment-parent",
            "platform_id": "gh-456",
        }
        c = PRComment.from_dict(data)
        assert c.id == "comment-xyz"
        assert c.author == "carol"
        assert c.body == "LGTM"
        assert c.path == "lib.py"
        assert c.line == 5
        assert c.created_at == "2026-02-01T12:00:00+00:00"
        assert c.resolved is True
        assert c.in_reply_to == "comment-parent"
        assert c.platform_id == "gh-456"

    def test_from_dict_missing_keys(self) -> None:
        c = PRComment.from_dict({})
        assert c.id.startswith("comment-")
        assert c.author == ""
        assert c.body == ""
        assert c.path is None
        assert c.line is None
        assert c.resolved is False
        assert c.in_reply_to is None
        assert c.platform_id == ""
        datetime.fromisoformat(c.created_at)

    def test_from_dict_invalid_line(self) -> None:
        c = PRComment.from_dict({"line": "not_a_number"})
        assert c.line is None


class TestReviewDecision:
    def test_defaults(self) -> None:
        d = ReviewDecision()
        assert d.decision == "comment"
        assert d.body == ""

    def test_from_dict_valid(self) -> None:
        d = ReviewDecision.from_dict({"decision": "approve", "body": "Ship it"})
        assert d.decision == "approve"
        assert d.body == "Ship it"

    def test_from_dict_invalid_decision(self) -> None:
        d = ReviewDecision.from_dict({"decision": "reject"})
        assert d.decision == "comment"

    def test_to_dict(self) -> None:
        d = ReviewDecision(decision="request_changes", body="Needs work")
        result = d.to_dict()
        assert result == {"decision": "request_changes", "body": "Needs work"}


class TestCommentPostResult:
    def test_defaults(self) -> None:
        r = CommentPostResult()
        assert r.success is False
        assert r.platform_id == ""
        assert r.error is None

    def test_from_dict_round_trip(self) -> None:
        original = CommentPostResult(success=True, platform_id="gh-789", error=None)
        restored = CommentPostResult.from_dict(original.to_dict())
        assert restored.success is True
        assert restored.platform_id == "gh-789"
        assert restored.error is None

    def test_from_dict_coerces_success(self) -> None:
        r = CommentPostResult.from_dict({"success": 1})
        assert r.success is True

    def test_from_dict_with_error(self) -> None:
        r = CommentPostResult.from_dict({"success": False, "error": "timeout"})
        assert r.success is False
        assert r.error == "timeout"


def test_review_decision_type_literal() -> None:
    """Verify ReviewDecisionType is importable and usable."""
    values: list[ReviewDecisionType] = ["approve", "request_changes", "comment"]
    assert len(values) == 3
