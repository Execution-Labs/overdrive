"""Tests for the git_remote helper module."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from overdrive.runtime.orchestrator.git_remote import (
    get_branch_status,
    push_to_remote,
)


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )


def _init_repo(path: Path) -> None:
    """Create an initialised git repo with one commit."""
    _git(["init", "-b", "main"], cwd=path)
    _git(["config", "user.email", "test@test.com"], cwd=path)
    _git(["config", "user.name", "Test"], cwd=path)
    (path / "README.md").write_text("init\n")
    _git(["add", "README.md"], cwd=path)
    _git(["commit", "-m", "Initial commit"], cwd=path)


def _init_bare_remote(path: Path) -> None:
    """Create a bare repo to act as a remote."""
    _git(["init", "--bare"], cwd=path)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """A temporary git repository with one commit and an origin remote."""
    repo = tmp_path / "repo"
    repo.mkdir()
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _init_bare_remote(remote)
    _init_repo(repo)
    _git(["remote", "add", "origin", str(remote)], cwd=repo)
    _git(["push", "-u", "origin", "main"], cwd=repo)
    return repo


@pytest.fixture()
def git_repo_no_remote(tmp_path: Path) -> Path:
    """A temporary git repository with no remote."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    return repo


class TestGetBranchStatus:
    def test_no_commits_ahead(self, git_repo: Path) -> None:
        status = get_branch_status(git_repo)
        assert status.branch == "main"
        assert status.has_remote is True
        assert status.ahead_count == 0
        assert status.behind_count == 0
        assert status.commits == []
        assert status.remote_branch is not None

    def test_commits_ahead(self, git_repo: Path) -> None:
        (git_repo / "a.txt").write_text("a\n")
        _git(["add", "a.txt"], cwd=git_repo)
        _git(["commit", "-m", "Add a"], cwd=git_repo)
        (git_repo / "b.txt").write_text("b\n")
        _git(["add", "b.txt"], cwd=git_repo)
        _git(["commit", "-m", "Add b"], cwd=git_repo)

        status = get_branch_status(git_repo)
        assert status.ahead_count == 2
        assert len(status.commits) == 2
        assert status.commits[0].message == "Add b"
        assert status.commits[1].message == "Add a"

    def test_no_remote(self, git_repo_no_remote: Path) -> None:
        status = get_branch_status(git_repo_no_remote)
        assert status.branch == "main"
        assert status.has_remote is False
        assert status.remote_branch is None

    def test_no_upstream_but_has_remote(self, tmp_path: Path) -> None:
        """Branch exists, origin remote exists, but no upstream tracking."""
        repo = tmp_path / "repo"
        repo.mkdir()
        remote = tmp_path / "remote.git"
        remote.mkdir()
        _init_bare_remote(remote)
        _init_repo(repo)
        _git(["remote", "add", "origin", str(remote)], cwd=repo)
        # Don't push — so no upstream is configured

        status = get_branch_status(repo)
        assert status.has_remote is True
        assert status.remote_branch is None
        # All commits are "ahead" since there's no remote tracking ref
        assert status.ahead_count >= 1


class TestPushToRemote:
    def test_push_to_upstream(self, git_repo: Path) -> None:
        (git_repo / "c.txt").write_text("c\n")
        _git(["add", "c.txt"], cwd=git_repo)
        _git(["commit", "-m", "Add c"], cwd=git_repo)

        result = push_to_remote(git_repo)
        assert result.success is True
        assert result.error is None
        assert result.pushed_commits == 1

        # Verify ahead count is now 0
        status = get_branch_status(git_repo)
        assert status.ahead_count == 0

    def test_push_to_new_branch(self, git_repo: Path) -> None:
        (git_repo / "d.txt").write_text("d\n")
        _git(["add", "d.txt"], cwd=git_repo)
        _git(["commit", "-m", "Add d"], cwd=git_repo)

        result = push_to_remote(git_repo, target_branch="feature/test")
        assert result.success is True
        assert result.remote_branch == "origin/feature/test"

    def test_push_no_remote(self, git_repo_no_remote: Path) -> None:
        result = push_to_remote(git_repo_no_remote)
        assert result.success is False
        assert result.error is not None
        assert "origin" in result.error.lower() or "remote" in result.error.lower()

    def test_push_no_upstream_sets_upstream(self, tmp_path: Path) -> None:
        """Push with no upstream should push to origin/<branch>."""
        repo = tmp_path / "repo"
        repo.mkdir()
        remote = tmp_path / "remote.git"
        remote.mkdir()
        _init_bare_remote(remote)
        _init_repo(repo)
        _git(["remote", "add", "origin", str(remote)], cwd=repo)

        result = push_to_remote(repo)
        assert result.success is True
        assert "origin/main" in result.remote_branch


