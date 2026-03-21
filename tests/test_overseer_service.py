"""Tests for OverseerService — God Mode lifecycle and handover parsing."""

from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from overdrive.runtime.overseer.service import OverseerService, OverseerState
from overdrive.runtime.storage.container import Container
from overdrive.runtime.events.bus import EventBus


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("# init\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _make_service(tmp_path: Path) -> tuple[OverseerService, Container]:
    _git_init(tmp_path)
    container = Container(tmp_path)
    bus = EventBus(container.events, container.project_id)
    service = OverseerService(container, bus)
    return service, container


class TestOverseerState:
    def test_roundtrip(self) -> None:
        state = OverseerState(objective="improve tests", advice=["be thorough"])
        data = state.to_dict()
        restored = OverseerState.from_dict(data)
        assert restored.objective == "improve tests"
        assert restored.advice == ["be thorough"]

    def test_defaults(self) -> None:
        state = OverseerState()
        assert state.status == "idle"
        assert state.objective == ""
        assert state.iteration == 0


class TestHandoverParsing:
    """Test _extract_handover_json with various output formats."""

    @pytest.fixture()
    def service(self, tmp_path: Path) -> OverseerService:
        svc, _ = _make_service(tmp_path)
        return svc

    def test_plain_json(self, service: OverseerService) -> None:
        text = 'Some output\n{"status": "completed", "summary": "All done"}'
        result = service._extract_handover_json(text)
        assert result is not None
        assert result["status"] == "completed"
        assert result["summary"] == "All done"

    def test_fenced_json(self, service: OverseerService) -> None:
        text = textwrap.dedent("""\
            I finished the work.

            ```json
            {"status": "continue", "context": "Next step", "progress": "Done so far"}
            ```
        """)
        result = service._extract_handover_json(text)
        assert result is not None
        assert result["status"] == "continue"
        assert result["context"] == "Next step"

    def test_continue_after_delay(self, service: OverseerService) -> None:
        text = '{"status": "continue-after-delay", "delay_seconds": 120, "context": "waiting"}'
        result = service._extract_handover_json(text)
        assert result is not None
        assert result["status"] == "continue-after-delay"
        assert result["delay_seconds"] == 120

    def test_blocked(self, service: OverseerService) -> None:
        text = '{"status": "blocked", "reason": "Need GITHUB_TOKEN"}'
        result = service._extract_handover_json(text)
        assert result is not None
        assert result["status"] == "blocked"

    def test_no_json(self, service: OverseerService) -> None:
        result = service._extract_handover_json("no json here")
        assert result is None

    def test_json_without_status(self, service: OverseerService) -> None:
        result = service._extract_handover_json('{"foo": "bar"}')
        assert result is None

    def test_stream_json_format(self, service: OverseerService) -> None:
        """Claude --output-format stream-json wraps response in NDJSON."""
        ndjson = (
            '{"type":"assistant","message":{"content":[{"type":"text","text":"'
            '{\\"status\\": \\"completed\\", \\"summary\\": \\"All done\\"}"}]}}\n'
        )
        result = service._extract_handover_json(ndjson)
        assert result is not None
        assert result["status"] == "completed"


class TestServiceLifecycle:
    def test_start_creates_running_state(self, tmp_path: Path) -> None:
        service, _ = _make_service(tmp_path)
        # Don't actually launch — just verify state setup
        with patch.object(service, "_run_loop"):
            state = service.start("improve everything")
        assert state.status == "running"
        assert state.objective == "improve everything"
        assert state.started_at is not None

    def test_start_with_advice(self, tmp_path: Path) -> None:
        service, _ = _make_service(tmp_path)
        with patch.object(service, "_run_loop"):
            state = service.start("improve", ["don't touch prod", "prefer small PRs"])
        assert len(state.advice) == 2

    def test_double_start_raises(self, tmp_path: Path) -> None:
        import threading
        service, _ = _make_service(tmp_path)
        hold = threading.Event()
        original_run_loop = service._run_loop
        service._run_loop = lambda: hold.wait(timeout=5)  # type: ignore[assignment]
        try:
            service.start("first")
            with pytest.raises(RuntimeError, match="already running"):
                service.start("second")
        finally:
            hold.set()
            service.stop()

    def test_stop(self, tmp_path: Path) -> None:
        service, _ = _make_service(tmp_path)
        with patch.object(service, "_run_loop"):
            service.start("test")
        state = service.stop()
        assert state.status == "stopped"
        assert state.finished_at is not None

    def test_add_remove_advice(self, tmp_path: Path) -> None:
        service, _ = _make_service(tmp_path)
        service.add_advice("first")
        service.add_advice("second")
        assert len(service.get_state().advice) == 2
        service.remove_advice(0)
        assert service.get_state().advice == ["second"]

    def test_unblock_when_not_blocked_raises(self, tmp_path: Path) -> None:
        service, _ = _make_service(tmp_path)
        with pytest.raises(RuntimeError, match="not blocked"):
            service.unblock("here's the key")


class TestPersistence:
    def test_state_persists_across_instances(self, tmp_path: Path) -> None:
        service1, container = _make_service(tmp_path)
        service1._state.objective = "persist me"
        service1._state.iteration = 5
        service1._persist_state()

        bus = EventBus(container.events, container.project_id)
        service2 = OverseerService(container, bus)
        assert service2.get_state().objective == "persist me"
        assert service2.get_state().iteration == 5


class TestMemoryDir:
    def test_memory_dir_created(self, tmp_path: Path) -> None:
        service, _ = _make_service(tmp_path)
        assert service._memory_dir.exists()
        assert service._memory_dir.is_dir()
        assert "overseer" in str(service._memory_dir)
        assert "memory" in str(service._memory_dir)

    def test_runs_dir_created(self, tmp_path: Path) -> None:
        service, _ = _make_service(tmp_path)
        assert service._runs_dir.exists()


class TestPromptBuild:
    def test_prompt_contains_objective(self, tmp_path: Path) -> None:
        service, _ = _make_service(tmp_path)
        service._state.objective = "make tests faster"
        service._state.advice = ["use pytest-xdist"]
        prompt = service._build_prompt(None)
        assert "make tests faster" in prompt
        assert "use pytest-xdist" in prompt
        assert str(service._memory_dir) in prompt

    def test_prompt_includes_handover(self, tmp_path: Path) -> None:
        service, _ = _make_service(tmp_path)
        service._state.objective = "test"
        handover = {"status": "continue", "context": "was working on X"}
        prompt = service._build_prompt(handover)
        assert "was working on X" in prompt
