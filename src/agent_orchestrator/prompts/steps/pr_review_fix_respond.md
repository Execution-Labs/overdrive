Address {{platform}} review comments by implementing code fixes and drafting responses.

Your job is to analyze existing review comments on this pull/merge request, implement code fixes for actionable comments, and draft a response for each comment explaining what was done.

## Instructions

1. **Analyze each review comment** — Determine whether it requires a code change, is a question, is praise, or is out of scope.
2. **For actionable comments** — Plan and implement the code fix. Prefer root-cause remediation over shortcut patches. Preserve correct existing behavior.
3. **For each addressed comment** — Draft a response body explaining what was changed and why.
4. **For non-actionable comments** (praise, questions, out-of-scope) — Draft an appropriate response without making code changes.

## Truncated diffs

If the diff was truncated (indicated by a `[DIFF TRUNCATED]` notice), consult the `--stat` summary above to identify files whose changes are not included in the diff. Use `{{cli_tool}}` to inspect additional context if needed. Prioritize source files over generated or vendored files.

## Validation

After making code changes:
- Run tests directly related to the files you changed. Do NOT run the full test suite.
- Run lint and typecheck on changed files only.
- Confirm each change addresses the reported issue without introducing regressions.

## Output

Respond with ONLY a JSON object:
```
{
  "addressed_comments": [
    {
      "comment_id": "original comment identifier",
      "response_body": "Response text to post as a reply",
      "files_changed": ["path/to/file.ext"],
      "fix_description": "What was changed and why"
    }
  ],
  "unaddressed_comments": [
    {
      "comment_id": "original comment identifier",
      "reason": "Why this comment was not addressed (e.g., out of scope, needs clarification)",
      "response_body": "Response text to post as a reply"
    }
  ],
  "summary": "Overall summary of fixes applied",
  "proposed_decision": "comment"
}
```

Rules:
- Every review comment must appear in either `addressed_comments` or `unaddressed_comments`.
- Each entry must include `comment_id` and `response_body`.
- Preserve correct existing behavior — do not regress working code.
- Do not perform unrelated refactors or changes beyond what the review comments request.
- Do not leave stubs, TODOs, placeholders, or empty bodies.
- Be specific in `fix_description`: reference exact file paths and what changed.
- `proposed_decision`: `"approve"` if all comments were addressed, `"request_changes"` if critical issues remain unaddressed, `"comment"` otherwise.
- Return only the JSON object (no preamble, tool logs, or follow-up questions).
