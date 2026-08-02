# Verdify Release Checklist

Last updated: 2026-07-03 (#413: §B pinch-decision + bake-record steps). Companion to `docs/runbooks/laptop-operator.md` (the how-to) and
`docs/reviews/lane1-architecture-audit-2026-06-16.md` (the why). Single-env-prod model: `main`
is the only branch; prod is ArgoCD app `verdify-prod-dark` (ns `verdify-prod`), manual-sync
behind the device-write gate.

Use the section(s) matching what you changed. **Jason is the human gate** for firmware OTA, the
prod `argocd app sync`, device-VLAN actions, destructive prod DB work, and outward-facing edge/DNS.

---

## A. Services (api / mcp / ingestor / migrate / planner)

**Pre-merge (on a topic branch or direct to `main`):**
- [ ] `make lint` and `make test` green (the 1 known-flaky `test_dew_point_risk_computes`
      timeout is tolerated; nothing else).
- [ ] **Schema-first ordering:** if this touches `verdify_schemas/**`, the schema change lands
      *before* its consumers; `verdify_schemas/tests/test_drift_guards.py` green.
- [ ] If schema touched (`verdify_schemas/**`, `ingestor/entity_map.py`, `mcp/server.py`): the
      PR body / commit message states **which services must bounce** post-merge
      (e.g. `Post-merge restart: verdify-mcp, verdify-ingestor`). `service-restart-drift-guard`
      will check for it.
- [ ] CI green: `CI_BASE_REF=<base> make ci` locally and the exact-head
      `Verdify Platform / Argo PR CI` status. Run acknowledged disposable-DB
      drift guards separately when that backend is provisioned.

**Publish → promote → sync:**
- [ ] After the central contract is green, merge to `main` → exact-SHA Kaniko
      archive builds → pinned Crane publishes immutable Zot digests. The current
      platform is blocked; do not use its direct-main prod-pin step as approval.
- [ ] Open a reviewed digest-only desired-state PR. Verify only the intended
      digest/comment/component-ref lines changed and all device-write interlock,
      egress, and render invariants still hold. Human review + merge changes git
      only; the cluster remains untouched.
- [ ] **[Jason gate]** Operator runs `argocd app sync verdify-prod-dark`.
- [ ] Post-sync verify: app Synced/Healthy; single-writer intact (`replicas:1`, exactly one
      ingestor pod, `sum(verdify_esp32_writer_estab)==1`); restart the services named above; tail
      ingestor logs for clean ESP32 connect.

---

## B. Firmware OTA (Jason-gated, ≤1/week, 48 h bake)

**Pre-deploy validation (local — CI does not gate firmware fully):**
- [ ] On `main`, clean tree (`git status --short`). `make lint && make test`.
- [ ] `make test-firmware` (native C++ unit + replay) — green.
- [ ] Corpus fresh: if `firmware/test/data/replay_overrides.csv.gz` max-ts is > ~3 weeks old,
      `make replay-corpus-refresh` first.
- [ ] `make firmware-invariants` — 0 breaches.
- [ ] `make firmware-replay OLD=<last-deployed-sha> NEW=HEAD` — 0 divergence, or reviewed +
      justified with a `THRESHOLD_PCT` override.
- [ ] **If a band-CURVE changed** (`greenhouse_solar.h` `band_value_at_phase`, anchor resolution,
      diurnal-band shape): also `make firmware-replay-band OLD=<base>` and **read the %** — the
      stock corpus-fed replay shows 0 here by construction; this is the check that catches the
      wet-night-curve class.
- [ ] `make firmware-check` — real ESPHome compile passes (CI only `esphome config`-validates).
- [ ] Every new tunable has a `cfg_*` readback (`no-new-fire-and-forget`).

**Deploy preflight gates (enforced by `make firmware-deploy`):**
- [ ] No open `critical` (or legacy `high`) alerts.
- [ ] Climate ≤300 s fresh; `climate_action_log` fresh + complete.
- [ ] 48 h bake since `last-good.ota.bin` (override needs a ≥12-char logged reason).
- [ ] ≤1 OTA this calendar week (resets Mon 00:00 MDT).
- [ ] `last-good.ota.bin` and a nonempty `last-good.version` present, and `OTA_PW` / rollback
      secret reachable (auto-rollback depends on all three — do not deploy if rollback can't run).
- [ ] Outdoor-temp >85°F forecast is a warning, not a blocker — note it.

**Deploy + post:**
- [ ] `make firmware-deploy` → confirm post-OTA `sensor-health` PASS; expected-firmware pin bumped.
      (Fail → auto-rollback to last-good; investigate before retry.)
- [ ] **Pinch state — execute the g-377 decision (#377, Jason gate; mechanics per #413).**
      `band_track_fraction` is `restore_value: no` with `initial_value: '0.0'`
      (`firmware/greenhouse/globals.yaml`), so the flash just **cold-started the pinch to 0.0**
      regardless of what was live (as of 2026-07-03 the live pre-#385 binary boots 0.25 and runs
      planner-pushed 0.25). The `crop_band_anchors`→NVS reconcile does **NOT** re-assert it — that
      path only protects `restore_value: yes` band globals (`docs/CONTROL-ARCHITECTURE.md` §7).
      Execute ONE of the two g-377 outcomes:
      - **Accept float 0.0** (the ADR-0004 planner default): nothing to push — confirm the readback:
        `scripts/verdify-db.sh prod -c "SELECT ts, value FROM setpoint_snapshot WHERE parameter='band_track_fraction' ORDER BY ts DESC LIMIT 1;"`
      - **Re-pin 0.25:** there is **no push-only command on current `main`** — ADR-0004 pinned the
        registry bounds to `[0.0, 0.0]` (`verdify_schemas/tunable_registry.py`), so MCP
        `set_tunable` rejects 0.25, the dispatcher clamps a direct `setpoint_plan` row back to 0.0
        (`ingestor/tasks/dispatcher.py::_coerce_registry_value`), and the RT listener rejects it
        (`ingestor/ingestor.py::_accept_outbound_setpoint`). Re-pinning therefore means:
        (1) widen the `band_track_fraction` registry `min`/`max` (schema change; bounce
        `verdify-mcp` + `verdify-ingestor` per freeze rule 7); (2) push
        `set_tunable('band_track_fraction', 0.25, reason=…, trigger_id=<audited MANUAL trigger>,
        planner_instance=…)` via MCP (or insert one `setpoint_plan` row) — dispatcher applies
        within ~5 min; (3) confirm the readback query above returns 0.25. **Never** push a raw
        ESPHome `number_command` from an operator host — that is a second device writer.
- [ ] **Bake report records the config** (required for every bake/KPI comparison window): the
      executed `band_track_fraction` state (+ readback proof), `dehum_vent_hold_enabled` state
      (`cfg_*` readback; OFF-default flag from #410), and the **envelope config** — door
      screen-window OPEN/CLOSED (open ~2026-06-19 → fall per #412; ~3× passive night air exchange
      while open). **Never change the window state mid-bake.**
- [ ] PR body / commit carries the **required artifacts**: replay-diff output, invariant-suite
      output, unit-test delta (CLAUDE.md firmware rule 9).
- [ ] **Bake 48 h** with no critical alert, then `make firmware-promote-last-good FW_VERSION=<v>`.

---

## C. Dashboards (Grafana)

- [ ] Edit the **source-of-truth** copy under `grafana/dashboards/` (NOT
      `grafana/provisioning/dashboards/json/` — those are dead shadows; see audit §4.5).
- [ ] Regenerate the ConfigMaps: `scripts/gen-grafana-dashboard-cms.py` →
      `deploy/k8s/components/grafana/generated/dashboards-cm-*.yaml`.
- [ ] `make grafana-brand-check` (and `-live` when appropriate) for embed styling.
- [ ] Prefer the **device-truth** source (`setpoint_snapshot`) over DB-derived (`fn_band_timeline`)
      for band/compliance panels (audit D11).
- [ ] Verify render locally / on `graphs.verdify.ai` after the prod sync — type-checks don't catch
      visual regressions.

---

## D. Lab site (lab.verdify.ai)

- [ ] If a generator changed (`scripts/*-page.py`, `render-*.py`): `make site-doctor`,
      `make site-lint`, and verify a local Quartz render.
- [ ] `make site-publish-status` to confirm the publish pipeline state.
- [ ] Note: lab content is generated from S3 + DB by the `verdify-lab-publisher` CronJob, not
      committed to git (`site/content/` is empty in-repo). Embeds depend on fixed Grafana UIDs —
      if you retire a dashboard, check `generate-forecast-page.py` embed UIDs first.

---

## E. Schema / migrations

- [ ] One migration change at a time, classified by `make migration-rollback-safety`.
- [ ] **Never wrap a self-committing migration** (own `COMMIT;` or `CREATE INDEX CONCURRENTLY`
      etc.) in an outer `BEGIN; … ROLLBACK;` — use `make irrigation-migration-check` / the
      `--rollback-wrap` preflight, which refuses to wrap a self-committing file.
- [ ] Run the targeted rollback proof the migration describes.
- [ ] `service-restart-drift-guard`: document which services bounce post-migrate.
- [ ] After a schema change, **regenerate `db/schema.sql`** so it stays a faithful snapshot
      (it is currently stale — audit D5).

---

## F. Docs (always)

- [ ] Durable decisions, invariants, and runbook changes go into `docs/` — not chat only.
- [ ] If the board / ArgoCD ownership / access boundaries changed, update the root lane docs
      (`AGENT_LANE.md`, `ARGOCD.md`, `ACCESS_MATRIX.md`, `PROJECT_BOARD.md`).
- [ ] `git status --short` reviewed so unrelated changes aren't bundled in.
