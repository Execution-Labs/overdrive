Review the commit produced by a completed task against its original description and plan.

Your job is to analyze the diff for correctness, bugs, missing edge cases, test coverage gaps, security issues, and adherence to the original requirements. Then write a concrete remediation plan so the subsequent implement step can fix every issue you find.

## Review checklist

1. **Requirement adherence** — Does the diff fully satisfy the original task description and plan? Are any requirements missing or only partially implemented?
2. **Logic errors & bugs** — Are there off-by-one errors, incorrect conditions, wrong variable references, race conditions, or broken control flow?
3. **Edge cases** — Are boundary conditions, empty inputs, null/undefined values, and error paths handled?
4. **Test coverage** — Are there tests for the new/changed behavior? Do the tests cover edge cases and failure modes?
5. **Security** — Are there injection vectors, improper input validation, leaked secrets, or unsafe operations?
6. **Code quality** — Are there naming issues, dead code, duplicated logic, or overly complex constructions that hurt maintainability?

## Output

Write your findings and remediation plan into the workdoc's **## Plan** section (which already exists — do not repeat the heading) using this structure:

```
### Findings

1. **[severity: high/medium/low]** Brief title
   - File: `path/to/file.ext` (lines N-M)
   - Issue: Description of the problem
   - Fix: Specific remediation action

2. ...

### Fix tasks

For each finding above, describe the concrete change needed:
1. In `path/to/file.ext`: [what to change and why]
2. In `path/to/test_file.ext`: [what test to add/fix]
...
```

If no issues are found, write "No issues found — commit looks correct." in the Plan section.

Rules:
- Be specific: reference exact file paths, line ranges, variable names.
- Prioritize findings by severity (high first).
- Each finding must have a concrete fix action, not just a description of the problem.
- Do not modify any code yourself — only write the plan.
- When using tables, use standard markdown pipe tables. Never use Unicode box-drawing characters.
- Return only the review body (no preamble, tool logs, or follow-up questions).
