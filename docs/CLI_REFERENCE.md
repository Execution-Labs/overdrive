# CLI Reference

Executable:

```bash
overdrive
```

The CLI is a local operational interface for server startup, project pinning,
task creation/list/run, and orchestrator control.

## Global Options

- `--project-dir PATH`: target project directory for state and task operations.
  If omitted, the current working directory is used.

## Exit Behavior

- Successful commands return exit code `0`.
- Invalid inputs or runtime errors return non-zero exit codes.
- Command output is JSON to stdout for machine-friendly scripting.

## Server

Start the FastAPI server:

```bash
overdrive [--project-dir PATH] server [--host 127.0.0.1] [--port 8080] [--reload]
```

Options:
- `--host` (default `127.0.0.1`)
- `--port` (default `8080`)
- `--reload` (dev autoreload)

If server extras are missing, install with:

```bash
python -m pip install -e .
```

## Project Commands

### `project pin`
Pin a repository path for quick selection in API/UI.

```bash
overdrive [--project-dir PATH] project pin /absolute/path/to/repo [--project-id ID] [--allow-non-git]
```

Notes:
- Path must exist and be a directory.
- By default path must contain `.git`.
- Use `--allow-non-git` to pin non-git directories.

### `project list`
List pinned projects from config.

```bash
overdrive [--project-dir PATH] project list
```

### `project unpin`
Remove a pinned project by id.

```bash
overdrive [--project-dir PATH] project unpin <project_id>
```

## Task Commands

### `task create`
Create a task in `backlog` status.

```bash
overdrive [--project-dir PATH] task create "Title" [--description TEXT] [--priority P0|P1|P2|P3] [--task-type TYPE]
```

Defaults:
- `--description ""`
- `--priority P2`
- `--task-type feature`

### `task list`
List tasks, optionally filtered by status.

```bash
overdrive [--project-dir PATH] task list [--status STATUS]
```

Common statuses:
- `backlog`, `queued`, `in_progress`, `in_review`, `done`, `blocked`, `cancelled`

### `task run`
Run a task by id via orchestrator.

```bash
overdrive [--project-dir PATH] task run <task_id>
```

## Orchestrator Commands

### `orchestrator status`
Show orchestrator runtime status.

```bash
overdrive [--project-dir PATH] orchestrator status
```

### `orchestrator control`
Change orchestrator control state.

```bash
overdrive [--project-dir PATH] orchestrator control pause
overdrive [--project-dir PATH] orchestrator control resume
overdrive [--project-dir PATH] orchestrator control drain
overdrive [--project-dir PATH] orchestrator control stop
overdrive [--project-dir PATH] orchestrator control reset
overdrive [--project-dir PATH] orchestrator control reconcile
```

Allowed actions:
- `pause`
- `resume`
- `drain`
- `stop`
- `reset`
- `reconcile`

## Practical Examples

Create and run a docs task in a repo:

```bash
overdrive --project-dir /path/to/repo task create "Document dependency policy" --task-type docs --priority P1
overdrive --project-dir /path/to/repo task list --status backlog
overdrive --project-dir /path/to/repo task run <task_id>
```

Pin multiple projects for the dashboard project selector:

```bash
overdrive project pin ~/repos/service-a
overdrive project pin ~/repos/service-b
overdrive project list
```
