Implement the task completely and safely using the working document as the source of truth.

Core rules:
- Complete the step end-to-end; do not leave partial work.
- Do not leave stubs, TODOs, placeholder comments, empty bodies, or pass-through no-ops.
- Solve from first principles; avoid shortcut, band-aid, or hacky patch fixes.
- Preserve existing behavior unless the task explicitly requires changing it.
- Keep changes minimal and targeted; avoid unrelated refactors.
- Never undo or overwrite unrelated changes in the repository.

Execution requirements:
- Read `.workdoc.md` and implement against the `## Plan` scope.
- Update `## Implementation Log` with completed work, key decisions, and justified deviations.
- Keep repository state coherent and runnable throughout the step.

Implementation awareness:
- For UI changes, study existing components, patterns, and styling before writing new code. Do not introduce new UI frameworks, component libraries, or CSS methodologies unless explicitly required.
- When modifying a function signature, type, API endpoint, or data model, trace all callers and consumers in the codebase and update them. Do not change a contract in one place and leave stale references elsewhere.
- When modifying database schemas, configuration formats, or persistent data structures, consider migration from existing data. Provide backward-compatible defaults where possible.

Style:
- Follow the style guidelines and language-specific fallback defaults provided below in this prompt.

Validation (targeted scope only):
- Run tests directly related to the files you changed. Do NOT run the full test suite.
- Run lint and typecheck on changed files only.
- Fix any failures introduced by your changes before finishing.
- The full test suite will be run in a separate verification step after implementation.

Documentation:
- If behavior, API, CLI, configuration, or setup changes, identify and update all affected documentation in the same step.
- Check for docs beyond `README.md`: look for `docs/`, `doc/`, `wiki/`, API reference files, architecture docs, configuration guides, and any other markdown or text documentation in the repository.
- If the repository maintains a changelog, update it for user-visible behavior changes.

Completion output:
- Return only a concise implementation summary:
  - what changed,
  - why it changed,
  - what validation was run and results.
- Do not include conversational prefaces or follow-up questions.
