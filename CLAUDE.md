# Agent Instructions

Fix forward to the requested, verified outcome with the smallest complete change. Ask only about material ambiguity; add no approval, planning, or issue/PR ceremony unless requested.

- Use focused checks or direct probes. Docs-only edits need diff/readback. Add tests only for meaningful risks to critical behavior, security, data integrity, or subtle logic.
- Reuse CI/CD; honor enforced checks without adding pipelines or gates unnecessarily. Broaden checks only for affected behavior; stop rechecking once verified.
- Search narrowly, reuse current evidence, avoid repeated polling/replanning, and report results briefly.
- Preserve unrelated work and secrets. Use owning sources/generators and practical recovery options for destructive work.
- For GitOps changes, commit declarative state, let ArgoCD reconcile, and verify the affected runtime.
