# Gravity Readiness Checklist

Each readiness item must be binary for pilot approval:

- `pass`: inspected evidence proves readiness.
- `fail`: evidence contradicts readiness or required evidence is missing.
- `blocked`: evidence cannot be inspected because of missing access or human
  decision.

Do not convert a `fail` into `pass` with a narrative promise. Create issues,
gates, or platform readiness work instead.

## Pilot constraints

The first Gravity wave must:

- have a validated `gravity-core-extraction-plan.yaml` when Sunshine-derived
  reuse, generic core extraction, or organization pack extraction is in scope;
- be small enough to review in one human session;
- use the standard lifecycle without exceptions;
- have deployed preview or dev evidence;
- include exact human test steps;
- include rollback and observability evidence;
- prove the controller can reproduce the workflow on a later wave.
