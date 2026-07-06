# Firmware Simplification Proposal — shrink the on-chip surface to the irreducible control floor

**Author:** control-plane agent · **Date:** 2026-07-06 · **Status:** proposal (data-driven) · **Refs:** #428 (chronic heap), #410 (S8), #424 (band-source split)

> **Thesis in one line:** the ESP32 spends **44 rows of tunable-synchronization traffic for
> every 1 row of actual sensing/control**, carries ~200 individually-pushable tunables + ~200
> readback echoes (≈62 % of its 612 entities), and has run at a **3–8 KB free-heap floor for
> weeks** (vs a healthy 38–44 KB) — which finally panicked it (Task WDT, 2026-07-05). The
> highest-value fix is not more heap watchdogs; it is **removing the tunable-sync machinery
> the device does not need to own**, most of which can go *without touching the device at all*.

---

## 1. First principles — what the firmware MUST own

The firmware is the **deterministic, offline-capable safety + control floor**. Isolated from the
network for up to 72 h it must still, every ~1 s:

1. **Sense** — read the physical probes (zone temp/RH/VPD, outdoor, flow, leaf, etc.).
2. **Decide** — run the deterministic FSM (`determine_mode` + `resolve_equipment`) on the served
   band + a set of control constants.
3. **Actuate** — drive the relays (heat/vent/fan/fog/mister/lights/irrigation).
4. **Stay safe** — safety rails (SAFETY_HEAT/COOL, sensor-fault→all-off), hardware thermostat in parallel.
5. **Keep time & band offline** — the on-chip solar curve (`greenhouse_solar.h`) from SNTP-seeded ephemeris.
6. **Persist** — the active band/config across reboots (NVS, `restore_value: yes`).
7. **Accept AI updates when online** — receive setpoint/tunable changes.
8. **Report enough to be observed** — a minimal decision + health signal.

**Everything else on the device is accretion.** The test for "does this belong on-chip?" is:
*if the network vanished for 72 h, would the plants be worse off without it?* For the tunable-sync
machinery and the derived-diagnostic surface, the answer is **no**.

---

## 2. The data — what has actually accreted

### 2.1 Entity surface (flashed `ab18fe8`, exact generated `main.cpp`)

| type | count | note |
|---|---:|---|
| sensor | 339 | includes ~200 `cfg_*` readback echoes |
| number | 153 | AI-tunables (receive) |
| switch | 48 | tunables / feature flags |
| text_sensor | 37 | diagnostic **strings** — `std::string` heap churn per publish |
| binary_sensor | 22 | health/gates |
| button | 13 | dashboard |
| **total** | **612** | |

**~201 tunables (number+switch) + ~200 `cfg_*` readback echoes ≈ 400 entities = 65 % of the surface** are the *tune-and-confirm* mechanism.

### 2.2 How "live" are the 201 tunables? (`setpoint_changes`, 60 days)

| class | params | pushes/60d |
|---|---:|---:|
| **CONSTANT** (1 value ever) | **88** | **152,542** |
| near-static (2–3 values) | 66 | 84,544 |
| occasional (4–10) | 31 | 83,553 |
| **genuinely dynamic (>10)** | **29** | 129,658 |

Only **29 parameters are genuinely dynamic** — the band anchors (`temp_low/high`, `vpd_low/target/high`
per zone) and the fog/mister dynamics. **154 of 214 (72 %) are static or near-static.** 88 are pure
constants re-pushed **152,542 times** for values that never change.

### 2.3 The traffic is dominated by re-synchronizing static values

- **DOWN (server→device): 7,505 pushes/day.** By source: **band 394,151/60d (87 %)**, plan 29,062,
  esp32 25,853, manual 935. The band reconcile re-pushes ~101 params on a **~5-minute cadence**
  (`zone_priority_center`: 315 s between identical pushes) regardless of change.
- **UP (device→server): `setpoint_snapshot` = 222,333 rows/day** — the `cfg_*` readback echoes of
  191 params, ~every 74 s. **100 of 191 params echo a CONSTANT value ≈129,000×/day.**
- **Real work for comparison:** `climate` (sensors) **1,405 rows/day**, `climate_action_log`
  (decisions) 3,763/day, `diagnostics` 1,405/day, `equipment_state` 1,177/day.

> **Headline: tunable-sync : real-work = 44 : 1.** The device's #1 activity, by both inbound and
> outbound volume, is confirming ~191 mostly-static tunables to the server — not sensing or controlling.

### 2.4 …and it correlates with the heap collapse (#428)

Worst-case free heap by binary: b7a531b **15.8 KB** → 995c9b3 **7.0 KB** → ab18fe8 **3.8 KB**
(healthy floor 38–44 KB). Each entity costs RAM + per-API-subscription buffers + a name string;
each of the ~230,000 publishes/day allocates/queues. The 612-entity, 44:1-churn config *is* the
chronic-heap failure mode that produced the 2026-07-05 Task WDT panic.

---

## 3. What can move OFF the device (and where it goes)

The **firmware-twin** (`deploy/k8s/components/firmware-twin/src/greenhouse_logic.h` + `offline_driver.py`)
runs a **byte-identical** copy of the control logic server-side (pinned by
`tests/test_19_firmware_twin_shadow_src_sync.py`). **Anything the twin can recompute from raw sensor
inputs + the served band does not need to be published by the device.** That is the relocation target
for the entire derived-diagnostic surface.

The band + control constants are `restore_value: yes` (84 NVS-persisted globals), so the device
**already retains them across reboots** — the 5-minute re-push is redundant for persistence.

---

## 4. The proposal — five tiers, ordered by value ÷ risk

### Tier 1 — STOP re-asserting unchanged tunables *(ingestor only — NO OTA, NO firmware, ships today)*
Change the dispatcher to push a tunable **only when its value changes** (plus one re-sync on reconnect),
not force-re-assert ~101 band/zone params every 5 min. **Eliminates ~85 % of the 7,505 pushes/day**
and, symmetrically, the constant-value `cfg_*` echoes → collapses the 222,333 rows/day toward the
real change rate. Immediate heap/flash/network relief **without touching the device**. Zero autonomy
impact (device keeps the band in NVS). **Highest value, lowest risk — do this first and measure the
heap recovery before deciding whether the #429 OTA is even still needed.**

### Tier 2 — BAKE the 88 constants into firmware constants *(OTA)*
The 88 CONSTANT tunables (zone priorities, boost offsets, taper minutes, mechanical-protection floors,
never-toggled feature flags) → compile-time constants in `greenhouse_logic.h`/`globals.yaml`. Removes
**88 number entities + 88 `cfg_*` readbacks ≈ 176 entities**. The 2026-06-17 control-story review
already argued these should not be planner-pushable ("over-parameterizing a mechanical minimum").
Anything genuinely seasonal (a handful) stays tunable.

### Tier 3 — COLLAPSE the readback echoes to one config hash *(OTA)*
Replace ~200 per-tunable `cfg_*` echo sensors with **one `cfg_config_version` / `cfg_config_hash`**
readback. The device echoes a single hash of its active config; the server confirms drift by comparing
hashes (it knows what it pushed). Removes **~200 sensor entities** and the 222 K rows/day of echoes.
(Keeps the drift-detection guarantee — see #424 for why drift detection matters — at 1/200th the cost.)

### Tier 4 — DERIVE diagnostics server-side via the twin *(OTA)*
Drop the device-side derived-diagnostic entities the twin can recompute from raw inputs. Every one of
the climate diagnostic **text_sensors** — `gh_climate_priority_axis`, `gh_climate_moisture_exchange`
(the 384-char JSON), `gh_climate_candidate_summary`, `gh_climate_mode_reason`,
`gh_climate_resource_cost_estimate`, `gh_climate_fog_block_reason`, `gh_climate_next_mist_eligible_s`,
`gh_climate_moisture_state/zone`, the override-audit string — is a **pure function of the raw sensor
inputs + the served band**, which the twin already computes offline. The ingestor stores the raw
sensors (`climate`) and the final decision (`climate_action_log`); running the twin over those
reconstructs every reason/gain/summary for graphs and KPIs **at zero device cost**. This class is the
highest per-publish heap churn (each publish allocates a `std::string`). Keep on-device only the
control-critical decision itself (mode + relay truth); derive the *why* server-side.

### Tier 5 — BATCH the live-tunable channel *(OTA, larger; optional)*
The remaining ~29–60 genuinely-dynamic params → a **single versioned config payload** (one text/number
entity carrying a compact JSON/blob the device parses into internal state) instead of N individual
entity endpoints. Collapses the receive side to ~1–3 entities and makes pushes atomic + versioned.

---

## 5. Net effect (projected)

| dimension | now | after T1–T3 | after T1–T5 |
|---|---:|---:|---:|
| entities | 612 | ~430 | ~150–200 |
| pushes/day (down) | 7,505 | ~1,000 | ~few hundred |
| readback rows/day (up) | 222,333 | ~change-rate | ~1 hash stream |
| tunable-sync : real-work | 44 : 1 | ~5 : 1 | ~1 : 1 |
| free-heap floor | 3.8 KB | recovering | → 38–44 KB target |

**Preserved by construction:** autonomy (FSM + on-chip band + NVS unchanged), network-isolation
resilience (device still runs the band offline on persisted config), the AI control surface (the ~29
dynamic params stay tunable). **Improved:** heap headroom → no chronic Task WDT; flash-wear ↓ ~40×;
network churn ↓ ~40×; the entity list becomes legible.

---

## 6. Sequencing & risk

1. **Tier 1 now** (ingestor PR, no OTA) → observe heap floor for 3–5 days. This alone may lift the
   floor out of the danger zone and is fully reversible (revert the dispatcher change).
2. **Tier 2 + Tier 3 in one gated OTA** (biggest entity removal; replay-neutral — control unchanged,
   only entity/telemetry surface changes → `make firmware-replay` 0-divergence).
3. **Tier 4** as a follow-up OTA once the twin-derivation path is wired into the ingestor/KPI layer.
4. **Tier 5** only if the batched channel earns its complexity after T1–T4.

Every tier is control-neutral (0 replay divergence) and independently reversible. The #429 heap-guard
stays as the backstop while the diet lands. **Recommend starting with Tier 1 immediately** — it is the
rare fix that removes the dominant failure driver with no device risk.
