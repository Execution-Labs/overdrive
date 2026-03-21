"""OverseerService — launch, monitor, and relaunch the autonomous God Mode agent."""
# ruff: noqa: D102

from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..domain.models import _id, now_iso
from ..events.bus import EventBus
from ..storage.container import Container

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class OverseerState:
    """Persisted overseer session state."""

    id: str = field(default_factory=lambda: _id("ovs"))
    status: str = "idle"  # idle | running | completed | blocked | stopped
    objective: str = ""
    advice: list[str] = field(default_factory=list)
    last_handover: Optional[dict[str, Any]] = None
    iteration: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    blocked_reason: Optional[str] = None
    human_response: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OverseerState:
        return cls(
            id=str(data.get("id") or _id("ovs")),
            status=str(data.get("status", "idle")),
            objective=str(data.get("objective", "")),
            advice=list(data.get("advice") or []),
            last_handover=data.get("last_handover"),
            iteration=int(data.get("iteration", 0)),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            blocked_reason=data.get("blocked_reason"),
            human_response=data.get("human_response"),
            error=data.get("error"),
        )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

_DEFAULT_COMMAND = "claude -p --dangerously-skip-permissions --verbose --output-format stream-json"
_DEFAULT_TICK_INTERVAL = 5  # seconds between loop iterations (not agent ticks)


class OverseerService:
    """Manage the God Mode agent lifecycle."""

    def __init__(self, container: Container, bus: EventBus) -> None:
        self._container = container
        self._bus = bus

        self._state = OverseerState()

        # Directories
        self._overseer_root = Path(container.state_root) / "overseer"
        self._memory_dir = self._overseer_root / "memory"
        self._runs_dir = self._overseer_root / "runs"
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._runs_dir.mkdir(parents=True, exist_ok=True)

        # Thread control
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._unblock = threading.Event()
        self._process: Optional[subprocess.Popen[str]] = None
        self._process_lock = threading.Lock()

        # Load persisted state if any
        self._load_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, objective: str, advice: list[str] | None = None) -> OverseerState:
        """Start God Mode with the given objective."""
        if self._thread and self._thread.is_alive():
            raise RuntimeError("Overseer is already running")

        self._state = OverseerState(
            status="running",
            objective=objective,
            advice=list(advice or []),
            started_at=now_iso(),
        )
        self._persist_state()
        self._stop.clear()
        self._unblock.clear()

        self._thread = threading.Thread(
            target=self._run_loop,
            name="overseer-loop",
            daemon=True,
        )
        self._thread.start()
        self._emit("overseer.started", {"objective": objective})
        return self._state

    def stop(self) -> OverseerState:
        """Stop God Mode."""
        self._stop.set()
        self._unblock.set()  # unblock if waiting

        # Kill the running agent process
        with self._process_lock:
            if self._process and self._process.poll() is None:
                try:
                    self._process.kill()
                except OSError:
                    pass

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=15.0)

        self._state.status = "stopped"
        self._state.finished_at = now_iso()
        self._persist_state()
        self._emit("overseer.stopped")
        return self._state

    def get_state(self) -> OverseerState:
        """Return current state."""
        return self._state

    def add_advice(self, text: str) -> OverseerState:
        """Add advice for the agent."""
        self._state.advice.append(text)
        self._persist_state()
        return self._state

    def remove_advice(self, index: int) -> OverseerState:
        """Remove advice by index."""
        if 0 <= index < len(self._state.advice):
            self._state.advice.pop(index)
            self._persist_state()
        return self._state

    def unblock(self, response: str) -> OverseerState:
        """Provide a human response to unblock the agent."""
        if self._state.status != "blocked":
            raise RuntimeError("Overseer is not blocked")
        self._state.human_response = response
        self._state.status = "running"
        self._persist_state()
        self._unblock.set()
        self._emit("overseer.unblocked", {"response": response})
        return self._state

    def shutdown(self, timeout: float = 10.0) -> None:
        """Graceful shutdown for server lifespan."""
        self._stop.set()
        self._unblock.set()
        with self._process_lock:
            if self._process and self._process.poll() is None:
                try:
                    self._process.kill()
                except OSError:
                    pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Main loop: launch agent, read handover, decide what to do."""
        handover = self._state.last_handover

        while not self._stop.is_set():
            self._state.iteration += 1
            self._state.status = "running"
            self._state.error = None
            self._persist_state()
            self._emit("overseer.iteration_started", {"iteration": self._state.iteration})

            try:
                result = self._launch_agent(handover)
            except Exception as exc:
                logger.exception("Overseer agent launch failed")
                self._state.error = str(exc)
                self._state.status = "stopped"
                self._persist_state()
                self._emit("overseer.error", {"error": str(exc)})
                return

            status = result.get("status", "continue")
            handover = result
            self._state.last_handover = result

            if status == "completed":
                self._state.status = "completed"
                self._state.finished_at = now_iso()
                self._persist_state()
                self._emit("overseer.completed", {"summary": result.get("summary", "")})
                return

            if status == "blocked":
                self._state.status = "blocked"
                self._state.blocked_reason = result.get("reason", "Unknown")
                self._persist_state()
                self._emit("overseer.blocked", {"reason": self._state.blocked_reason})
                # Wait for human unblock or stop
                self._unblock.wait()
                if self._stop.is_set():
                    return
                self._unblock.clear()
                # Inject human response into handover
                handover = {
                    **result,
                    "human_response": self._state.human_response,
                }
                self._state.human_response = None
                continue

            if status == "continue-after-delay":
                delay = int(result.get("delay_seconds", 60))
                self._persist_state()
                self._emit("overseer.waiting", {
                    "iteration": self._state.iteration,
                    "delay_seconds": delay,
                    "reason": result.get("context", ""),
                })
                self._stop.wait(timeout=delay)
                if self._stop.is_set():
                    return
                continue

            # status == "continue" — relaunch immediately
            self._persist_state()
            self._emit("overseer.iteration_completed", {
                "iteration": self._state.iteration,
                "progress": result.get("progress", ""),
            })

    # ------------------------------------------------------------------
    # Agent launch
    # ------------------------------------------------------------------

    def _launch_agent(self, handover: dict[str, Any] | None) -> dict[str, Any]:
        """Launch the LLM agent subprocess, wait for it to finish, parse handover."""
        prompt = self._build_prompt(handover)

        run_dir = self._runs_dir / f"iteration-{self._state.iteration}"
        run_dir.mkdir(parents=True, exist_ok=True)

        # Persist prompt for debugging
        (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")

        cmd = self._resolve_command()
        cmd_parts = shlex.split(cmd)
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"

        with self._process_lock:
            self._process = subprocess.Popen(
                cmd_parts,
                cwd=self._container.project_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=None,  # inherit parent env
            )
        proc = self._process

        # Write prompt to stdin
        try:
            if proc.stdin:
                proc.stdin.write(prompt)
                proc.stdin.close()
        except OSError:
            pass

        # Stream stdout/stderr to files in background threads
        def _stream(src: Any, dest: Path) -> None:
            try:
                with open(dest, "w", encoding="utf-8") as f:
                    for line in src:
                        f.write(line)
                        f.flush()
            except (ValueError, OSError):
                pass

        stdout_thread = threading.Thread(target=_stream, args=(proc.stdout, stdout_path), daemon=True)
        stderr_thread = threading.Thread(target=_stream, args=(proc.stderr, stderr_path), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        # Wait for process to finish (respecting stop signal)
        while proc.poll() is None:
            if self._stop.is_set():
                proc.kill()
                break
            time.sleep(1.0)

        stdout_thread.join(timeout=5.0)
        stderr_thread.join(timeout=5.0)

        exit_code = proc.poll() or -1

        # Read stdout and extract handover
        stdout_text = ""
        try:
            stdout_text = stdout_path.read_text(encoding="utf-8")
        except OSError:
            pass

        logger.info(
            "Overseer iteration %d finished (exit=%d, stdout=%d bytes)",
            self._state.iteration,
            exit_code,
            len(stdout_text),
        )

        handover_json = self._extract_handover_json(stdout_text)
        if handover_json is None:
            # Agent exited without explicit handover — treat as continue
            return {
                "status": "continue",
                "context": f"Agent exited (code={exit_code}) without handover JSON. Check logs at {run_dir}",
            }
        return handover_json

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(self, handover: dict[str, Any] | None) -> str:
        """Build the full prompt for the agent."""
        from ...prompts import load

        template = load("overseer.md")

        # Resolve API port from config or default
        cfg = self._container.config.load()
        port = cfg.get("server", {}).get("port", 8080)

        advice_section = ""
        if self._state.advice:
            advice_section = "\n".join(f"- {a}" for a in self._state.advice)
        else:
            advice_section = "(No advice provided.)"

        handover_section = ""
        if handover:
            handover_section = json.dumps(handover, indent=2)
        else:
            handover_section = "(First launch — no previous context.)"

        return template.format(
            objective=self._state.objective,
            advice_section=advice_section,
            handover_section=handover_section,
            memory_dir=str(self._memory_dir),
            port=port,
        )

    # ------------------------------------------------------------------
    # Handover JSON extraction
    # ------------------------------------------------------------------

    def _extract_handover_json(self, stdout_text: str) -> dict[str, Any] | None:
        """Extract the handover JSON from agent output.

        Supports both raw text output and Claude stream-json format.
        Looks for the last JSON object containing a "status" key.
        """
        # First, try to extract text from Claude stream-json format
        response_text = self._extract_stream_json_text(stdout_text)
        if not response_text:
            response_text = stdout_text

        # Find all JSON objects in the text
        # Look for ```json blocks first
        fenced = re.findall(r"```json\s*\n(.*?)\n\s*```", response_text, re.DOTALL)
        for block in reversed(fenced):
            try:
                obj = json.loads(block.strip())
                if isinstance(obj, dict) and "status" in obj:
                    return obj
            except json.JSONDecodeError:
                continue

        # Fall back to scanning for bare JSON objects
        for match in reversed(list(re.finditer(r"\{[^{}]*\"status\"[^{}]*\}", response_text))):
            try:
                obj = json.loads(match.group())
                if isinstance(obj, dict) and "status" in obj:
                    return obj
            except json.JSONDecodeError:
                continue

        return None

    @staticmethod
    def _extract_stream_json_text(raw: str) -> str:
        """Extract response text from Claude --output-format stream-json NDJSON."""
        parts: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Full assistant message
            if obj.get("type") == "assistant" and isinstance(obj.get("message"), dict):
                content = obj["message"].get("content", [])
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return str(block.get("text", ""))

            # Streaming deltas
            if obj.get("type") == "content_block_delta":
                delta = obj.get("delta", {})
                if delta.get("type") == "text_delta":
                    parts.append(str(delta.get("text", "")))

            # Result message (fallback)
            if obj.get("type") == "result":
                msg = obj.get("result", {})
                if isinstance(msg, dict):
                    content = msg.get("content", [])
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            return str(block.get("text", ""))

        if parts:
            return "".join(parts)
        return ""

    # ------------------------------------------------------------------
    # Command resolution
    # ------------------------------------------------------------------

    def _resolve_command(self) -> str:
        """Resolve the CLI command for the agent from config or defaults."""
        cfg = self._container.config.load()
        overseer_cfg = cfg.get("overseer", {})
        if isinstance(overseer_cfg, dict):
            cmd = overseer_cfg.get("command", "")
            if cmd:
                return str(cmd)

        # Try to use the project's claude provider config
        providers = cfg.get("workers", {}).get("providers", {})
        claude_cfg = providers.get("claude", {})
        if isinstance(claude_cfg, dict):
            base_cmd = claude_cfg.get("command", "claude -p")
            # Ensure we have the flags we need
            parts = shlex.split(base_cmd)
            if "--dangerously-skip-permissions" not in parts:
                parts.append("--dangerously-skip-permissions")
            if "--output-format" not in parts:
                parts.extend(["--output-format", "stream-json"])
            if "--verbose" not in parts:
                parts.append("--verbose")
            return shlex.join(parts)

        return _DEFAULT_COMMAND

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_state(self) -> None:
        self._container.db.save_overseer_state(self._state.to_dict())

    def _load_state(self) -> None:
        data = self._container.db.load_overseer_state()
        if data:
            self._state = OverseerState.from_dict(data)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _emit(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self._bus.emit(
            channel="overseer",
            event_type=event_type,
            entity_id=self._state.id,
            payload=payload or {},
        )
