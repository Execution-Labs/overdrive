"""Git worktree, branch, and merge helpers for orchestrator tasks."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, TypedDict

from ..domain.models import now_iso

if TYPE_CHECKING:
    from .service import OrchestratorService

logger = logging.getLogger(__name__)


class PreserveOutcome(TypedDict):
    """Structured preserve result for deterministic blocked/review handling."""

    status: str
    reason_code: str
    commit_sha: str | None
    base_sha: str | None
    head_sha: str | None


class MergeOutcome(TypedDict, total=False):
    """Structured merge result for commit/approval flows."""

    status: str
    reason_code: str
    error: str
    commit_sha: str
    blocking_paths: list[str]
    unmerged_paths: list[str]
    stderr_excerpt: str


class WorktreeManager:
    """Coordinate task branch/worktree lifecycle and merge conflict handling."""

    def __init__(self, service: OrchestratorService) -> None:
        """Bind the manager to the owning orchestrator service state."""
        self._service = service

    _WORKTREE_ADD_MAX_ATTEMPTS = 3
    _WORKTREE_ADD_RETRY_SLEEP_SECONDS = 0.05
    _TRANSIENT_WORKTREE_ADD_ERROR_HINTS = (
        "already checked out at",
        "is already checked out",
        "worktree is already registered",
        "another git process seems to be running",
        "index.lock",
        "unable to create",
        "cannot lock ref",
        "could not lock",
        "resource temporarily unavailable",
    )

    @staticmethod
    def _local_branch_exists(project_dir: Path, branch_name: str) -> bool:
        """Return whether a local branch currently exists."""
        normalized = str(branch_name or "").strip()
        if not normalized:
            return False
        try:
            result = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{normalized}"],
                cwd=project_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except Exception:
            return False
        return result.returncode == 0

    def _prepare_worktree_dir(self, worktree_dir: Path) -> None:
        """Ensure target worktree path is clear before adding a new worktree."""
        svc = self._service
        if not worktree_dir.exists():
            return
        subprocess.run(
            ["git", "worktree", "remove", str(worktree_dir), "--force"],
            cwd=svc.container.project_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if worktree_dir.exists():
            shutil.rmtree(worktree_dir, ignore_errors=True)

    @classmethod
    def _is_transient_worktree_add_error(cls, stderr: str) -> bool:
        lowered = str(stderr or "").lower()
        if not lowered:
            return False
        return any(hint in lowered for hint in cls._TRANSIENT_WORKTREE_ADD_ERROR_HINTS)

    def _add_worktree_with_retry(
        self,
        *,
        worktree_dir: Path,
        branch: str,
        prefer_create_branch: bool,
    ) -> None:
        """Add task worktree with bounded retries for git-lock/registration races."""
        svc = self._service
        last_exc: subprocess.CalledProcessError | None = None
        for attempt in range(1, self._WORKTREE_ADD_MAX_ATTEMPTS + 1):
            self._prepare_worktree_dir(worktree_dir)
            create_branch = bool(
                prefer_create_branch and not self._local_branch_exists(svc.container.project_dir, branch)
            )
            cmd = ["git", "worktree", "add", str(worktree_dir)]
            if create_branch:
                cmd.extend(["-b", branch])
            else:
                cmd.append(branch)
            try:
                subprocess.run(
                    cmd,
                    cwd=svc.container.project_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                return
            except subprocess.CalledProcessError as exc:
                resolved_exc = exc
                stderr = str(getattr(exc, "stderr", "") or "")
                # Handle TOCTOU branch races: branch appeared after our local check.
                if create_branch and "already exists" in stderr.lower():
                    try:
                        self._prepare_worktree_dir(worktree_dir)
                        subprocess.run(
                            ["git", "worktree", "add", str(worktree_dir), branch],
                            cwd=svc.container.project_dir,
                            check=True,
                            capture_output=True,
                            text=True,
                            timeout=60,
                        )
                        return
                    except subprocess.CalledProcessError as race_exc:
                        resolved_exc = race_exc
                        stderr = str(getattr(race_exc, "stderr", "") or "")
                last_exc = resolved_exc
                if not self._is_transient_worktree_add_error(stderr):
                    raise resolved_exc
                # Best-effort prune clears stale worktree registrations before retry.
                subprocess.run(
                    ["git", "worktree", "prune"],
                    cwd=svc.container.project_dir,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                time.sleep(self._WORKTREE_ADD_RETRY_SLEEP_SECONDS * attempt)
        if last_exc is not None:
            raise last_exc

    @staticmethod
    def _clear_preserved_context_metadata(task: Any) -> None:
        """Remove preserved-branch metadata keys after merge/reattach."""
        if not isinstance(getattr(task, "metadata", None), dict):
            return
        for key in (
            "preserved_branch",
            "preserved_base_branch",
            "preserved_base_sha",
            "preserved_head_sha",
            "preserved_merge_base_sha",
            "preserved_at",
        ):
            task.metadata.pop(key, None)

    def create_worktree(self, task: Any) -> Optional[Path]:
        """Create a task-specific worktree and branch when git metadata exists.

        Returns ``None`` for non-git projects so callers can fall back to
        in-place execution in the primary repository directory.
        """
        svc = self._service
        git_dir = svc.container.project_dir / ".git"
        if not git_dir.exists():
            return None
        self.ensure_branch()
        task_id = str(task.id)
        worktree_dir = svc.container.state_root / "worktrees" / task_id
        branch = f"task-{task_id}"
        self._add_worktree_with_retry(
            worktree_dir=worktree_dir,
            branch=branch,
            prefer_create_branch=True,
        )
        return worktree_dir

    def create_worktree_from_branch(self, task: Any, branch: str) -> Optional[Path]:
        """Create a worktree by checking out an existing branch (no ``-b`` flag).

        Used when retrying a task that has a preserved branch so that prior
        committed work is carried forward into the new worktree.
        """
        svc = self._service
        git_dir = svc.container.project_dir / ".git"
        if not git_dir.exists():
            return None
        self.ensure_branch()
        task_id = str(task.id)
        worktree_dir = svc.container.state_root / "worktrees" / task_id
        self._add_worktree_with_retry(
            worktree_dir=worktree_dir,
            branch=branch,
            prefer_create_branch=False,
        )
        return worktree_dir

    def merge_and_cleanup(self, task: Any, worktree_dir: Path) -> MergeOutcome:
        """Merge task work into the base branch, then remove transient worktree.

        Merge failures are classified into explicit statuses:
        ``merge_conflict`` (unmerged entries), ``dirty_overlapping`` (local
        tracked/untracked overlap), and ``git_error`` (other git failures).
        """
        svc = self._service
        branch = f"task-{task.id}"
        merge_outcome: MergeOutcome = {"status": "ok", "reason_code": "ok"}
        with svc._merge_lock:
            merge_outcome = self._merge_branch_with_classification(task, branch)
        if merge_outcome.get("status") == "ok":
            subprocess.run(
                ["git", "worktree", "remove", str(worktree_dir), "--force"],
                cwd=svc.container.project_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=svc.container.project_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            svc._integration_health.record_merge()
        return merge_outcome

    def approve_and_merge(self, task: Any) -> dict[str, Any]:
        """Merge a preserved task branch after manual review approval.

        Returns a status payload for API handlers and persists metadata cleanup
        when the branch no longer exists or merges successfully.
        """
        svc = self._service
        branch = task.metadata.get("preserved_branch")
        if not branch:
            return {"status": "ok"}

        result = subprocess.run(
            ["git", "branch", "--list", branch],
            cwd=svc.container.project_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if not result.stdout.strip():
            self._clear_preserved_context_metadata(task)
            svc.container.tasks.upsert(task)
            return {"status": "ok"}

        self.ensure_branch()

        with svc._merge_lock:
            merge_outcome = self._merge_branch_with_classification(task, branch)
            if merge_outcome.get("status") != "ok":
                svc.container.tasks.upsert(task)
                return dict(merge_outcome)
            sha = str(merge_outcome.get("commit_sha") or "").strip() or subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=svc.container.project_dir,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()

        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=svc.container.project_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        svc._integration_health.record_merge()
        self._clear_preserved_context_metadata(task)
        task.metadata.pop("merge_conflict", None)
        svc.container.tasks.upsert(task)
        return {"status": "ok", "commit_sha": sha}

    def _merge_branch_with_classification(self, task: Any, branch: str) -> MergeOutcome:
        """Merge branch and classify failures into conflict/dirty/git-error buckets.

        When a merge conflict is detected, the merge lock is released before
        dispatching to the external worker so that other git operations are not
        blocked for the duration of the resolution attempt.  Git itself prevents
        concurrent merges while unmerged entries exist, so releasing the lock is
        safe in this state.
        """
        svc = self._service
        try:
            subprocess.run(
                ["git", "merge", branch, "--ff", "--no-edit"],
                cwd=svc.container.project_dir,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=svc.container.project_dir,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            self._clear_merge_failure_metadata(task)
            return {"status": "ok", "reason_code": "ok", "commit_sha": sha}
        except subprocess.CalledProcessError as exc:
            stderr = str(exc.stderr or "").strip()
            stdout = str(exc.stdout or "").strip()
            unmerged = self._safe_list_unmerged_files(svc.container.project_dir)
            if unmerged:
                # Release the merge lock before calling the external worker so
                # other git operations are not blocked during resolution.
                svc._merge_lock.release()
                try:
                    resolved = self.resolve_merge_conflict(task, branch)
                finally:
                    svc._merge_lock.acquire()
                if resolved:
                    sha = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=svc.container.project_dir,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    ).stdout.strip()
                    self._clear_merge_failure_metadata(task)
                    return {"status": "ok", "reason_code": "ok", "commit_sha": sha}
                self._abort_merge_if_needed(svc.container.project_dir)
                reason_code = "merge_conflict"
                error = "Merge conflict could not be resolved automatically"
                self._record_merge_failure(
                    task,
                    reason_code=reason_code,
                    error=error,
                    unmerged_paths=unmerged,
                    stderr_excerpt=self._clip_merge_stderr(stderr, stdout),
                    is_conflict=True,
                )
                return {
                    "status": "merge_conflict",
                    "reason_code": reason_code,
                    "error": error,
                    "unmerged_paths": unmerged,
                    "stderr_excerpt": self._clip_merge_stderr(stderr, stdout),
                }

            self._abort_merge_if_needed(svc.container.project_dir)
            reason_code, blocking_paths = self._classify_merge_failure(stderr, stdout)
            if reason_code == "dirty_overlapping":
                if blocking_paths:
                    joined = ", ".join(blocking_paths)
                    error = f"Integration branch has local changes that overlap this merge: {joined}"
                else:
                    error = "Integration branch has local changes that overlap this merge"
            else:
                reason_code = "git_error"
                error = "Git merge failed before conflict resolution"
            stderr_excerpt = self._clip_merge_stderr(stderr, stdout)
            self._record_merge_failure(
                task,
                reason_code=reason_code,
                error=error,
                blocking_paths=blocking_paths,
                stderr_excerpt=stderr_excerpt,
                is_conflict=False,
            )
            return {
                "status": reason_code,
                "reason_code": reason_code,
                "error": error,
                "blocking_paths": blocking_paths,
                "stderr_excerpt": stderr_excerpt,
            }
        except subprocess.TimeoutExpired:
            self._abort_merge_if_needed(svc.container.project_dir)
            error = "Git merge timed out"
            self._record_merge_failure(
                task,
                reason_code="git_error",
                error=error,
                stderr_excerpt=error,
                is_conflict=False,
            )
            return {
                "status": "git_error",
                "reason_code": "git_error",
                "error": error,
                "stderr_excerpt": error,
            }

    @staticmethod
    def _safe_list_unmerged_files(project_dir: Path) -> list[str]:
        """Return unresolved paths, swallowing git failures during error handling."""
        try:
            return WorktreeManager._list_unmerged_files(project_dir)
        except Exception:
            return []

    @staticmethod
    def _clip_merge_stderr(stderr: str, stdout: str) -> str:
        text = (stderr or "").strip() or (stdout or "").strip()
        if len(text) > 1000:
            return text[:1000].rstrip() + "..."
        return text

    @staticmethod
    def _parse_overwritten_paths(stderr_text: str, header: str) -> list[str]:
        """Extract path list from git overwrite errors."""
        lines = stderr_text.splitlines()
        start = -1
        header_l = header.lower()
        for idx, line in enumerate(lines):
            if header_l in line.lower():
                start = idx + 1
                break
        if start < 0:
            return []
        out: list[str] = []
        for line in lines[start:]:
            stripped = line.strip()
            if not stripped:
                continue
            low = stripped.lower()
            if low.startswith("please ") or low.startswith("aborting") or low.startswith("merge "):
                break
            if line.startswith("\t") or line.startswith(" "):
                out.append(stripped)
                continue
            # Stop when the listing block ends.
            break
        deduped: list[str] = []
        seen: set[str] = set()
        for path in out:
            if path in seen:
                continue
            seen.add(path)
            deduped.append(path)
        return deduped

    @classmethod
    def _classify_merge_failure(cls, stderr: str, stdout: str) -> tuple[str, list[str]]:
        """Classify merge failure with best-effort extraction of blocking paths."""
        text = f"{stderr}\n{stdout}".lower()
        local_header = "your local changes to the following files would be overwritten by merge"
        untracked_header = "the following untracked working tree files would be overwritten by merge"
        if local_header in text:
            return "dirty_overlapping", cls._parse_overwritten_paths(stderr or "", local_header)
        if untracked_header in text:
            return "dirty_overlapping", cls._parse_overwritten_paths(stderr or "", untracked_header)
        if "would be overwritten by merge" in text:
            return "dirty_overlapping", []
        if "cannot merge" in text and "entry '" in text and "not uptodate" in text:
            return "dirty_overlapping", []
        return "git_error", []

    @staticmethod
    def _merge_in_progress(project_dir: Path) -> bool:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                cwd=project_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
        except Exception:
            return False
        return result.returncode == 0

    @classmethod
    def _abort_merge_if_needed(cls, project_dir: Path) -> None:
        if not cls._merge_in_progress(project_dir):
            return
        subprocess.run(
            ["git", "merge", "--abort"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    @staticmethod
    def _clear_merge_failure_metadata(task: Any) -> None:
        if not isinstance(getattr(task, "metadata", None), dict):
            return
        for key in ("merge_failure_reason_code", "merge_failure_details", "merge_conflict"):
            task.metadata.pop(key, None)

    @staticmethod
    def _record_merge_failure(
        task: Any,
        *,
        reason_code: str,
        error: str,
        blocking_paths: list[str] | None = None,
        unmerged_paths: list[str] | None = None,
        stderr_excerpt: str = "",
        is_conflict: bool,
    ) -> None:
        if not isinstance(getattr(task, "metadata", None), dict):
            task.metadata = {}
        details: dict[str, Any] = {}
        if blocking_paths:
            details["blocking_paths"] = list(blocking_paths)
        if unmerged_paths:
            details["unmerged_paths"] = list(unmerged_paths)
        if stderr_excerpt:
            details["stderr_excerpt"] = stderr_excerpt
        task.metadata["merge_failure_reason_code"] = reason_code
        task.metadata["merge_failure_details"] = details
        if is_conflict:
            task.metadata["merge_conflict"] = True
        else:
            task.metadata.pop("merge_conflict", None)
        if hasattr(task, "error"):
            task.error = error

    def resolve_merge_conflict(self, task: Any, branch: str) -> bool:
        """Try worker-assisted conflict resolution for the currently running merge.

        The method snapshots conflict context into task metadata so the worker
        prompt includes file-level conflict text and related task objectives.
        """
        svc = self._service
        saved_worktree_dir = task.metadata.get("worktree_dir")
        previous_error = ""
        try:
            cfg = svc.container.config.load()
            orch_cfg = dict(cfg.get("orchestrator") or {})
            max_attempts = int(orch_cfg.get("max_merge_conflict_attempts", 3) or 3)
            max_attempts = max(1, max_attempts)

            other_tasks_info, other_objectives = self._gather_peer_context(task)
            task.metadata.pop("worktree_dir", None)

            for attempt in range(1, max_attempts + 1):
                conflicted_files = self._list_unmerged_files(svc.container.project_dir)
                if not conflicted_files:
                    return False

                conflict_contents: dict[str, str] = {}
                for fpath in conflicted_files:
                    full = svc.container.project_dir / fpath
                    if full.exists():
                        conflict_contents[fpath] = full.read_text(errors="replace")

                task.metadata["merge_conflict_files"] = conflict_contents
                task.metadata["merge_other_tasks"] = other_tasks_info
                task.metadata["merge_current_objective"] = self.format_task_objective_summary(task)
                task.metadata["merge_other_objectives"] = other_objectives
                task.metadata["merge_conflict_attempt"] = attempt
                task.metadata["merge_conflict_max_attempts"] = max_attempts
                if attempt > 1 and previous_error:
                    task.metadata["merge_conflict_previous_error"] = previous_error
                else:
                    task.metadata.pop("merge_conflict_previous_error", None)
                svc.container.tasks.upsert(task)

                step_result = None
                try:
                    step_result = svc.worker_adapter.run_step(task=task, step="resolve_merge", attempt=attempt)
                except Exception as exc:
                    previous_error = f"Merge resolution worker raised exception: {exc}"
                    logger.exception(
                        "Resolve-merge worker crashed for task %s on attempt %s/%s (branch %s)",
                        task.id,
                        attempt,
                        max_attempts,
                        branch,
                    )

                if step_result is not None and step_result.status == "ok":
                    subprocess.run(
                        ["git", "add", "-A"],
                        cwd=svc.container.project_dir,
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    unresolved_files = self._list_unmerged_files(svc.container.project_dir)
                    if unresolved_files:
                        marker_files = self._check_remaining_conflicts(svc.container.project_dir, unresolved_files)
                        if marker_files:
                            previous_error = (
                                "Worker reported success but unresolved merge entries remain in "
                                f"{', '.join(unresolved_files)}; conflict markers still present in "
                                f"{', '.join(marker_files)}."
                            )
                        else:
                            previous_error = (
                                "Worker reported success but unresolved merge entries remain in "
                                f"{', '.join(unresolved_files)}."
                            )
                        if attempt < max_attempts:
                            self._reset_conflicted_files(svc.container.project_dir, unresolved_files)
                        continue
                    marker_files = self._check_remaining_conflicts(svc.container.project_dir, conflicted_files)
                    if marker_files:
                        previous_error = (
                            "Worker reported success but conflict markers are still present in "
                            f"{', '.join(marker_files)}."
                        )
                        if attempt < max_attempts:
                            self._reset_conflicted_files(svc.container.project_dir, conflicted_files)
                        continue
                    subprocess.run(
                        ["git", "commit", "--no-edit"],
                        cwd=svc.container.project_dir,
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    return True
                else:
                    summary = str((step_result.summary if step_result is not None else "") or "").strip()
                    previous_error = summary or "Merge resolution worker returned non-ok status."

                if attempt < max_attempts:
                    retry_files = self._list_unmerged_files(svc.container.project_dir)
                    if retry_files:
                        self._reset_conflicted_files(svc.container.project_dir, retry_files)
            return False
        except Exception:
            logger.exception("Failed to resolve merge conflict for task %s", task.id)
            return False
        finally:
            task.metadata.pop("merge_conflict_files", None)
            task.metadata.pop("merge_other_tasks", None)
            task.metadata.pop("merge_current_objective", None)
            task.metadata.pop("merge_other_objectives", None)
            task.metadata.pop("merge_conflict_attempt", None)
            task.metadata.pop("merge_conflict_max_attempts", None)
            task.metadata.pop("merge_conflict_previous_error", None)
            if saved_worktree_dir:
                task.metadata["worktree_dir"] = saved_worktree_dir

    def _gather_peer_context(self, task: Any) -> tuple[list[str], list[str]]:
        """Build cross-task context to guide worker conflict resolution."""
        svc = self._service
        other_tasks_info: list[str] = []
        other_objectives: list[str] = []
        peers = [other for other in svc.container.tasks.list() if other.id != task.id and other.status == "done"]
        if not peers:
            peers = [other for other in svc.container.tasks.list() if other.id != task.id]
        for other in peers:
            other_tasks_info.append(f"- {other.title}: {other.description}")
            other_objectives.append(self.format_task_objective_summary(other))
        return other_tasks_info, other_objectives

    @staticmethod
    def _list_unmerged_files(project_dir: Path) -> list[str]:
        """Return current unresolved merge paths from git index state."""
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=project_dir,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return [f for f in result.stdout.strip().split("\n") if f]

    @staticmethod
    def _check_remaining_conflicts(project_dir: Path, files: list[str]) -> list[str]:
        """Return files that still contain git conflict marker lines."""
        remaining: list[str] = []
        for fpath in files:
            full = project_dir / fpath
            if not full.exists():
                continue
            for line in full.read_text(errors="replace").splitlines():
                if (
                    line.startswith("<<<<<<< ")
                    or line == "======="
                    or line.startswith(">>>>>>> ")
                    or line.startswith("||||||| ")
                ):
                    remaining.append(fpath)
                    break
        return remaining

    @staticmethod
    def _reset_conflicted_files(project_dir: Path, files: list[str]) -> None:
        """Best-effort reset to three-way conflict state between retries."""
        for fpath in files:
            try:
                subprocess.run(
                    ["git", "checkout", "--merge", "--", fpath],
                    cwd=project_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception:
                logger.warning("Failed to reset conflicted file before retry: %s", fpath, exc_info=True)

    def cleanup_orphaned_worktrees(self) -> None:
        """Remove leftover task worktrees and delete unneeded task branches."""
        svc = self._service
        worktrees_dir = svc.container.state_root / "worktrees"
        if not worktrees_dir.exists():
            return
        if not (svc.container.project_dir / ".git").exists():
            return
        referenced_branches: set[str] = set()
        referenced_worktrees: set[Path] = set()
        terminal_statuses = {"done", "cancelled"}
        for t in svc.container.tasks.list():
            metadata = t.metadata if isinstance(t.metadata, dict) else {}
            if t.status not in terminal_statuses:
                task_context_raw = metadata.get("task_context")
                task_context = task_context_raw if isinstance(task_context_raw, dict) else {}
                for key in ("worktree_dir",):
                    raw_path = str(task_context.get(key) or "").strip()
                    if raw_path:
                        try:
                            referenced_worktrees.add(Path(raw_path).expanduser().resolve())
                        except Exception:
                            pass
                raw_worktree_dir = str(metadata.get("worktree_dir") or "").strip()
                if raw_worktree_dir:
                    try:
                        referenced_worktrees.add(Path(raw_worktree_dir).expanduser().resolve())
                    except Exception:
                        pass
                for branch_key in ("task_branch", "preserved_branch"):
                    branch = str(task_context.get(branch_key) or "").strip()
                    if branch:
                        referenced_branches.add(branch)
                review_context_raw = metadata.get("review_context")
                review_context = review_context_raw if isinstance(review_context_raw, dict) else {}
                review_branch = str(review_context.get("preserved_branch") or "").strip()
                if review_branch:
                    referenced_branches.add(review_branch)
            pb = str(metadata.get("preserved_branch") or "").strip()
            if pb:
                referenced_branches.add(pb)
        for child in worktrees_dir.iterdir():
            if child.is_dir():
                try:
                    child_resolved = child.resolve()
                except Exception:
                    child_resolved = child
                if child_resolved in referenced_worktrees:
                    continue
                branch_name = f"task-{child.name}"
                subprocess.run(
                    ["git", "worktree", "remove", str(child), "--force"],
                    cwd=svc.container.project_dir,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if branch_name not in referenced_branches:
                    subprocess.run(
                        ["git", "branch", "-D", branch_name],
                        cwd=svc.container.project_dir,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )

    def ensure_branch(self) -> Optional[str]:
        """Record the user's current branch as the orchestrator base branch.

        Previously this created an ephemeral ``orchestrator-run-*`` branch.
        Now it simply reads the current branch name so that task merges land
        directly on whatever branch the user was on (e.g. ``main``).
        """
        svc = self._service
        if svc._run_branch:
            return svc._run_branch
        with svc._branch_lock:
            if svc._run_branch:
                return svc._run_branch
            git_dir = svc.container.project_dir / ".git"
            if not git_dir.exists():
                return None
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=svc.container.project_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                branch = result.stdout.strip()
                if branch and branch != "HEAD":
                    svc._run_branch = branch
                    return branch
                return None
            except subprocess.CalledProcessError:
                return None

    def commit_for_task(self, task: Any, working_dir: Optional[Path] = None) -> Optional[str]:
        """Stage and commit task changes, returning the commit SHA on success.

        Returns ``None`` when there is no git repository or commit creation
        fails (for example when there are no staged changes).
        """
        svc = self._service
        cwd = working_dir or svc.container.project_dir
        if not (cwd / ".git").exists() and not (svc.container.project_dir / ".git").exists():
            return None
        if working_dir is None:
            self.ensure_branch()
        try:
            subprocess.run(["git", "add", "-A"], cwd=cwd, check=True, capture_output=True, text=True, timeout=30)
            subprocess.run(
                ["git", "commit", "-m", f"task({task.id}): {task.title[:60]}"],
                cwd=cwd,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=cwd,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            return sha
        except subprocess.CalledProcessError:
            return None

    def has_uncommitted_changes(self, cwd: Path) -> bool:
        """Return whether git reports staged or unstaged changes for ``cwd``."""
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            return bool(result.stdout.strip())
        except subprocess.CalledProcessError:
            return True

    def has_commits_ahead(self, cwd: Path) -> bool:
        """Return whether ``cwd``'s HEAD has commits beyond the base branch.

        Used to detect prior committed work on a task branch (e.g. from a
        preserved branch) even when there are no uncommitted changes.
        """
        svc = self._service
        base_ref = str(svc._run_branch or "").strip()
        if not base_ref:
            try:
                current_branch = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=svc.container.project_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                candidate = str(current_branch.stdout or "").strip()
                if current_branch.returncode == 0 and candidate and candidate != "HEAD":
                    base_ref = candidate
            except Exception:
                base_ref = ""
        if not base_ref:
            base_ref = "HEAD"
        try:
            result = subprocess.run(
                ["git", "log", f"{base_ref}..HEAD", "--oneline"],
                cwd=cwd,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            return bool(result.stdout.strip())
        except subprocess.CalledProcessError:
            # Err on the side of preserving work — matches has_uncommitted_changes
            # which also returns the conservative default (True) on git errors.
            return True

    def preserve_worktree_work(self, task: Any, worktree_dir: Path) -> PreserveOutcome:
        """Persist task edits by keeping branch history but removing worktree dir.

        This is used when human review is required before final merge into the
        base branch.
        """
        svc = self._service
        branch = f"task-{task.id}"
        try:
            if not isinstance(task.metadata, dict):
                task.metadata = {}
            had_uncommitted = self.has_uncommitted_changes(worktree_dir)
            had_commits_ahead = self.has_commits_ahead(worktree_dir)
            has_material_work = had_uncommitted or had_commits_ahead
            if not has_material_work:
                return {
                    "status": "no_changes",
                    "reason_code": "no_task_changes",
                    "commit_sha": None,
                    "base_sha": None,
                    "head_sha": None,
                }

            before_head = ""
            before_head_result = subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"],
                cwd=worktree_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if before_head_result.returncode == 0:
                before_head = before_head_result.stdout.strip()
            svc._cleanup_workdoc_for_commit(worktree_dir)
            commit_sha = self.commit_for_task(task, worktree_dir)
            after_head = ""
            after_head_result = subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"],
                cwd=worktree_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if after_head_result.returncode == 0:
                after_head = after_head_result.stdout.strip()

            if had_uncommitted and (not commit_sha or not after_head or after_head == before_head):
                return {
                    "status": "failed",
                    "reason_code": "dirty_not_preserved",
                    "commit_sha": commit_sha,
                    "base_sha": None,
                    "head_sha": after_head or None,
                }
            if not commit_sha and not had_commits_ahead:
                return {
                    "status": "failed",
                    "reason_code": "preserve_commit_missing",
                    "commit_sha": None,
                    "base_sha": None,
                    "head_sha": after_head or None,
                }

            base_ref = str(svc._run_branch or "").strip()
            if not base_ref:
                try:
                    current_branch_result = subprocess.run(
                        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                        cwd=svc.container.project_dir,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=10,
                    )
                    candidate = current_branch_result.stdout.strip()
                    if current_branch_result.returncode == 0 and candidate and candidate != "HEAD":
                        base_ref = candidate
                except Exception:
                    base_ref = ""
            if not base_ref:
                base_ref = "HEAD"
            try:
                result = subprocess.run(
                    ["git", "log", f"{base_ref}..{branch}", "--oneline"],
                    cwd=svc.container.project_dir,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=10,
                )
                if not result.stdout.strip():
                    return {
                        "status": "failed",
                        "reason_code": "branch_ahead_missing",
                        "commit_sha": commit_sha,
                        "base_sha": None,
                        "head_sha": after_head or None,
                    }
            except subprocess.CalledProcessError:
                pass

            subprocess.run(
                ["git", "worktree", "remove", str(worktree_dir), "--force"],
                cwd=svc.container.project_dir,
                capture_output=True,
                text=True,
                timeout=60,
            )
            task.metadata["preserved_branch"] = branch
            if base_ref != "HEAD":
                task.metadata["preserved_base_branch"] = str(base_ref)
            else:
                task.metadata.pop("preserved_base_branch", None)
            task.metadata["preserved_at"] = now_iso()

            try:
                base_sha_result = subprocess.run(
                    ["git", "rev-parse", "--verify", f"{base_ref}^{{commit}}"],
                    cwd=svc.container.project_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                base_sha = base_sha_result.stdout.strip() if base_sha_result.returncode == 0 else ""
                if base_sha:
                    task.metadata["preserved_base_sha"] = base_sha
                else:
                    task.metadata.pop("preserved_base_sha", None)
            except Exception:
                task.metadata.pop("preserved_base_sha", None)

            try:
                head_sha_result = subprocess.run(
                    ["git", "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"],
                    cwd=svc.container.project_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
                head_sha = head_sha_result.stdout.strip() if head_sha_result.returncode == 0 else ""
                if head_sha:
                    task.metadata["preserved_head_sha"] = head_sha
                else:
                    task.metadata.pop("preserved_head_sha", None)
            except Exception:
                task.metadata.pop("preserved_head_sha", None)

            base_sha_for_merge = str(task.metadata.get("preserved_base_sha") or "").strip()
            head_sha_for_merge = str(task.metadata.get("preserved_head_sha") or "").strip()
            if base_sha_for_merge and head_sha_for_merge:
                try:
                    merge_base_result = subprocess.run(
                        ["git", "merge-base", base_sha_for_merge, head_sha_for_merge],
                        cwd=svc.container.project_dir,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=10,
                    )
                    merge_base_sha = merge_base_result.stdout.strip() if merge_base_result.returncode == 0 else ""
                    if merge_base_sha:
                        task.metadata["preserved_merge_base_sha"] = merge_base_sha
                    else:
                        task.metadata.pop("preserved_merge_base_sha", None)
                except Exception:
                    task.metadata.pop("preserved_merge_base_sha", None)
            else:
                task.metadata.pop("preserved_merge_base_sha", None)

            svc.container.tasks.upsert(task)
            return {
                "status": "preserved",
                "reason_code": "ok",
                "commit_sha": commit_sha or None,
                "base_sha": str(task.metadata.get("preserved_base_sha") or "").strip() or None,
                "head_sha": str(task.metadata.get("preserved_head_sha") or "").strip() or None,
            }
        except Exception:
            logger.exception("Failed to preserve worktree work for task %s", task.id)
            return {
                "status": "failed",
                "reason_code": "exception",
                "commit_sha": None,
                "base_sha": None,
                "head_sha": None,
            }

    def resolve_task_plan_excerpt(self, task: Any, *, max_chars: int = 800) -> str:
        """Extract bounded plan text used in merge-conflict prompt context."""
        svc = self._service
        if not isinstance(task.metadata, dict):
            return ""

        for key in ("committed_plan_revision_id", "latest_plan_revision_id"):
            rev_id = str(task.metadata.get(key) or "").strip()
            if not rev_id:
                continue
            revision = svc.container.plan_revisions.get(rev_id)
            if revision and revision.task_id == task.id and str(revision.content or "").strip():
                return str(revision.content).strip()[:max_chars]

        step_outputs = task.metadata.get("step_outputs")
        if isinstance(step_outputs, dict):
            plan_text = str(step_outputs.get("plan") or "").strip()
            if plan_text:
                return plan_text[:max_chars]
        return ""

    def format_task_objective_summary(self, task: Any, *, max_chars: int = 1600) -> str:
        """Compose a compact objective summary for conflict-resolution prompts."""
        lines = [f"- Task: {task.title}"]
        if task.description:
            lines.append(f"  Description: {task.description}")
        plan_excerpt = self.resolve_task_plan_excerpt(task)
        if plan_excerpt:
            lines.append("  Plan excerpt:")
            lines.append("  ---")
            lines.append(plan_excerpt)
            lines.append("  ---")
        return "\n".join(lines)[:max_chars]
