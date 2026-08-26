# Agent Instructions

Work directly from the user's request. Use reasonable judgment, implement the requested outcome, run relevant checks, and verify the real end state.

- Preserve unrelated work already present in the repository.
- Never expose or commit secrets.
- Use the repository's source and documentation for technical context.
- Follow existing CI/CD for validation and delivery.
- For k3s or GitOps changes, commit the declarative source and allow ArgoCD to reconcile it; avoid unmanaged cluster drift.
- Ask only when the target or outcome cannot be determined safely.
