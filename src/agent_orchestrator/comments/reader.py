"""Fetch PR comments from GitHub via the ``gh`` CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger

from .models import PRComment, _comment_id


def _run_gh_api(endpoint: str, git_dir: Path) -> str | None:
    """Run ``gh api --paginate`` for *endpoint* and return raw stdout.

    Args:
        endpoint: GitHub REST API path (e.g. ``repos/o/r/pulls/1/comments``).
        git_dir: Working directory so ``gh`` resolves the correct repo context.

    Returns:
        Raw stdout on success, ``None`` on any error.
    """
    try:
        result = subprocess.run(
            ["gh", "api", "--paginate", endpoint],
            cwd=git_dir,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        return result.stdout
    except subprocess.CalledProcessError as exc:
        logger.warning("gh api {} failed (exit {}): {}", endpoint, exc.returncode, exc.stderr.strip())
    except subprocess.TimeoutExpired:
        logger.warning("gh api {} timed out", endpoint)
    except OSError as exc:
        logger.warning("gh api {} OS error: {}", endpoint, exc)
    return None


def _parse_paginated_json(raw: str) -> list[dict[str, Any]]:
    """Parse ``gh api --paginate`` output into a merged list of objects.

    ``--paginate`` concatenates JSON arrays per page (e.g. ``[...][...]``).
    This function handles both single-page and multi-page output.

    Args:
        raw: Raw stdout from ``gh api --paginate``.

    Returns:
        Merged list of JSON objects, or ``[]`` on parse failure.
    """
    raw = raw.strip()
    if not raw:
        return []

    # Fast path: single page — standard JSON array.
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        return []
    except json.JSONDecodeError:
        pass

    # Slow path: concatenated arrays — use streaming decoder.
    decoder = json.JSONDecoder()
    items: list[dict[str, Any]] = []
    idx = 0
    while idx < len(raw):
        # Skip whitespace between arrays.
        while idx < len(raw) and raw[idx] in " \t\n\r":
            idx += 1
        if idx >= len(raw):
            break
        try:
            obj, end = decoder.raw_decode(raw, idx)
            if isinstance(obj, list):
                items.extend(obj)
            idx = end
        except json.JSONDecodeError:
            logger.warning("Failed to parse paginated JSON at offset {}", idx)
            return items if items else []
    return items


def _map_review_comment(raw: dict[str, Any]) -> PRComment:
    """Map a GitHub pull-request review comment to :class:`PRComment`.

    Args:
        raw: A single object from ``pulls/{n}/comments``.

    Returns:
        Mapped :class:`PRComment`.
    """
    user = raw.get("user") or {}
    line = raw.get("line") if raw.get("line") is not None else raw.get("original_line")
    in_reply_to = raw.get("in_reply_to_id")
    return PRComment(
        id=_comment_id(),
        author=str(user.get("login") or ""),
        body=str(raw.get("body") or ""),
        path=str(raw["path"]) if raw.get("path") is not None else None,
        line=int(line) if line is not None else None,
        created_at=str(raw.get("created_at") or ""),
        in_reply_to=str(in_reply_to) if in_reply_to is not None else None,
        platform_id=str(raw.get("id") or ""),
    )


def _map_issue_comment(raw: dict[str, Any]) -> PRComment:
    """Map a GitHub issue comment to :class:`PRComment`.

    Issue comments are conversation-level and have no file/line context.

    Args:
        raw: A single object from ``issues/{n}/comments``.

    Returns:
        Mapped :class:`PRComment`.
    """
    user = raw.get("user") or {}
    return PRComment(
        id=_comment_id(),
        author=str(user.get("login") or ""),
        body=str(raw.get("body") or ""),
        path=None,
        line=None,
        created_at=str(raw.get("created_at") or ""),
        platform_id=str(raw.get("id") or ""),
    )


def _map_review_decision(raw: dict[str, Any]) -> PRComment:
    """Map a GitHub review decision to :class:`PRComment`.

    Review decisions include approvals, change requests, and general comments.
    The ``state`` field is prefixed to the body for downstream visibility.

    Args:
        raw: A single object from ``pulls/{n}/reviews``.

    Returns:
        Mapped :class:`PRComment`.
    """
    user = raw.get("user") or {}
    state = str(raw.get("state") or "").upper()
    body = str(raw.get("body") or "")
    combined_body = f"[{state}] {body}" if state else body
    return PRComment(
        id=_comment_id(),
        author=str(user.get("login") or ""),
        body=combined_body,
        path=None,
        line=None,
        created_at=str(raw.get("submitted_at") or ""),
        platform_id=str(raw.get("id") or ""),
    )


def fetch_pr_comments(
    owner: str,
    repo: str,
    pr_number: int,
    git_dir: Path,
) -> list[PRComment]:
    """Fetch all PR comments from GitHub via ``gh api``.

    Queries three endpoints (review comments, issue comments, review decisions),
    merges results into a single list sorted by ``created_at``.

    Args:
        owner: Repository owner (user or organization).
        repo: Repository name.
        pr_number: Pull request number.
        git_dir: Local git directory for ``gh`` CLI context.

    Returns:
        Unified list of :class:`PRComment` sorted chronologically.
        Returns ``[]`` if all endpoints fail.
    """
    base = f"repos/{owner}/{repo}"
    endpoints = [
        (f"{base}/pulls/{pr_number}/comments", _map_review_comment),
        (f"{base}/issues/{pr_number}/comments", _map_issue_comment),
        (f"{base}/pulls/{pr_number}/reviews", _map_review_decision),
    ]

    comments: list[PRComment] = []
    for endpoint, mapper in endpoints:
        raw = _run_gh_api(endpoint, git_dir)
        if raw is None:
            continue
        items = _parse_paginated_json(raw)
        for item in items:
            comments.append(mapper(item))

    comments.sort(key=lambda c: c.created_at)
    return comments
