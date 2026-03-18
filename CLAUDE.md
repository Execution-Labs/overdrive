# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Overdrive is a local orchestration control center for AI-assisted software delivery. It provides a task board, execution controls, review gates, and agent management. The backend is Python/FastAPI; the frontend is React/TypeScript with Vite.

## Development Commands

### Backend (Python)

```bash
# Activate venv
source .venv/bin/activate
# Install (editable, with server deps)
python -m pip install -e ".[server]"

# Run backend server
overdrive server --project-dir /path/to/repo

# Run all unit tests
pytest

# Run a single test file
pytest tests/test_event_hub.py

# Run a single test
pytest tests/test_event_hub.py::test_name -x

# Integration tests (skipped by default)
OVERDRIVE_RUN_INTEGRATION=1 pytest tests/test_integration_claude_provider.py

# Type checking
mypy

# Linting
ruff check src/
```

### Frontend (web/)

All frontend commands use `npm --prefix web` from the repo root, or run from `web/`.

```bash
npm --prefix web install
npm --prefix web run dev          # Dev server on :3000 (proxies /api to :8080)
npm --prefix web run build        # Production build (runs typecheck + contract check first)
npm --prefix web run lint         # TypeScript typecheck (tsc --noEmit)
npm --prefix web run test         # Vitest unit tests
npm --prefix web run test -- --reporter=verbose web/src/App.logs.test.tsx  # Single test file
npm --prefix web run check        # lint + test + build (full CI check)
npm --prefix web run e2e          # Playwright e2e (starts backend on :19080 + frontend on :19030)
npm --prefix web run e2e:smoke    # Smoke subset only
```

The build script enforces API contract checks (`check:mounted-api-contracts`) before compiling.

## Architecture

### Backend (`src/overdrive/`)

- **`runtime/orchestrator/service.py`** — Core `OrchestratorService`. Manages task queue, worker dispatch via `ThreadPoolExecutor`, state transitions, and review cycles. This is the central coordination point.
- **`runtime/orchestrator/task_executor.py`** — Task execution logic: runs pipeline steps, persists run/task state, handles retries.
- **`runtime/orchestrator/live_worker_adapter.py`** — `LiveWorkerAdapter` dispatches pipeline steps to real worker providers (Codex, Claude, Ollama). Contains formatter sub-calls for verify/review/summarize output parsing.
- **`runtime/orchestrator/worker_adapter.py`** — `WorkerAdapter` protocol + `DefaultWorkerAdapter` base.
- **`runtime/orchestrator/worktree_manager.py`** — Git worktree lifecycle (create, merge, cleanup) for task isolation.
- **`runtime/orchestrator/plan_manager.py`** — Plan revision management and refinement job orchestration.
- **`runtime/orchestrator/reconciler.py`** — Runtime reconciliation to repair inconsistent state after crashes.
- **`runtime/orchestrator/dependency_manager.py`** — Task dependency graph analysis and cycle detection.
- **`runtime/orchestrator/env_resolver.py`** — Worker environment variable resolution: auto-detection from project files, 4-layer merge (auto/process/config/task), and API display view.
- **`runtime/orchestrator/environment_preflight.py`** — Environment capability checks and auto-remediation before worker steps.
- **`runtime/api/`** — FastAPI routes, split across multiple files:
  - `router_impl.py` — Router factory and shared request/response schemas.
  - `routes_tasks.py` — Task CRUD and lifecycle endpoints.
  - `routes_agents.py` — Agent management endpoints.
  - `routes_collab.py` — Collaboration timeline and HITL endpoints.
  - `routes_projects.py` — Multi-project management endpoints.
  - `routes_terminal.py` — Terminal PTY session endpoints.
  - `routes_imports.py` — PRD import endpoints.
  - `routes_misc.py` — Metrics, phases, review, orchestrator control, settings, worker health.
  - `deps.py` — Shared FastAPI dependencies.
  - `logs_io.py` — Log streaming I/O helpers.
- **`runtime/domain/models.py`** — Dataclasses: `Task`, `ReviewCycle`, `RunRecord`, `PlanRevision`, `AgentRecord`, `TerminalSession`.
- **`runtime/storage/`** — SQLite-backed repositories (`runtime.db`). Legacy YAML repos exist for migration only.
- **`runtime/events/`** — `EventBus` + `WebSocketHub` for real-time pub/sub across channels (`tasks`, `queue`, `agents`, `review`, `terminal`).
- **`runtime/terminal/service.py`** — Terminal PTY session service for interactive shell access.
- **`server/api.py`** — FastAPI app factory with lifespan management.
- **`workers/`** — Worker provider configuration (`config.py`), execution (`run.py`), and diagnostics (`diagnostics.py`).
- **`worker.py`** — Top-level worker subprocess runner and `WorkerCancelledError`.
- **`pipelines/`** — Pipeline template registry for task execution workflows.
- **`collaboration/`** — HITL (Human-In-The-Loop) mode configs: autopilot, supervised, collaborative, review_only.
- **`prompts/`** — Prompt templates for worker steps and output formatters.
- **`cli.py`** — CLI entry point (`overdrive` command).

### Frontend (`web/src/`)

- **`App.tsx`** — Main component handling routing, WebSocket connection, and global state. This is a large monolithic file (~7.7k lines).
- **`api.ts`** — HTTP client with auth token handling and base URL construction.
- **`components/AppPanels/`** — Panel components (ImportJobPanel, TerminalPanel, TaskExplorerPanel).
- **`components/HITLModeSelector/`** — HITL mode selection UI.
- **`types/`** — Shared TypeScript type definitions.
- **`styles/`** — CSS with variables, base styles, and orchestrator-specific styles.

### Communication

- REST: Frontend calls `/api/*` endpoints, proxied by Vite dev server to backend on `:8080`.
- WebSocket: Frontend connects to `/ws` for real-time event streaming. Events are channel-based with sequence counters for ordering.

### Data Storage

All runtime state lives in `.overdrive/` within the target project directory, stored in a SQLite database (`runtime.db`). No external database required.

### Task Lifecycle

`backlog` → `queued` → `in_progress` → `in_review` → `done`

Tasks support dependency graphs (validated for cycles), parallel execution with configurable concurrency, and review cycles with severity-based findings.

## Key Conventions

- Backend source lives under `src/` (setuptools `package-dir` mapping).
- Python 3.10+ required. Strict mypy enabled.
- Frontend uses TypeScript strict mode. Tests use Vitest + React Testing Library; e2e uses Playwright.
- Proxy env vars: `VITE_API_PROXY_TARGET` (default `http://localhost:8080`), `VITE_WS_PROXY_TARGET`, `VITE_PORT` (default `3000`).
