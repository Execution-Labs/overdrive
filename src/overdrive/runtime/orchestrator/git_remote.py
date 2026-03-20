"""Git remote helpers: branch status, ahead/behind counts, and push operations."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...workers.config import WorkerProviderSpec

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 30


@dataclass
class CommitInfo:
    """Abbreviated commit metadata for ahead-of-remote commits."""

    sha: str
    message: str


@dataclass
class BranchStatus:
    """Current branch tracking state relative to its remote upstream."""

    branch: str
    remote_branch: str | None
    ahead_count: int
    behind_count: int
    commits: list[CommitInfo] = field(default_factory=list)
    has_remote: bool = False


@dataclass
class PushResult:
    """Outcome of a push operation."""

    success: bool
    error: str | None
    remote_branch: str
    pushed_commits: int


def _run_git(
    args: list[str],
    cwd: Path,
    *,
    timeout: int = _SUBPROCESS_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the completed process."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def get_branch_status(project_dir: Path) -> BranchStatus:
    """Return current branch info, remote tracking state, and ahead/behind counts.

    Args:
        project_dir: Root of the git repository.

    Returns:
        BranchStatus with branch name, upstream info, and commit list.
    """
    # Current branch name
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=project_dir)
    if result.returncode != 0:
        return BranchStatus(
            branch="HEAD",
            remote_branch=None,
            ahead_count=0,
            behind_count=0,
            has_remote=False,
        )
    branch = result.stdout.strip()

    # Check if origin remote exists
    remote_result = _run_git(["remote"], cwd=project_dir)
    remotes = [r.strip() for r in remote_result.stdout.splitlines() if r.strip()]
    has_remote = "origin" in remotes

    if has_remote:
        fetch_result = _run_git(["fetch", "origin"], cwd=project_dir, timeout=_SUBPROCESS_TIMEOUT)
        if fetch_result.returncode != 0:
            logger.warning("git fetch origin failed: %s", fetch_result.stderr.strip())

    # Upstream tracking branch
    upstream_result = _run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd=project_dir,
    )
    remote_branch: str | None = None
    if upstream_result.returncode == 0:
        remote_branch = upstream_result.stdout.strip() or None

    ahead_count = 0
    behind_count = 0
    commits: list[CommitInfo] = []

    if remote_branch:
        # Ahead count
        ahead_result = _run_git(
            ["rev-list", "--count", f"@{{upstream}}..HEAD"],
            cwd=project_dir,
        )
        if ahead_result.returncode == 0:
            try:
                ahead_count = int(ahead_result.stdout.strip())
            except ValueError:
                pass

        # Behind count
        behind_result = _run_git(
            ["rev-list", "--count", f"HEAD..@{{upstream}}"],
            cwd=project_dir,
        )
        if behind_result.returncode == 0:
            try:
                behind_count = int(behind_result.stdout.strip())
            except ValueError:
                pass

        # Ahead commit details
        if ahead_count > 0:
            log_result = _run_git(
                ["log", "--oneline", "--format=%H %s", f"@{{upstream}}..HEAD"],
                cwd=project_dir,
            )
            if log_result.returncode == 0:
                for line in log_result.stdout.strip().splitlines():
                    parts = line.split(" ", 1)
                    if len(parts) == 2:
                        commits.append(CommitInfo(sha=parts[0], message=parts[1]))
    elif has_remote:
        # No upstream — count all commits on current branch not reachable from
        # any remote tracking ref as the "ahead" set.
        count_result = _run_git(
            ["rev-list", "--count", "HEAD", "--not", "--remotes=origin"],
            cwd=project_dir,
        )
        if count_result.returncode == 0:
            try:
                ahead_count = int(count_result.stdout.strip())
            except ValueError:
                pass
        if ahead_count > 0:
            log_result = _run_git(
                ["log", "--oneline", "--format=%H %s", "HEAD", "--not", "--remotes=origin"],
                cwd=project_dir,
            )
            if log_result.returncode == 0:
                for line in log_result.stdout.strip().splitlines():
                    parts = line.split(" ", 1)
                    if len(parts) == 2:
                        commits.append(CommitInfo(sha=parts[0], message=parts[1]))

    return BranchStatus(
        branch=branch,
        remote_branch=remote_branch,
        ahead_count=ahead_count,
        behind_count=behind_count,
        commits=commits,
        has_remote=has_remote,
    )


def push_to_remote(
    project_dir: Path,
    target_branch: str | None = None,
) -> PushResult:
    """Push the current HEAD to the remote.

    Args:
        project_dir: Root of the git repository.
        target_branch: If provided, push to ``origin/<target_branch>`` and set
            upstream tracking.  If ``None``, push to the existing upstream.

    Returns:
        PushResult describing the outcome.
    """
    status = get_branch_status(project_dir)

    if not status.has_remote:
        return PushResult(
            success=False,
            error="No 'origin' remote configured",
            remote_branch="",
            pushed_commits=0,
        )

    if target_branch:
        # Push to a specific (possibly new) remote branch
        push_result = _run_git(
            ["push", "-u", "origin", f"HEAD:refs/heads/{target_branch}"],
            cwd=project_dir,
        )
        remote_ref = f"origin/{target_branch}"
    elif status.remote_branch:
        # Push to existing upstream
        push_result = _run_git(["push"], cwd=project_dir)
        remote_ref = status.remote_branch
    else:
        # No upstream and no target — push current branch to same-named remote
        push_result = _run_git(
            ["push", "-u", "origin", status.branch],
            cwd=project_dir,
        )
        remote_ref = f"origin/{status.branch}"

    if push_result.returncode != 0:
        stderr = push_result.stderr.strip()
        return PushResult(
            success=False,
            error=stderr or "Push failed",
            remote_branch=remote_ref,
            pushed_commits=0,
        )

    return PushResult(
        success=True,
        error=None,
        remote_branch=remote_ref,
        pushed_commits=status.ahead_count,
    )


def generate_branch_name(project_dir: Path) -> str:
    """Auto-generate a push target branch name based on current branch and timestamp.

    Args:
        project_dir: Root of the git repository.

    Returns:
        A branch name like ``push/main-20260319-091742``.
    """
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=project_dir)
    base = result.stdout.strip() if result.returncode == 0 else "HEAD"
    # Sanitize base for use in branch names
    safe_base = base.replace("/", "-").replace(" ", "-")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"push/{safe_base}-{ts}"


def generate_branch_name_llm(
    project_dir: Path,
    commits: list[CommitInfo],
    worker_spec: WorkerProviderSpec,
    state_root: Path,
) -> str:
    """Use an LLM worker to suggest a branch name from commit messages.

    Args:
        project_dir: Root of the git repository.
        commits: List of ahead-of-remote commits.
        worker_spec: Resolved worker provider specification.
        state_root: Directory for temporary run artifacts.

    Returns:
        A branch name like ``push/fix-auth-token-expiry``.

    Raises:
        ValueError: If the worker fails or returns an unparseable/empty name.
    """
    from ...prompts import load as load_prompt
    from ...workers.run import run_worker

    template = load_prompt("formatters/branch_name.md")
    commits_text = "\n".join(
        f"{c.sha[:7]} {c.message}" for c in commits
    )[:4000]
    formatted_prompt = template.format(commits=commits_text)

    run_dir = Path(tempfile.mkdtemp(dir=str(state_root)))
    progress_path = run_dir / "progress.json"
    try:
        result = run_worker(
            spec=worker_spec,
            prompt=formatted_prompt,
            project_dir=project_dir,
            run_dir=run_dir,
            timeout_seconds=60,
            heartbeat_seconds=30,
            heartbeat_grace_seconds=60,
            progress_path=progress_path,
        )
    except Exception as exc:
        raise ValueError(f"Branch name worker failed: {exc}") from exc
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)

    response_text = result.response_text or ""

    # Try JSON parse, then regex fallback
    branch_name: str | None = None
    try:
        parsed = json.loads(response_text)
        if isinstance(parsed, dict):
            branch_name = parsed.get("branch_name")
    except (json.JSONDecodeError, TypeError):
        pass

    if not branch_name:
        match = re.search(r'\{"branch_name"\s*:\s*"([^"]+)"\}', response_text)
        if match:
            branch_name = match.group(1)

    if not branch_name:
        raise ValueError("Failed to parse branch name from worker response")

    # Sanitize
    sanitized = branch_name.lower()
    sanitized = re.sub(r"[\s/]+", "-", sanitized)
    sanitized = re.sub(r"[^a-z0-9._-]", "", sanitized)
    sanitized = re.sub(r"-{2,}", "-", sanitized)
    sanitized = sanitized.strip("-")
    sanitized = sanitized[:50]

    if not sanitized:
        raise ValueError("Worker returned an empty branch name after sanitization")

    return f"push/{sanitized}"
