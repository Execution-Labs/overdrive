"""Tests for ReviewMode/ReviewDecisionType domain types and request validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_orchestrator.runtime.api.routes_tasks import CreatePullRequestReviewRequest
from agent_orchestrator.runtime.domain.models import (
    ReviewDecisionType,
    ReviewMode,
    _VALID_REVIEW_DECISION_TYPES,
    _VALID_REVIEW_MODES,
)


class TestDomainTypes:
    """Verify Literal type sets match their companion validation sets."""

    def test_valid_review_modes_match_literal(self) -> None:
        assert _VALID_REVIEW_MODES == {"review_comment", "summarize", "fix_only", "fix_respond"}

    def test_valid_review_decision_types_match_literal(self) -> None:
        assert _VALID_REVIEW_DECISION_TYPES == {"approve", "request_changes", "comment"}


class TestCreatePullRequestReviewRequestDefaults:
    """Default values for the simplified creation request."""

    def test_defaults(self) -> None:
        req = CreatePullRequestReviewRequest()
        assert req.guidance == ""
        assert req.review_mode == "fix_only"

    def test_with_guidance(self) -> None:
        req = CreatePullRequestReviewRequest(guidance="please review carefully")
        assert req.guidance == "please review carefully"
        assert req.review_mode == "fix_only"


class TestValidModes:
    """All review modes are accepted (review_decision moved to gate)."""

    @pytest.mark.parametrize("mode", ["review_comment", "summarize", "fix_only", "fix_respond"])
    def test_valid_mode(self, mode: str) -> None:
        req = CreatePullRequestReviewRequest(review_mode=mode)
        assert req.review_mode == mode


class TestInvalidValues:
    """Invalid enum values are rejected by Pydantic Literal validation."""

    def test_invalid_review_mode(self) -> None:
        with pytest.raises(ValidationError):
            CreatePullRequestReviewRequest(review_mode="invalid")

    def test_unknown_fields_ignored(self) -> None:
        """review_decision is no longer a field — extra fields are silently ignored."""
        req = CreatePullRequestReviewRequest(
            review_mode="review_comment",
            review_decision="approve",  # type: ignore[call-arg]
        )
        assert req.review_mode == "review_comment"
        assert not hasattr(req, "review_decision")
