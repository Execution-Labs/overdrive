A git push failed with the following error output:

```
{error_output}
```

The project is located at: {project_dir}
The push target branch is: {push_target}

Analyze the error and fix the underlying issue so the push can succeed on retry.
Common causes include:
- Pre-push hook failures (linting errors, test failures, formatting issues)
- File permission or path issues

Apply the minimal set of changes needed to fix the problem. Do NOT change test expectations or disable hooks — fix the actual code issue that the hook caught.