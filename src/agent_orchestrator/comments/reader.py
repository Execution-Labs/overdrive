"""GitLab MR comment reader via glab CLI."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from .models import PRComment

logger = logging.getLogger(__name__)


class CommentFetchError(Exception):
    """Raised when fetching comments from a platform fails."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def fetch_mr_comments(
    project_id: str | int,
    mr_number: int,
    *,
    cwd: Path | None = None,
) -> list[PRComment]:
    """Fetch all user comments from a GitLab merge request.

    Uses ``glab api --paginate`` to retrieve notes and parses them into
    the unified :class:`PRComment` format.  System-generated notes are
    filtered out.

    Args:
        project_id: GitLab project ID (numeric or URL-encoded path).
        mr_number: Merge request IID.
        cwd: Working directory for the ``glab`` subprocess.

    Returns:
        List of parsed comments sorted by ``created_at`` ascending.

    Raises:
        CommentFetchError: If ``glab`` is missing, the API call fails,
            times out, or returns unparseable output.
    """
    if shutil.which("glab") is None:
        raise CommentFetchError("glab CLI is not installed")

    cmd = [
        "glab",
        "api",
        "--paginate",
        f"projects/{project_id}/merge_requests/{mr_number}/notes",
        "-X",
        "GET",
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        raise CommentFetchError(f"glab api failed: {exc.stderr}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommentFetchError("glab api timed out") from exc

    notes = _parse_paginated_json(result.stdout)

    comments: list[PRComment] = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        if note.get("system", False):
            continue
        try:
            comments.append(_parse_note(note))
        except (KeyError, TypeError) as exc:
            logger.warning("Skipping malformed note %s: %s", note.get("id"), exc)

    comments.sort(key=lambda c: c.created_at)
    return comments


def _parse_paginated_json(raw: str) -> list[dict]:  # type: ignore[type-arg]
    """Parse ``glab api --paginate`` output into a flat list.

    ``--paginate`` may return a single JSON array or multiple
    newline-separated JSON arrays.  This helper handles both forms.

    Args:
        raw: Raw stdout from ``glab api``.

    Returns:
        Flat list of note dicts.

    Raises:
        CommentFetchError: If the output is not valid JSON.
    """
    raw = raw.strip()
    if not raw:
        return []

    # Fast path: single valid JSON array.
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # Slow path: newline-separated JSON arrays from --paginate.
    # glab concatenates pages as e.g. "[...]\n[...]", so split on the
    # boundary between a closing and opening bracket.
    fragments = re.split(r"\]\s*\n\s*\[", raw)
    merged: list[dict] = []  # type: ignore[type-arg]
    for i, fragment in enumerate(fragments):
        # Re-add brackets stripped by the split.
        if i == 0:
            fragment = fragment if fragment.rstrip().endswith("]") else fragment + "]"
        elif i == len(fragments) - 1:
            fragment = "[" + fragment if not fragment.lstrip().startswith("[") else fragment
        else:
            fragment = "[" + fragment + "]"
        try:
            part = json.loads(fragment)
            if isinstance(part, list):
                merged.extend(part)
            else:
                merged.append(part)
        except json.JSONDecodeError:
            raise CommentFetchError("Failed to parse glab api response")
    return merged


def _parse_note(note: dict) -> PRComment:  # type: ignore[type-arg]
    """Convert a single GitLab note dict into a :class:`PRComment`.

    Args:
        note: Raw note object from the GitLab API.

    Returns:
        Parsed comment.

    Raises:
        KeyError: If required fields are missing.
        TypeError: If field types are unexpected.
    """
    position = note.get("position")
    path: str | None = None
    line: int | None = None

    if isinstance(position, dict):
        path = position.get("new_path") or position.get("old_path")
        raw_line = position.get("new_line")
        if raw_line is None:
            raw_line = position.get("old_line")
        if raw_line is not None:
            line = int(raw_line)

    return PRComment(
        platform_id=str(note["id"]),
        author=note["author"]["username"],
        body=note["body"],
        created_at=note["created_at"],
        resolved=bool(note.get("resolved")),
        path=path,
        line=line,
        in_reply_to=None,
    )
