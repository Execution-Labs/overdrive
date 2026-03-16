Perform a code-level security scan for this task.

Mission:
- Identify concrete code and configuration security weaknesses in the repository.
- Produce evidence-backed findings for downstream reporting and task generation.

Scope:
- Code/config layer only (authn/authz, input handling, injection, secrets handling, crypto usage, unsafe deserialization, insecure defaults).
- Do NOT perform dependency/advisory inventory in this step.
- Do NOT propose implementation plans or choose remediation strategy.

Rules:
- Report only evidence-backed issues tied to specific files/locations.
- Do not invent exploit paths or impact beyond observed evidence.
- If certainty is limited, state uncertainty explicitly.
- Focus on materially actionable security findings, not style issues.

Output requirements:
- Return concise, deduplicated, actionable findings with:
  - severity,
  - category,
  - summary,
  - file/location,
  - concrete evidence observed,
  - uncertainty notes when applicable.

## No-issue case
If no code-level security issues are identified AND the prior dependency scan (provided as context, if any) also found no issues:
- Write "No issues found" as the first line of your response.
- Follow with a brief summary of what was scanned and why it is clean.
- The pipeline will skip task generation and complete.

If THIS scan found no issues but the prior dependency scan DID find issues, report your clean code scan results normally (do NOT write "No issues found" as the first line) so that task generation can proceed for the dependency findings.
