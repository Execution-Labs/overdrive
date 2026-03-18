# User Guide

## Overview

Agent Orchestrator is a local AI delivery control center with task lifecycle
management, execution orchestration, review gates, and worker routing. It runs
entirely on your machine — no external services required beyond the AI worker
providers you configure.

The web UI is the primary interface. It is organized around three main views
accessible from the top navigation bar:

- **Board** — Kanban task board with drag-and-drop lifecycle management.
- **Execution** — Live orchestrator status, runtime metrics, and pipeline wave visualization.
- **Settings** — Provider configuration, execution defaults, and advanced tuning.

Additional surfaces available from any view:

- **Create Work** modal — Create tasks, import PRDs, or review pull/merge requests.
- **Task detail** modal — Deep-dive into any task (8 tabs).
- **Terminal panel** — Embedded project terminal (floating, bottom-right).
- **Project selector** — Switch between pinned repositories.
- **Theme toggle** — Light, dark, or system-default appearance.

## Quick Start

Start backend:

```bash
python -m pip install -e ".[server]"
agent-orchestrator server --project-dir /absolute/path/to/your/repo
```

Start frontend:

```bash
npm --prefix web install
npm --prefix web run dev
```

Open:
- Backend: `http://localhost:8080`
- Frontend: `http://localhost:3000`

## Core Concepts

- **Task**: unit of work with lifecycle state, priority, type, and pipeline.
- **Pipeline**: ordered steps chosen by task type (e.g., `plan → implement → verify → review → commit`).
- **Run**: one orchestrator execution attempt for a task.
- **Review queue**: tasks waiting for human decision after execution.
- **HITL mode**: collaboration style controlling which steps require human approval.
- **Project commands**: language-specific test/lint/typecheck/format commands injected into worker prompts.
- **Worker provider**: the AI backend that executes pipeline steps (Codex, Claude, or Ollama).

## Task Lifecycle

Statuses:
- `backlog` — not yet queued for execution.
- `queued` — ready to run when capacity is available.
- `in_progress` — currently being executed by a worker.
- `in_review` — execution complete, awaiting human review.
- `blocked` — paused due to errors, dependencies, or merge conflicts.
- `done` — approved and complete.
- `cancelled` — aborted.

Transition rules:
- `backlog → queued | cancelled`
- `queued → backlog | cancelled`
- `in_progress → cancelled`
- `in_review → done | blocked | cancelled`
- `blocked → queued | in_review | cancelled`
- `cancelled → backlog`

Constraint: a task cannot move to `queued` while any blocker is unresolved.
Blockers are resolved when dependent tasks reach `done` or `cancelled`.

Terminal-task cleanup:
- Tasks in `done` or `cancelled` can be permanently deleted from task detail.
- Non-terminal tasks cannot be deleted.

---

## The Web UI

### Navigation Bar

The top bar contains:

| Element | Description |
|---------|-------------|
| **Project selector** | Dropdown showing the current project path. Switch between pinned repositories or add new ones via the browse modal. |
| **Board / Execution / Settings** | Main view tabs. On mobile, these appear as a dropdown. |
| **Theme toggle** | Switch between light, dark, and system-default appearance. |

### Project Selector and Repository Browser

Click the project selector in the top-left to:
- Switch to another pinned project.
- Select **Add new repository** to open the **Browse Repositories** modal.

The browse modal provides:
- Navigation buttons (up, refresh, go).
- A path input field for direct navigation.
- A directory listing where git repositories are marked with a green indicator.
- An **Allow non-git directory** checkbox for projects without `.git`.
- A **Pin this folder** button to add the directory to your pinned list.

---

### Board View

The board displays tasks in a Kanban layout with seven columns:

| Column | Ordering |
|--------|----------|
| `backlog` | Priority (P0 first), then oldest created |
| `queued` | Priority (P0 first), then oldest created |
| `in_progress` | Priority (P0 first), then most recently updated |
| `in_review` | Priority (P0 first), then oldest updated |
| `blocked` | Priority (P0 first), then most recently updated |
| `done` | Most recently updated first |
| `cancelled` | Most recently updated first |

#### Board Toolbar

- **Compact view** checkbox — toggles between full and compact task cards.
- **More actions (···)** menu — contains **Clear All Tasks**.
- **Create Work (+)** button — opens the Create Work modal.

#### Task Cards

Each card shows:
- Task title.
- Priority badge and short task ID.

In full view (compact off), cards additionally show:
- First line of the description.
- A "from plan" indicator if the task was generated from a parent.
- Status badges: `Awaiting Approval` (blue) for tasks in review, `Needs Intervention` (red) for blocked tasks.

Click a card to open the **Task Detail** modal. Right-click a completed task for a context menu with **Review Commit**.

#### Board Summary

A summary section appears at the bottom when the dispatch queue is blocked or post-merge tests are failing. It shows the reason execution is stalled (e.g., queue paused, at concurrency limit, blocked by dependencies, post-merge test degradation).

#### Clear All Tasks

**Clear All Tasks** archives the existing `.agent_orchestrator/` directory to
`.agent_orchestrator_archive/state_<timestamp>/`, then initializes fresh
empty state. The UI shows the archive destination path after a successful clear.

---

### Create Work Modal

Open from the **+** button on the board toolbar. The modal has three tabs.

#### Tab 1: Create Task

| Field | Required | Description |
|-------|----------|-------------|
| **Title** | Yes | Short task name. |
| **Description** | No | Detailed task description (textarea). |
| **Task type** | No | Dropdown: `auto`, `feature`, `bug`, `refactor`, `chore`, `hotfix`, `research`, `spike`, `test`, `docs`, `review`, `commit_review`, `pr_review`, `mr_review`, `security`, `performance`, `verify_only`, `initiative_plan`. Defaults to `feature`. When `auto` is selected the system classifies the task from its title/description. |
| **HITL mode** | No | Collaboration mode selector (see [HITL Modes](#hitl-modes)). |
| **Project commands** | No | Per-task command overrides (textarea, YAML-like). |

Expand **Advanced** for additional fields:

| Field | Description |
|-------|-------------|
| **Priority** | Toggle: P0, P1, P2, P3. Default P2. |
| **Dependency policy** | Toggle: permissive, prudent, strict. |
| **Labels** | Comma-separated labels. |
| **Depends on** | Comma-separated task IDs this task is blocked by. |
| **Parent task ID** | ID of a parent task (for task hierarchies). |
| **Pipeline template** | Comma-separated step names to override the default pipeline. |
| **Task timeout** | Seconds limit for the implement step. |
| **Worker model** | Override the default model for this task. |
| **Worker provider** | Dropdown of available providers. |
| **Metadata** | Arbitrary JSON object (textarea). |

Footer buttons:
- **Create & Queue** — creates with status `queued` (starts execution when capacity is available).
- **Add to Backlog** — creates with status `backlog`.

#### Tab 2: Import PRD

Paste a Product Requirements Document into the text area and click **Preview**.
The preview shows:
- A graph of parsed nodes (tasks) and edges (dependencies).
- Chunk count and parsing strategy.
- Ambiguity warnings for unclear references.

Click **Commit to board** to create the tasks. This generates a parent task
(`initiative_plan` type) and child tasks with inferred dependencies.

The tab also shows **recent import jobs** with their status, letting you review
or re-open past imports.

#### Tab 3: Review PR/MR

Lists open pull requests (GitHub) or merge requests (GitLab) detected from the
project's git remote. Each entry shows:
- PR/MR number, title, author, and branch info (`base ← head`).
- A **Review exists** badge if a review task was already created.

Select a PR/MR, optionally add **review guidance** (e.g., "Focus on error
handling in the auth module"), and click **Create Review** to generate a review
task.

Requires the `gh` CLI (GitHub) or `glab` CLI (GitLab) to be installed and
authenticated.

---

### Task Detail Modal

Click any task card to open its detail modal. The header shows:
- Task title.
- Status pill with color coding.
- Pipeline flow visualization — all steps in the pipeline with the current step
  highlighted and completed steps marked.

The modal has **8 tabs**:

#### 1. Overview

Displays comprehensive task information:

**Metadata row**: task ID (click to copy), HITL mode, priority, task type,
latest commit hash (click to copy).

**Timing**: total execution time and a "running" indicator when active.

**Pending gate banner**: appears when the task is paused at a human approval
gate. Shows the gate name with action buttons:
- **Approve** — continue execution.
- **Request Changes** — send back with guidance.
- **Cancel** — abort the task.
- For the `before_generate_tasks` gate: also includes a quick status selector
  and HITL mode selector for generated children.

**Description**: full task description rendered as markdown.

**Dependencies**: lists blocking tasks with status pills. Shows resolved vs.
unresolved blockers.

**Generated from**: link to the parent task if this task was generated from a
plan.

**Generated tasks**: chips for each child task with status pill.

**Execution summary** (after completion or blocking):
- Summary prose from the worker.
- Collapsible **Step details** section with per-step results: status icon,
  step name, duration, finding counts by severity (critical/high/medium/low),
  commit hash, and summary text.

**Review history**: timeline of human review actions — approvals, change
requests, retries, manual merge finalizations — each with timestamp and
guidance text.

**Error section**: shows error message with recent stdout/stderr excerpts.

**Human blocking issues**: lists issues that require human intervention, with
summary, details, category, and severity.

#### 2. Plan

Visible when the task has a plan or is waiting at a plan gate. Three modes
accessible from the toolbar:

- **View** — read-only display of the current plan revision (markdown). A
  revision selector lets you browse earlier revisions showing source
  (`worker_plan`, `worker_refine`, `human_edit`, `import`) and status.
- **Edit** — textarea editor for direct plan editing. **Save & commit** persists
  your changes as a new human-authored revision.
- **Refine** — submit feedback to have a worker refine the plan asynchronously.
  Shows a progress indicator while refining and a completion banner when done.

#### 3. Workdoc

Displays the task's working document — a structured markdown journal that
tracks analysis findings, implementation decisions, and verification results
as the worker progresses through pipeline steps.

#### 4. Logs

Shows execution logs with step-level filtering:

- **Step selector** dropdown to choose which pipeline step's logs to view.
- **Run attempt** selector for steps that have been retried.
- **Tabbed output** panes for stdout and stderr.
- Full scrollable output with structured log event parsing.

#### 5. Activity

Collaboration timeline combining:
- System status change events.
- Human review actions and feedback.
- Threaded comments.
- Human blocking issue notifications.

Each entry shows timestamp, actor, and event details.

#### 6. Dependencies

Manages the task's dependency graph:

- **Incoming dependencies** (tasks this one blocks).
- **Outgoing dependencies** (tasks blocking this one) with status badges and
  remove buttons.
- **Analyze dependencies** button to run automated inference.
- Circular dependency detection and policy compliance checks.

#### 7. Configuration

View and edit task configuration (locked after execution starts):

- HITL mode selector.
- Priority toggle (P0–P3).
- Dependency policy toggle (permissive / prudent / strict).
- Task type dropdown.
- Worker provider and model overrides.
- Labels and metadata editors.

**Save** button persists changes.

#### 8. Changes

Shows code changes produced by the task:

- Unified diff view of file changes.
- File list with change counts.
- Confidence indicator and warnings for preserved-branch diffs reconstructed
  from legacy metadata.

---

### Task Actions

The task detail footer provides context-sensitive action buttons based on status:

| Status | Available Actions |
|--------|-------------------|
| `backlog` | **Queue** (move to queued), **Cancel** |
| `queued` | **Move to Backlog**, **Cancel** |
| `in_progress` | **Cancel** (stops execution) |
| `in_review` | **Approve** (mark done, merge if applicable), **Request Changes** (with optional guidance, returns to queued), **Cancel** |
| `blocked` | **Retry** (with optional guidance and step/provider selection), **Skip to Pre-commit Review** (if eligible), **Finalize Manual Merge** (if merge conflict), **Cancel** |
| `done` | **Review Commit** (creates a commit-review task), **Delete** |
| `cancelled` | **Move to Backlog** (resurrect), **Delete** |

For **blocked** tasks, the retry section includes:
- A guidance text field for directing the retry.
- A **Retry from step** dropdown (from beginning, or any specific pipeline step).
- A **Retry provider** dropdown to override the worker for this attempt.

---

### Execution View

The execution view provides real-time orchestrator monitoring.

#### Orchestrator Status and Controls

A status pill shows the current state:
- **Running** (green) — actively processing queue.
- **Draining** (orange) — finishing current tasks, not picking up new ones.
- **Paused** (gray) — queue processing suspended.
- **Stopped** (red) — orchestrator halted.

Control buttons:
- **Pause** — suspends queue processing (visible when running).
- **Start / Resume** — resumes processing (visible when paused).
- **Drain** — finishes in-progress tasks then pauses (visible when running).
- **Stop** — halts the orchestrator immediately.

#### Status Metrics Grid

Six cards showing live counts (click any to filter the wave view below):

| Card | Description |
|------|-------------|
| **Queue** | Tasks waiting to run. |
| **In Progress** | Tasks currently executing. |
| **Running now** | Tasks actively running (not waiting at gates). |
| **Waiting approval** | Tasks paused at approval gates. |
| **Needs intervention** | Tasks paused at intervention gates (errors). |
| **Blocked** | Tasks blocked by unresolved dependencies. |

#### Runtime Diagnostics

A diagnostics section shows:
- **Last tick** — timestamp of the last scheduler tick.
- **Tick lag** — seconds behind real time.
- **Tick failures** — consecutive failed ticks.
- **Last reconcile** — timestamp of last state repair.
- **Repairs** — count of reconciliation fixes.
- **Dispatch reason** — why the queue is blocked (if applicable).
- **Post-merge tests** — status with link to fix task when degraded.

#### Pipeline Wave Visualization

Tasks are organized into **waves** — groups that can execute in parallel based
on dependency ordering:

- Each wave card contains tasks that run concurrently.
- Task items show title (clickable), status pill, and visual separators.
- Clicking a status metric card above highlights matching tasks.
- A **Completed** section shows finished tasks (done/cancelled).

#### Runtime Metrics

A summary card showing:
- Tasks completed (count).
- Worker time (formatted duration).
- Tokens used.
- Estimated cost (USD, when available).

#### Worker Snapshot

Lists all in-progress tasks with:
- Task title and ID.
- Current pipeline step.
- Assigned worker provider.

---

### Settings View

Settings has three tabs: **Providers**, **Execution**, and **Advanced**.

#### Providers Tab

**Default Provider**

Shows all configured providers with:
- Provider name and model/command/endpoint details.
- Diagnostic status message and health badge (`connected`, `unavailable`, `not_configured`).
- A `Default` badge on the current default provider.
- Click a healthy provider row to set it as default.
- **Recheck providers** button to re-validate all providers.

**Provider Configuration**

Select a provider from the dropdown (`codex`, `claude`, `ollama`) and configure:

| Provider | Fields |
|----------|--------|
| **Codex** | Command (default: `codex exec`), model, execution mode (`sandboxed` / `host_access`), reasoning effort (`low` / `medium` / `high`). |
| **Claude** | Command (default: `claude -p`), model, execution mode (`host_access` / `sandboxed`), reasoning effort (`low` / `medium` / `high`). |
| **Ollama** | Endpoint (e.g., `http://localhost:11434`), model (e.g., `llama3.1:8b`), temperature, context window size (`num_ctx`). |

Click **Save** to persist the configuration.

**Step Routing**

A table mapping pipeline steps to specific providers. Each step has a dropdown
to select a provider or fall back to the default. Steps include: `plan`,
`implement`, `verify`, `review`, and others depending on pipeline templates.

An **Advanced** collapsible section provides JSON editors for the raw worker
routing map and provider overrides, with format/clear/save/reload controls.

#### Execution Tab

**Default HITL Mode**

A compact HITL mode picker showing the current mode, its description, and which
gates require approval (Plan, Implementation, Tasks, Review, Commit, Done).
Use the dropdown to change the default mode for new tasks.

**Dependency Policy**

Toggle group with three options:
- **Permissive** — prefer libraries, install as needed.
- **Prudent** — prefer existing, add new when necessary (default).
- **Strict** — no new dependencies allowed.

A description below the toggle explains the selected policy.

**Project Commands**

A textarea editor for language-specific commands that workers run during
implement and verify steps. Format:

```yaml
python:
  test: ".venv/bin/pytest -n auto --tb=short"
  lint: ".venv/bin/ruff check ."
  typecheck: ".venv/bin/mypy ."
  format: ".venv/bin/ruff format ."
typescript:
  test: "npm test"
  lint: "npx eslint ."
  typecheck: "npx tsc --noEmit"
```

Each language key supports `test`, `lint`, `typecheck`, and `format` commands.
Format/Clear buttons and a Save button are provided.

**Auto-detected Defaults**

When the system detects project tooling, this section shows what workers will
use when no explicit overrides are set:
- **Languages detected** — badges for each language found.
- **Python venv** — detected path and source (`auto` / `config`).
- **Project commands** — table of detected commands per language and step.
- **Environment variables** — table with variable name and source
  (`auto` / `process` / `config`).

Auto-detection sources include `.env` files, Prisma schemas, Docker Compose
files, and Python virtual environments. Explicit settings always take
precedence over auto-detected values.

**Quality Gate Thresholds**

Numeric inputs defining the maximum allowed unresolved findings per severity
before a review gate blocks:
- **Critical** — release-blocking issues.
- **High** — important issues.
- **Medium** — standard issues.
- **Low** — minor issues.

#### Advanced Tab

Contains additional settings for fine-grained control:

**Step Prompt Overrides**

Review default prompt text for each pipeline step and set per-step overrides.
Overrides replace only the step's instruction block; immutable preamble and
guardrails are always injected. Empty string removes an override.

**Step Prompt Injections**

Append project-specific instructions per step without replacing the built-in
prompt. Injections are additive — they are appended after the default prompt
for the specified step only. Empty string removes an injection.

**Orchestrator Settings**

- **Concurrency** — maximum parallel task execution.
- **Auto dependency analysis** — automatically infer task dependencies.
- **Max review attempts** — limit retry cycles before escalation.

**Agent Routing**

- **Default role** — the default agent role for all task types.
- **Task-type role mapping** — assign specific agent roles to task types.

---

### HITL Modes

HITL (Human-In-The-Loop) modes control which pipeline gates require human
approval before execution continues. Three built-in modes are available:

| Mode | Description | Approval Gates |
|------|-------------|----------------|
| **Autopilot** | No approvals. Agents run end-to-end automatically. Allows unattended execution. | None |
| **Supervised** | Approve the plan, then review implementation before commit. Requires reasoning from workers. | Plan, Implementation, Tasks, Commit, Done |
| **Review Only** | Skip plan approval. Review implementation before commit. Allows unattended plan execution. | Commit, Done |

When creating a task under a parent, an **Inherit parent** option uses the
parent task's HITL mode.

The HITL mode selector is available in:
- The **Create Task** form.
- The **Task Detail → Configuration** tab.
- The **Settings → Execution** tab (for the project-wide default).
- The pending gate banner (for `before_generate_tasks` gates).

Each mode shows its active gates as badges (Plan, Impl, Tasks, Review, Commit,
Done) so you can see at a glance which steps will pause for approval.

---

### Terminal Panel

A floating terminal panel in the bottom-right corner provides an embedded
interactive shell session for the active project.

Toggle it from the **Terminal** button in the app shell. The panel includes:
- **Clear** button — clears terminal output.
- **Restart** button — restarts the shell session.
- **Minimize** button — hides the panel.
- Full terminal emulator with 3000-line scrollback.
- Auto-reconnect on disconnect with log backfill.
- Responsive resize detection.

---

### Task Explorer Panel

The task explorer provides search and filter capabilities across all tasks:

- **Query input** — search by title or task ID.
- **Status dropdown** — filter by lifecycle status.
- **Type dropdown** — filter by task type.
- **Priority dropdown** — filter by P0–P3.
- **Only blocked** checkbox — show only blocked tasks.

Results appear as a paginated grid of task cards. Click any card to open its
detail modal. Pagination controls include prev/next buttons, page number, and
a per-page size selector (6 / 10 / 20).

---

## Pipeline Templates

Each task type maps to a pipeline template that defines its execution steps.
There are 18 built-in templates:

| Template | Task Types | Steps |
|----------|------------|-------|
| **Feature Implementation** | `feature` | plan → implement → verify → review → commit |
| **Bug Fix** | `bug` | diagnose → implement → verify → review → commit |
| **Refactoring** | `refactor` | analyze → plan → implement → verify → review → commit |
| **Hotfix** | `hotfix` | implement → verify → review → commit |
| **Chore** | `chore` | implement → verify → commit |
| **Documentation** | `docs` | analyze → implement → verify → review → commit |
| **Testing** | `test` | analyze → implement → verify → review → commit |
| **Research** | `research` | analyze |
| **Spike** | `spike` | analyze → prototype |
| **Code Review** | `review` | analyze → review |
| **Commit Review** | `commit_review` | commit_review → implement → verify → review → commit |
| **PR Review** | `pr_review` | pr_review → implement → verify → review → commit |
| **MR Review** | `mr_review` | mr_review → implement → verify → review → commit |
| **Repository Review** | `repo_review` | analyze → initiative_plan → generate_tasks |
| **Initiative Plan** | `initiative_plan` | analyze → initiative_plan → generate_tasks |
| **Security Audit** | `security` | scan_deps → scan_code → generate_tasks |
| **Performance Optimization** | `performance` | profile → plan → implement → benchmark → review → commit |
| **Verify Only** | `verify_only` | verify |

When creating a task with type `auto`, the system classifies the task from its
title and description and selects the appropriate pipeline. You can also
override the pipeline by specifying custom steps in the advanced section of the
Create Task form.

---

## Typical Workflows

### 1. Create and run a task

1. Open **Create Work → Create Task**.
2. Enter title, description, and select a task type.
3. Choose a HITL mode (or keep the project default).
4. Click **Create & Queue** to start immediately, or **Add to Backlog** to defer.
5. Track step progression in the task detail modal and the **Execution** view.
6. When status becomes `in_review`, open the task and **Approve** or **Request Changes**.

### 2. Import a PRD into executable tasks

1. Open **Create Work → Import PRD**.
2. Paste PRD content and click **Preview**.
3. Review the preview graph — nodes (tasks), edges (dependencies), and any ambiguity warnings.
4. Click **Commit to board**.
5. Monitor the generated parent task (`initiative_plan`) and its children on the board.

### 3. Review a pull request or merge request

1. Open **Create Work → Review PR/MR**.
2. Select a PR/MR from the list of open items.
3. Optionally add review guidance.
4. Click **Create Review** to generate a review task.
5. The review task runs through `pr_review → implement → verify → review → commit`, fixing any issues found.

### 4. Use the embedded terminal

1. Click **Terminal** in the app shell to toggle the floating panel.
2. A shell session starts automatically in the project directory.
3. Use it for ad-hoc commands, inspecting files, or running manual tests.
4. Restart or clear the session as needed from the panel header.

### 5. Retry a blocked task

1. Open the blocked task from the board.
2. In the footer, enter **retry guidance** describing what to fix.
3. Optionally select a specific step to **retry from** and a different **provider**.
4. Click **Retry**.

### 6. Handle a merge conflict

1. When a task is blocked at the `commit` step with a merge conflict, the task detail shows the conflict state.
2. Resolve the conflict manually in your terminal (or the embedded terminal).
3. Return to the task detail and click **Finalize Manual Merge**.
4. The system verifies Git has no unresolved index entries and marks the task done.

---

## Review Flow

The review queue includes all tasks in `in_review` status.

Actions:
- **Approve**: marks the task `done`. If a preserved branch exists, merge is
  attempted first.
- **Request Changes**: returns the task to `queued` with retry targeting the
  `implement` step. Optional guidance is recorded.

Both actions are recorded in the task's review history with timestamps.

---

## Settings via API

All settings shown in the UI can also be managed via the REST API.

### Read settings

```bash
curl http://localhost:8080/api/settings
```

### Patch settings

```bash
curl -X PATCH http://localhost:8080/api/settings \
  -H 'Content-Type: application/json' \
  -d '{
    "project": {
      "commands": {
        "python": {
          "test": ".venv/bin/pytest -n auto",
          "lint": ".venv/bin/ruff check ."
        }
      }
    }
  }'
```

Empty string values remove specific entries:
- In `project.commands`: removes a command.
- In `project.prompt_overrides`: removes a step override.
- In `project.prompt_injections`: removes a step injection.

---

## Worker Providers

Three provider types are supported:

| Provider | Type | Key Fields |
|----------|------|------------|
| **Codex** | CLI-based | `command` (default: `codex exec`), `model`, `execution_mode` (`sandboxed` / `host_access`), `reasoning_effort` |
| **Claude** | CLI-based | `command` (default: `claude -p`), `model`, `execution_mode` (`host_access` / `sandboxed`), `reasoning_effort` |
| **Ollama** | HTTP-based | `endpoint`, `model`, `temperature`, `num_ctx` |

Routing behavior:
1. Step-level routing uses explicit `workers.routing` mappings first.
2. Falls back to `workers.default` provider.
3. Task-level provider overrides take precedence over both.

Check provider health at any time with the **Recheck providers** button in
Settings → Providers, or via `GET /api/workers/health`.

---

## Runtime Storage

State root in each project directory:
- `.agent_orchestrator/runtime.db` — canonical runtime state store (SQLite).
- `.agent_orchestrator/workdocs/<task_id>.md` — canonical task working documents.
- `.agent_orchestrator_archive/state_<timestamp>/` — archived snapshots from clear/reset operations.

Legacy migration: incompatible old state is archived to `.agent_orchestrator_legacy_<timestamp>/`.

---

## Diagnostics and Troubleshooting

Health checks:
- `GET /healthz` — liveness probe.
- `GET /readyz` — readiness probe with project and cache details.

Context checks:
- `GET /` — confirms active project and schema version.
- `GET /api/settings` — confirms effective runtime configuration.
- `GET /api/workers/health` — validates provider availability.

Task diagnostics:
- `GET /api/tasks/{task_id}/logs` — stdout/stderr and step history.
- `GET /api/tasks/{task_id}/changes` — code changes with diff.
- `GET /api/collaboration/timeline/{task_id}` — review and blocker context.

Runtime diagnostics (in the Execution view):
- Scheduler tick lag and failure counts.
- Reconciliation status and repair counts.
- Dispatch blocking reasons.
- Post-merge test status.

---

## Additional References

- `README.md` — product overview and quick start.
- `docs/API_REFERENCE.md` — full HTTP and WebSocket API contract.
- `docs/CLI_REFERENCE.md` — command-line reference.
- `web/README.md` — frontend development setup.
- `example/README.md` — example assets and local sandbox walkthrough.
