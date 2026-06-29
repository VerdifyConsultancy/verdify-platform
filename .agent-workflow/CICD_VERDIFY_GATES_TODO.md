# CI/CD — Verdify Gates (Deferred Wiring)

Status: **TODO / not yet wired.** This standardization lane intentionally does
**not** modify existing CI. The repo's current pipeline under
`.github/workflows/` is left untouched.

This file records the Verdify fleet gate wiring that should be added in a
follow-up lane so `verdify-platform` CI enforces the standard shape, on top of
the existing product gates (which stay authoritative).

## Existing CI (do not rewrite here)

- `.github/workflows/ci.yml` — Python lint/test, migration-rollback-safety,
  firmware replay-diff/invariants, drift guards, and the firmware/tunable/
  service-restart guards described in `AGENTS.md`.
- `.github/workflows/container-publish.yml` + `reusable-container-build.yml` —
  digest-pinned GHCR image builds.
- `.github/workflows/prod-promote.yml` + `promote-diff-guard.yml` — gated,
  digests-only prod promotion PR flow.
- `.github/workflows/k8s-manifests.yml` — manifest validation.
- `.github/workflows/cnpg-image.yml`, `lab-content-pipeline.yml` — DB image and
  lab content pipelines.

These remain the source of truth for language-, firmware-, schema-, and
deploy-level checks. Do not duplicate or rewrite them here.

## Deferred Verdify gates to wire

1. **Standard-shape integrity gate**
   - Verify `.agent-skills/verdify-skills/<VERSION>/` is present and that its
     `VERSION` file matches the vendored package path (`1.0.0`).
   - Verify discovery symlinks under `.claude/skills/` and `.agents/skills/`
     resolve into the vendored package (no broken/dangling links).
   - Verify the `BEGIN/END VERDIFY AGENT WORKFLOW` markers in `AGENTS.md`
     (symlink to `CLAUDE.md`) are present and paired.

2. **Skills doctor gate**
   - Run `bin/verdify doctor --repo .` (from the vendored package) and fail on a
     non-zero report (e.g. missing `.agent-workflow`).

3. **Artifact validation gate**
   - Run `bin/verdify artifact validate` over `.agent-workflow/` YAML artifacts
     against their `schema_ref` schemas as they are populated.

4. **North Star presence gate**
   - Require `.agent-workflow/northstar/NORTHSTAR_PRODUCT.md` to exist and stay
     non-boilerplate for this FULL-tier repo.

5. **Secret-scan gate**
   - Ensure no greenhouse/device secrets, DB credentials, Slack/MQTT tokens,
     OpenAI keys, or production payloads enter the tree (aligns with the
     no-secret-exposure rules in `AGENTS.md` and `.gitignore`/`.sops.yaml`).

## Notes

- Firmware-OTA and prod-ArgoCD-sync gates are device-affecting and remain behind
  the human operator gate; they are not turned into autonomous CI actions.
- Wiring these gates is out of scope for the standardization PR; this file is the
  handoff marker for that follow-up work.
