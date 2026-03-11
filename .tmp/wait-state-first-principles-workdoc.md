# Wait-State First Principles Rewire

## Objective
Eliminate contradictory task states where auto-recoverable failures surface as `Needs Intervention`, and prevent execution from continuing after true human-blocking transitions.

## Invariants (authoritative)
1. `wait_state.kind=auto_recovery_wait` => task must not be in `human_intervention` gate.
2. `wait_state.kind=intervention_wait` => task is terminally blocked until explicit human action.
3. Step execution outcomes are explicit (`ok`, `verify_failed`, `verify_degraded`, `auto_requeued`, `human_blocked`, `blocked`) and callers branch on outcome, never metadata side channels.
4. UI `Needs Intervention` is keyed off authoritative wait state, not inferred from stale gate fragments.

## Plan
- [x] Phase 1: Introduce `Task.wait_state` model + normalization.
- [x] Phase 2: Add service helpers to set/clear wait state; wire into gate wait, human block, auto recovery, retries.
- [x] Phase 3: Replace boolean `_run_non_review_step` contract with typed outcome and update executor branches.
- [x] Phase 4: API payload normalization + UI consumption of wait state.
- [x] Phase 5: Tests (backend + UI) and regression pass.

## Progress Log
- 2026-03-05: Created workdoc.
- 2026-03-05: Completed Phase 1-4 implementation draft.
  - Added `Task.wait_state` persistence field.
  - Added wait-state normalization in API payload (`wait_state` + gate_context derivation).
  - Added explicit wait-state setters/clearers in orchestrator service.
  - Reworked `_run_non_review_step` to return explicit outcomes.
  - Updated executor to branch on explicit outcomes (removed metadata side-channel branching).
  - Added invariant repairs for stale wait-state combinations.
  - Updated UI waiting-kind logic to prioritize authoritative `wait_state`.
- 2026-03-05: Completed Phase 5 verification.
  - `.venv/bin/pytest -n auto` => 637 passed, 4 skipped.
  - `npm --prefix web run build` => success.

## Review/Fix Loop
1. Finding: `_run_non_review_step` still returned legacy boolean values in two branches (`generate_tasks` empty-output, scope violation).
   - Impact: executor interpreted `True` as unknown outcome and returned early, leaving tasks stuck `in_progress`.
   - Fix: normalized all returns to typed outcomes (`"ok"`/`"blocked"`).
2. Finding: tests assumed boolean return contract from `_run_non_review_step`.
   - Impact: regression failures in pipeline-dispatch and workdoc tests.
   - Fix: updated assertions to explicit outcome strings.
3. Final review status: no remaining issues found in implemented scope.
