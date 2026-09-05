# planning/ — validated backlog source of truth

`backlog.yaml` is the machine-validated plan for the floating-corridor replan
(lanes → user stories → work items, each with what/why/how/acceptance/deps/preflight/
wave). `schema.py` is the pydantic contract; it enforces structural and
cross-field invariants (every item explains what/why/how; new control properties
imply a schema touch; no issue is owned by two lanes; deps are well-formed;
waves resolve). Readable rollup: top-level `LANES.md`.

Validate on any change:
    make planning-validate        # python planning/schema.py planning/backlog.yaml
    pytest planning/tests/test_backlog.py

When you add/restructure work: edit `backlog.yaml`, re-run validate, regenerate
issues via the manifest, and keep `LANES.md` in sync.
