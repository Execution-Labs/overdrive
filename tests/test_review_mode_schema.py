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
    """Default values preserve backward compatibility."""

    def test_defaults(self) -> None:
        req = CreatePullRequestReviewRequest()
        assert req.guidance == ""
        assert req.review_mode == "fix_only"
        assert req.review_decision is None

    def test_backward_compat_guidance_only(self) -> None:
        req = CreatePullRequestReviewRequest(guidance="please review carefully")
        assert req.guidance == "please review carefully"
        assert req.review_mode == "fix_only"
        assert req.review_decision is None


class TestValidModes:
    """All review modes accept without review_decision."""

    @pytest.mark.parametrize("mode", ["review_comment", "summarize", "fix_only", "fix_respond"])
    def test_valid_mode_no_decision(self, mode: str) -> None:
        req = CreatePullRequestReviewRequest(review_mode=mode)
        assert req.review_mode == mode
        assert req.review_decision is None


class TestReviewCommentWithDecision:
    """review_comment mode accepts all valid decision types."""

    @pytest.mark.parametrize("decision", ["approve", "request_changes", "comment"])
    def test_review_comment_with_decision(self, decision: str) -> None:
        req = CreatePullRequestReviewRequest(review_mode="review_comment", review_decision=decision)
        assert req.review_mode == "review_comment"
        assert req.review_decision == decision


class TestDecisionRejectedForNonReviewComment:
    """review_decision is rejected when mode is not review_comment."""

    @pytest.mark.parametrize("mode", ["summarize", "fix_only", "fix_respond"])
    def test_decision_rejected(self, mode: str) -> None:
        with pytest.raises(ValidationError, match="review_decision is only valid when review_mode is 'review_comment'"):
            CreatePullRequestReviewRequest(review_mode=mode, review_decision="approve")


class TestInvalidValues:
    """Invalid enum values are rejected by Pydantic Literal validation."""

    def test_invalid_review_mode(self) -> None:
        with pytest.raises(ValidationError):
            CreatePullRequestReviewRequest(review_mode="invalid")

    def test_invalid_review_decision(self) -> None:
        with pytest.raises(ValidationError):
            CreatePullRequestReviewRequest(review_mode="review_comment", review_decision="reject")
