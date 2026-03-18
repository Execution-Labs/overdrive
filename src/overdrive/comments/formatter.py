"""Format PR/MR comments into compact text for worker prompt injection."""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from .models import PRComment

_TRUNCATION_NOTICE_TEMPLATE = "\n\n[… {} more comments truncated]"
# Reserve space for the longest plausible truncation notice.
_TRUNCATION_NOTICE_RESERVE = len(_TRUNCATION_NOTICE_TEMPLATE.format("9999"))


def _date_prefix(created_at: str) -> str:
    """Extract YYYY-MM-DD from an ISO-8601 datetime string."""
    return created_at[:10] if len(created_at) >= 10 else created_at


def _render_comment(comment: PRComment) -> str:
    """Render a single comment as a compact text block.

    Args:
        comment: The PR comment to render.

    Returns:
        Rendered comment string (no trailing newline).
    """
    status = "resolved" if comment.resolved else "unresolved"
    header = f"[@{comment.author}, {_date_prefix(comment.created_at)}] ({status})"
    if comment.line is not None:
        body = f"L{comment.line}: {comment.body}"
    else:
        body = comment.body
    return f"{header}\n{body}"


def _sort_key(comment: PRComment) -> tuple[int, str]:
    """Sort key: unresolved first (0), then resolved (1); within each, most recent first."""
    return (
        0 if not comment.resolved else 1,
        # Negate recency by reversing the string sort — ISO-8601 sorts lexicographically,
        # so we invert each character's ordinal relative to '~' (ASCII 126).
        "".join(chr(126 - ord(c)) for c in comment.created_at),
    )


def format_comments_for_prompt(
    comments: Sequence[PRComment],
    *,
    max_chars: int = 8000,
) -> str:
    """Convert PR comments into compact text for injection into worker prompts.

    Comments are prioritized: unresolved before resolved, most recent first
    within each group. Inline comments (with ``path``) are grouped by file
    path; general comments appear in their own section.

    The output is truncated to *max_chars* characters. When truncation occurs,
    the lowest-priority comments (resolved, oldest) are dropped first, and a
    notice is appended.

    Args:
        comments: List of PR comments to format.
        max_chars: Maximum character budget for the returned string.

    Returns:
        Formatted string ready for prompt injection, or ``""`` if *comments*
        is empty.
    """
    if not comments:
        return ""

    sorted_comments = sorted(comments, key=_sort_key)

    # Partition into inline (has path) and general (no path).
    inline: list[PRComment] = []
    general: list[PRComment] = []
    for c in sorted_comments:
        if c.path:
            inline.append(c)
        else:
            general.append(c)

    # Build ordered sections: list of (section_text, comment) pairs.
    # Each section_text is the full renderable text for that comment
    # including any file header that needs to precede it.
    entries: list[tuple[str, PRComment]] = []

    # Group inline comments by file path, preserving priority order.
    if inline:
        by_file: dict[str, list[PRComment]] = defaultdict(list)
        for c in inline:
            assert c.path is not None
            by_file[c.path].append(c)

        # Emit file groups in the order of their highest-priority comment.
        seen_files: set[str] = set()
        for c in inline:
            assert c.path is not None
            path = c.path
            if path in seen_files:
                continue
            seen_files.add(path)
            group = by_file[path]
            for i, gc in enumerate(group):
                rendered = _render_comment(gc)
                if i == 0:
                    # Prepend file header to the first comment in the group.
                    rendered = f"## {path}\n{rendered}"
                entries.append((rendered, gc))

    # General comments section.
    if general:
        for i, gc in enumerate(general):
            rendered = _render_comment(gc)
            if i == 0:
                rendered = f"## General\n{rendered}"
            entries.append((rendered, gc))

    # Assemble with truncation.
    parts: list[str] = []
    used = 0
    total = len(entries)

    for idx, (text, _comment) in enumerate(entries):
        remaining_entries = total - idx - 1
        # Separator between entries.
        sep = "\n\n" if parts else ""
        candidate = sep + text
        candidate_len = len(candidate)

        # Check if we need to reserve space for a truncation notice.
        if remaining_entries > 0:
            headroom = max_chars - _TRUNCATION_NOTICE_RESERVE
        else:
            headroom = max_chars

        if used + candidate_len <= headroom:
            parts.append(candidate)
            used += candidate_len
        else:
            # Try to fit a truncated version if this is the first entry
            # (i.e., a single oversized comment).
            if not parts:
                avail = max_chars - _TRUNCATION_NOTICE_RESERVE - len(sep)
                if avail > 0:
                    truncated_text = _truncate_entry(text, avail)
                    parts.append(sep + truncated_text)
                    used = len(parts[-1])
            break

    # Each part corresponds to one entry.
    dropped = total - len(parts)

    result = "".join(parts)
    if dropped > 0:
        result += _TRUNCATION_NOTICE_TEMPLATE.format(dropped)

    # Final safety: hard-truncate if somehow over budget (shouldn't happen).
    if len(result) > max_chars:
        result = result[: max_chars - 3] + "…"

    return result


def _truncate_entry(text: str, max_len: int) -> str:
    """Truncate a rendered entry to fit within *max_len* characters."""
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return "…"
    return text[: max_len - 1] + "…"
