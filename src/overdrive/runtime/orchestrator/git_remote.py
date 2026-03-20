"""Git remote helpers: branch status, ahead/behind counts, and push operations."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable


class PushCancelledError(Exception):
    """Raised when a push operation is cancelled by the user."""

if TYPE_CHECKING:
    from ...workers.config import WorkerProviderSpec

logger = logging.getLogger(__name__)

_SUBPROCESS_TIMEOUT = 30
_PUSH_TIMEOUT = 300  # push may trigger pre-push hooks (tests, linting, etc.)


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
    on_output: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> PushResult:
    """Push the current HEAD to the remote.

    Args:
        project_dir: Root of the git repository.
        target_branch: If provided, push to ``origin/<target_branch>`` and set
            upstream tracking.  If ``None``, push to the existing upstream.
        on_output: Optional callback invoked with each line of output from the
            push process (stdout + stderr merged).  Useful for streaming
            progress to the frontend.
        cancel: Optional threading event; when set the push subprocess is killed.

    Returns:
        PushResult describing the outcome.

    Raises:
        PushCancelledError: If *cancel* is set while the push is running.
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
        push_args = ["push", "-u", "origin", f"HEAD:refs/heads/{target_branch}"]
        remote_ref = f"origin/{target_branch}"
    elif status.remote_branch:
        push_args = ["push"]
        remote_ref = status.remote_branch
    else:
        push_args = ["push", "-u", "origin", status.branch]
        remote_ref = f"origin/{status.branch}"

    push_result = _run_git_streaming(
        push_args, cwd=project_dir, timeout=_PUSH_TIMEOUT,
        on_output=on_output, cancel=cancel,
    )

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


def _run_git_streaming(
    args: list[str],
    cwd: Path,
    *,
    timeout: int = _PUSH_TIMEOUT,
    on_output: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a git command, streaming merged stdout+stderr line-by-line.

    Falls back to ``_run_git`` when no *on_output* callback is provided.

    Raises:
        PushCancelledError: If *cancel* is set while the process is running.
    """
    if on_output is None and cancel is None:
        return _run_git(args, cwd=cwd, timeout=timeout)

    proc = subprocess.Popen(
        ["git", *args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    cancelled = False
    done = threading.Event()  # set when the process finishes normally

    def _cancel_watchdog() -> None:
        """Kill the subprocess promptly when the cancel event is set."""
        nonlocal cancelled
        if cancel is not None and cancel.wait(timeout=timeout):
            if not done.is_set():
                cancelled = True
                proc.kill()

    watchdog: threading.Thread | None = None
    if cancel is not None:
        watchdog = threading.Thread(target=_cancel_watchdog, daemon=True)
        watchdog.start()

    collected: list[str] = []
    try:
        assert proc.stdout is not None  # noqa: S101
        for line in proc.stdout:
            stripped = line.rstrip("\n")
            collected.append(stripped)
            if on_output is not None:
                on_output(stripped)
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    finally:
        done.set()
        proc.stdout.close()  # type: ignore[union-attr]
        if cancel is not None:
            cancel.set()  # unblock watchdog if still waiting
        if watchdog is not None:
            watchdog.join(timeout=2)

    if cancelled:
        raise PushCancelledError("Push cancelled by user")

    stderr_text = "\n".join(collected)
    return subprocess.CompletedProcess(
        args=["git", *args], returncode=returncode, stdout="", stderr=stderr_text,
    )


def run_fix_and_push_worker(
    project_dir: Path,
    error_output: str,
    target_branch: str | None,
    worker_spec: "WorkerProviderSpec",
    state_root: Path,
    on_output: Callable[[str], None] | None = None,
    on_done: Callable[[bool, str], None] | None = None,
) -> None:
    """Launch a worker to fix a push error, commit the fix, and push.

    This is a self-contained blocking function meant to run in a background
    thread.  The worker receives the full error output and a prompt telling
    it to fix the code, commit, and push.  The orchestrator does not manage
    any of the retry logic.

    Args:
        project_dir: Root of the git repository.
        error_output: Combined stdout/stderr from the failed push.
        target_branch: Remote branch target (passed to push command).
        worker_spec: Resolved worker provider specification.
        state_root: Directory for temporary run artifacts.
        on_output: Optional line-by-line progress callback.
        on_done: Optional callback ``(success, detail)`` fired on completion.
    """
    from ...prompts import load as load_prompt
    from ...workers.run import run_worker

    def _emit(line: str) -> None:
        if on_output is not None:
            on_output(line)

    push_target_desc = target_branch or "the current upstream branch"
    template = load_prompt("formatters/fix_push_error.md")
    formatted_prompt = (
        template
        .replace("{error_output}", error_output[:8000])
        .replace("{project_dir}", str(project_dir))
        .replace("{push_target}", push_target_desc)
    )

    run_dir = Path(tempfile.mkdtemp(dir=str(state_root)))
    progress_path = run_dir / "progress.json"
    stderr_path = run_dir / "stderr.log"

    # Tail the worker's stderr log and forward lines to on_output.
    # This mirrors how task detail logs are streamed: the worker writes
    # to a file via _stream_pipe, and we read new lines from that file.
    stop_tail = threading.Event()

    def _tail_stderr() -> None:
        """Read new lines from stderr.log and emit them."""
        # Wait for the file to appear (worker creates it on start)
        for _ in range(50):
            if stderr_path.exists() or stop_tail.is_set():
                break
            stop_tail.wait(0.1)
        if not stderr_path.exists():
            return
        with open(stderr_path) as fh:
            while not stop_tail.is_set():
                line = fh.readline()
                if line:
                    stripped = line.rstrip("\n")
                    if stripped:
                        _emit(stripped)
                else:
                    stop_tail.wait(0.3)
            # Drain any lines written between last poll and stop signal
            for line in fh:
                stripped = line.rstrip("\n")
                if stripped:
                    _emit(stripped)

    tail_thread: threading.Thread | None = None
    if on_output is not None:
        tail_thread = threading.Thread(target=_tail_stderr, daemon=True)
        tail_thread.start()

    # Step 1 — worker fixes the code
    _emit("Worker is analyzing the error and applying a fix...")
    try:
        run_worker(
            spec=worker_spec,
            prompt=formatted_prompt,
            project_dir=project_dir,
            run_dir=run_dir,
            timeout_seconds=120,
            heartbeat_seconds=30,
            heartbeat_grace_seconds=60,
            progress_path=progress_path,
        )
    except FileNotFoundError as exc:
        binary = getattr(exc, "filename", None) or "worker"
        detail = (
            f"'{binary}' is not installed or not on PATH. "
            f"Install it or configure a different worker provider."
        )
        _emit(detail)
        if on_done:
            on_done(False, detail)
        return
    except Exception as exc:
        _emit(f"Worker failed: {exc}")
        if on_done:
            on_done(False, str(exc))
        return
    finally:
        stop_tail.set()
        if tail_thread is not None:
            tail_thread.join(timeout=2)
        shutil.rmtree(run_dir, ignore_errors=True)

    # Step 2 — stage + commit (only if worker actually changed something)
    _emit("Committing the fix...")
    add = _run_git(["add", "-A"], cwd=project_dir)
    if add.returncode != 0:
        detail = f"git add failed: {add.stderr.strip()}"
        _emit(detail)
        if on_done:
            on_done(False, detail)
        return

    # Check if there are staged changes before amending
    diff_check = _run_git(["diff", "--cached", "--quiet"], cwd=project_dir)
    if diff_check.returncode == 0:
        detail = "Worker made no changes — nothing to fix"
        _emit(detail)
        if on_done:
            on_done(False, detail)
        return

    commit = _run_git(
        ["commit", "--amend", "--no-edit"], cwd=project_dir,
    )
    if commit.returncode != 0:
        detail = f"git commit failed: {commit.stderr.strip()}"
        _emit(detail)
        if on_done:
            on_done(False, detail)
        return

    # Step 3 — push
    _emit("Pushing...")
    result = push_to_remote(project_dir, target_branch, on_output=on_output)
    if result.success:
        _emit(f"Pushed successfully to {result.remote_branch}")
        if on_done:
            on_done(True, f"Pushed to {result.remote_branch}")
    else:
        _emit(f"Push still failed: {result.error}")
        if on_done:
            on_done(False, result.error or "Push failed")


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
    except FileNotFoundError as exc:
        binary = getattr(exc, "filename", None) or "worker"
        raise ValueError(
            f"'{binary}' is not installed or not on PATH. "
            f"Install it or configure a different worker provider."
        ) from exc
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
