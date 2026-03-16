Classify this task into the most suitable pipeline.

Return JSON only with this exact shape:
{
  "pipeline_id": "string",
  "confidence": "high" | "low",
  "reason": "short explanation"
}

## Scope assessment

Before choosing a pipeline, assess the scope of the task:

- If the task is **broad, multi-faceted, or would require multiple independent changes across different areas of the codebase**, classify as `plan_only`. This pipeline decomposes the work into smaller executable subtasks.
- Examples that should use `plan_only`: "Build a user authentication system", "Add multi-tenancy support", "Migrate from REST to GraphQL", "Set up CI/CD pipeline with testing, linting, and deployment", or any request that describes an initiative or epic rather than a single focused change.
- If the task is a **single, focused change** (one feature, one bug fix, one refactor), use the specific pipeline that matches the work type.

## Rules

- `pipeline_id` must be one of the allowed pipeline IDs provided below.
- Choose `high` only when the task intent is clear and specific for one pipeline.
- Choose `low` when intent is ambiguous, underspecified, or could fit multiple pipelines.
- Keep `reason` concise and concrete.
- Do not include markdown, code fences, or extra keys.
