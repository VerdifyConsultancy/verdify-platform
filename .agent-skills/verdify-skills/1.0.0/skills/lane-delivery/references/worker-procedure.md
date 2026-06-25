# Worker procedure

1. Orient to contract and baseline.
2. Identify the smallest implementation sequence that keeps tests runnable.
3. Add or update contract-facing tests before or with behavior changes.
4. Implement without changing unrelated formatting or dependencies.
5. Run targeted validation, then the required lane suite.
6. Review the full diff for scope, security, error handling, migrations, and generated files.
7. Push, update the PR, and complete closeout.

Do not claim evidence you did not observe. Record skipped tests and why; skipped required tests normally block closeout.
