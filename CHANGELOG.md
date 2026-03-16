# Changelog

## [0.2.2] - 2026-03-16

### Added
- Auto-detected defaults display in Settings (languages, venv, project commands, environment variables)
- Review PR/MR tab in Create Work modal with selectable PR list and review guidance
- Icon buttons for browse toolbar and modal close buttons

### Changed
- Plan/workdoc font switched from monospace to sans-serif for readability
- Plan/workdoc heading sizes tamed and margins tightened
- Markdown table formatting instructions added to plan and review prompts
- Runtime metrics cleaned up: removed misleading api_calls, files_changed, steps counts
- Error banners redesigned with boxed style and dismiss button
- Acronyms (PR, MR, API, HITL, PRD) preserved in humanizeLabel
- Homepage screenshot regenerated with current UI

### Fixed
- Default provider persistence: selecting claude or ollama no longer silently reverts to codex
- Plan/workdoc tabs now update on WebSocket refreshes (previously stale for PR/MR reviews)
- HTML comment markers stripped from workdoc display
- Duplicate Plan heading eliminated from review prompt output
- Status card alignment standardized to left-align
- Detail tab-to-content gap reduced

## [0.2.1] - 2026-03-16

### Added
- Dark mode with three-way toggle (light / dark / system) and green-tinted dark neutrals
- Flash prevention: theme applied before first paint via inline script

### Changed
- Navigation uses underline-style active tab indicator instead of filled pills
- Moved Create Work button from topbar to board toolbar for clearer action grouping
- Dependency policy selector converted to segmented toggle control
- Execution control labels shortened for compact layout (Pause, Start, Drain, Stop)
- Board summary streamlined: removed redundant queue/worker counts
- Provider recheck button redesigned to match global icon button style
- Responsive layout improvements for topbar, board summary, and execution controls

## [0.2.0] - 2026-03-16

### Added
- LLM-generated recommended actions for blocked tasks with concrete recovery suggestions
- Task-level worker provider override for retrying blocked tasks with a different provider
- Merge and pull request review pipelines
- LLM-generated summaries at gate junctures (review cycles, completion)
- Initiative intent preservation through task generation
- Worker environment variable configuration with 4-layer resolution (auto/process/config/task)
- Virtual environment auto-detection for Python projects
- Default project commands configuration in Settings

### Changed
- Consolidated Workers panel into Settings with 3-tab layout (Providers, Execution, Advanced)
- Hybrid save UX: auto-save for toggles/dropdowns, dirty-state save buttons for text/numeric fields
- Replaced HITL toggle buttons with compact dropdown selector
- Default HITL mode changed to supervised
- Improved worker stall detection with defense-in-depth recovery
- Faster worker cancellation on task stop
- Simplified post-merge health check (alert instead of revert)
- Collapsed file changes by default in task detail view

### Fixed
- Report step not capturing summary after review cycle
- `implement_fix` retry bypassing verify when pipeline phase is missing
- Supervised gate skipped after `request_changes` on commit review tasks
- Partially completed pipelines incorrectly marked as done
- Default task timeout rejecting 0 (no timeout)
- Task detail modal refreshing unnecessarily
- Plan tab not rendering for some pipeline types

### Removed
- Standalone Workers route (consolidated into Settings)
- Diagnostics section from Settings

## [0.1.0] - 2026-02-28
### Added
- Initial public release.
