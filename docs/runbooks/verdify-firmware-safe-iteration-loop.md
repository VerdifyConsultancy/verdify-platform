# Verdify Firmware / Band Safe-Iteration Loop

> **Obsolete under the 2026-06-19 single-environment model.** This runbook
> depends on the retired `verdify-dev` shadow environment. Keep it as historical
> context only; do not use its dev/shadow commands for current firmware work.

**Status:** Runbook (lane VerdifyConsultancy/verdify-platform#221). SHADOW-first, device-write=0.
**Author:** laptop-root. **Date:** 2026-06-07.
**Audience:** Jason + the `verdify-firmware` dev agent.

> **Hard gate (CLAUDE.md / lane #221):** the LIVE prod device-writer ingestor
> (`verdify-prod/verdify-ingestor`) is the SOLE live ESP32 writer (`192.168.10.111:6053`).
> **NEVER** flash live firmware, push a live setpoint, or scale/restart its push path
> without **root executor** confirmation. Everything below bakes a change in
> dev/shadow *before* the one gated live step.

---

## 0. The big picture — ideate → deploy → validate, safely

```
                       SHADOW (device-write=0)                 SAFEGUARDED LIVE
  ┌──────────┐   ┌──────────────────────────────────┐   ┌──────────────────────────┐
  │ idea /   │──▶│ 1. branch + worktree             │──▶│ 6. make firmware-deploy   │
  │ band edit│   │ 2. replay-diff vs prod corpus     │   │    (compile→OTA→60s soak  │
  └──────────┘   │ 3. firmware-invariants + tests    │   │     →sensor-health→pass/  │
                 │ 4. verdify-dev shadow bake        │   │     auto-rollback)        │
                 │ 5. (firmware) digital-twin bake   │   │ 7. 48h behavioral bake    │
                 └──────────────────────────────────┘   │ 8. promote-last-good      │
                                                         └──────────────────────────┘
```

There are **two surfaces** you iterate on, and they have different safe loops:

| Surface | What it is | Where it lives | Safe loop |
|---|---|---|---|
| **Firmware** (FSM control logic) | `firmware/lib/greenhouse_logic.h` + `firmware/greenhouse/*.yaml` | compiled to OTA binary, runs on ESP32 | replay-diff → invariants → twin bake → gated OTA |
| **Bands** (agronomic targets, incl. the "orchid time-of-day" curve) | `crop_target_profiles` rows (DB) → `fn_band_setpoints` → dispatcher → firmware setpoints | the **DB**, not the firmware | migration in dev DB → band-viz dashboard → gated prod migration. **No OTA needed.** See `verdify-band-tuning-safe.md`. |

The decisive fact: **changing a band does NOT require a firmware flash.** Bands are
DB-driven; the firmware just consumes the resolved setpoints the dispatcher pushes. So
most "tuning" iterations never touch the device binary at all.

---

## 1. The shadow lane (device-dark) — verified invariants

The `verdify-dev` namespace is the structurally-safe place to exercise firmware/planner
changes end-to-end without any path to the device:

- **Ingestor `replicas:0`** in `verdify-dev` (the writer is scaled to zero — no push loop).
  - Verify: `ssh jason@192.168.30.32 sudo k3s kubectl -n verdify-dev get deploy verdify-ingestor -o jsonpath='{.spec.replicas}'` → `0`.
- **`deny-esp32-egress` NetworkPolicy** denies egress to `192.168.10.0/24` for
  `component=ingestor` in `verdify-dev` (so even if an ingestor pod were ever scaled up,
  it physically cannot reach the ESP32).
  - Verify: `ssh jason@192.168.30.32 sudo k3s kubectl -n verdify-dev get networkpolicy deny-esp32-egress` exists.
- **Acceptance for #221:** "dev ns never opens an ESTAB writer to the ESP32" — both
  invariants above are the enforcement. Re-probe both before any dev exercise.

> Both invariants were **verified live 2026-06-07** (ingestor `0/0`, NetworkPolicy present).

---

## 2. The firmware OTA loop (`make firmware-deploy`) — what it already enforces

The existing target (`Makefile: firmware-deploy`) is a self-protecting deploy:

1. **`scripts/firmware-deploy-preflight.sh`** — refuses OTA while any `critical`/`high`
   alert is unresolved (override requires `ALLOW_FIRMWARE_DEPLOY_GUARD_OVERRIDE=1` +
   a specific `FIRMWARE_DEPLOY_OVERRIDE_REASON`).
2. **Dirty-tree refusal** — refuses to flash a dirty worktree unless
   `ALLOW_DIRTY_FIRMWARE_DEPLOY=1` **and** an explicit execution reason are set (emergency only).
3. **Compile + OTA** to `ESP32_DEVICE=192.168.10.111` with a stamped
   `fw_version=<date>.<sha>`.
4. **60s soak** for reboot + ingestor reconnect + first diagnostics cycle.
5. **`sensor-health SINCE='5 minutes'`** decides pass/fail.
   - **Pass** → archive the binary + advance the *expected-firmware* pin. **Rollback target
     stays on the prior last-good** (it bakes first).
   - **Fail** → `scripts/firmware-rollback.sh firmware/artifacts/last-good.ota.bin` flashes
     last-good back, waits 60s, re-runs sensor-health, exits non-zero.

**This is already a good loop. The two gaps lane #221 closes are:**

- **(A) bake BEFORE the flash** — today the only pre-flash behavioral check is the
  frozen-corpus replay-diff. The **digital twin (#34)** turns rule-3's file-mtime bake into
  a *behavioral* bake against today's real telemetry. → §3.
- **(B) the rollback floor is stale** — `last-good` is the 2026-05-17 binary while the
  device runs `2026.5.30.1418.aa6518c`. A rollback today drops the device 2 weeks back.
  → §4 (#35).

### 2.1 VM-era assumption to fix before the next live deploy (gap found 2026-06-07)
`scripts/firmware-deploy-preflight.sh` and `firmware-rollback.sh` reach the DB via
`docker exec verdify-timescaledb …` and read secrets from `/srv/greenhouse/esphome/`.
**Those paths were on the now-powered-off `.150` VM.** The DB is now
`verdify-prod/verdify-db` (k3s, TimescaleDB 2.25.2-pg16) and esphome secrets must be
re-homed. **Before any live `make firmware-deploy`, the preflight DB handle + the
esphome secrets path must be re-pointed at the k3s DB / new tooling host.** This is a
prerequisite gate, tracked as a firmware-optimization issue.

---

## 3. Digital twin (#34) — deploy it as the pre-flash behavioral bake

The twin runs the **same** `greenhouse_logic.h` the ESP32 compiles, fed the same
telemetry, and **never actuates** (read-only by construction: no actuation credential, no
route to ESP32, INSERT-only DB role). Design: `docs/design/firmware-digital-twin.md`.

**Status:** #34 is `state:MERGED` but **not deployed** to k3s. Deploy it as a `verdify-dev`
(or a dedicated `verdify-twin`) workload. Two twin roles:

- **prod-twin** pinned to the deployed `last-good` → **prod-vs-reality divergence** (the
  novel signal; catches a firmware-vs-device drift a twin-vs-twin comparison is blind to).
- **stage-twin** pinned to the **OTA candidate** → shadows live telemetry for the full bake
  window *before* the binary is flashed (converts the mtime bake into a behavioral bake).

**Deploy checklist (additive, dev-first, no device risk):**
1. Land the twin runtime (the `--stream` mode on `replay_emit.cpp`, gated so the batch
   replay-diff path is byte-for-byte unchanged — see design §2.1) + the `twin_decisions`
   hypertable + `twin_ro` INSERT-only role (#33 / TWIN-6).
2. **Close the setpoint-coverage gap first (#31 / TWIN-3)** — the differ must walk every
   `Setpoints` field with an active code path against the dispatcher's pushed keys, or the
   twin diverges for a *config* reason and the divergence metric is untrustworthy as a gate.
   This is a **blocker for trusting the gate** (design §2.2).
3. Fix the **local-hour timezone bug** (`EXTRACT(HOUR … AT TIME ZONE 'America/Denver')`) and
   apply the **`dt_ms = min(dt_ms, 5000)`** device cap in the live driver (design §2.2/§2.3).
4. Set the prod-vs-reality alert threshold **≥ `MAX(MIN_HEAT_OFF_MS, MIN_FAN_OFF_MS)`** (the
   twin predicts FSM *intent*, the device defers by dwell timers — several minutes).
5. Wire the **prod-vs-reality Grafana panel** (design §6 Phase 1).

**Acceptance (#34):** twin-prod shadows live telemetry through `greenhouse_logic.h`; writes
`twin_decisions` per tick; the divergence panel renders; read-only by construction.

---

## 4. Refresh the rollback floor (#35) — `make firmware-promote-last-good`

- The live device runs `2026.5.30.1418.aa6518c`; `last-good` is still the 2026-05-17
  binary. Freeze rule 3 requires a **48h bake** (no `severity='critical'` sensor-health
  alert) before promotion — that window completed ~2026-06-01.
- **Promote (safeguarded):**
  `make firmware-promote-last-good FW_VERSION=2026.5.30.1418.aa6518c`
  → advances `firmware/artifacts/last-good.{version,ota.bin,metadata.env}`.
- **Precondition:** the archived artifacts for `aa6518c` must exist in
  `firmware/artifacts/` on the tooling host. If they were lost with the `.150` decom,
  re-archive from the running device first (`make firmware-archive-artifacts`) — do NOT
  re-flash to produce them.
- **Gate:** this only changes the *rollback target file*, not the device. Still confirm
  with Jason because it changes what an auto-rollback would flash.

---

## 5. The staging-device validation harness (alternative / complement to the twin)

If/when a second physical ESP32 (or an ESPHome `host`-platform Tier-2 build — design §2.2)
is available, a **staging device** lets a candidate binary run a full bake on real hardware
fed shadow telemetry before the live OTA. Until then, the **Tier-1 twin (§3) is the
pre-flash bake** and is sufficient for FSM-intent regressions; mister-timing / dwell-timer
regressions are a Tier-2-only signal (design §2.2 fidelity boundary).

---

## 6. The one-page operator checklist

```
IDEATE
  □ branch + git worktree add (isolated lane)
SHADOW (no device)
  □ make firmware-replay           # FSM trace vs prod corpus
  □ make firmware-invariants       # 16 safety invariants
  □ make firmware-check            # replay-diff vs last deployed ref
  □ (twin) deploy candidate to stage-twin, watch divergence panel ≥ bake window
  □ re-probe dev device-dark invariants (ingestor replicas:0 + deny-esp32-egress)
VERIFY → run the technical preflight and confirm rollback readiness
LIVE
  □ refresh last-good if stale (§4)
  □ re-point preflight DB handle + esphome secrets at k3s (§2.1)  ← prereq
  □ make firmware-deploy           # compile→OTA→60s→sensor-health→pass/auto-rollback
  □ 48h behavioral bake (no critical sensor-health alert)
  □ make firmware-promote-last-good FW_VERSION=<new>   # advance rollback floor
DURABILITY
  □ re-probe sensor-health + twin divergence ≥10 min after the deploy claim
    record "GREEN at <T>, re-verified at <T+N>"
```
