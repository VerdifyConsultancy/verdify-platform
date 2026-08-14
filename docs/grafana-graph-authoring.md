# Grafana Graph Authoring Guide & Homepage Compliance-Band Reference

Durable, practical reference for building and changing the public Grafana graphs
(graphs.verdify.ai + the lab.verdify.ai embeds). Covers the deploy/iterate
workflow, the homepage climate compliance-band panels, the equipment-state
**stripe** rendering recipe, every hard-won gotcha, and the design rationale for
how the current homepage equipment overlay was arrived at.

Companion docs (don't duplicate them here):
- `docs/grafana-brand-system.md` — palette, series colors, embed brand rules.
- `docs/grafana-panel-catalog.md` — inventory of every dashboard + panel.

Everything below is **visualization only — non-control-path**. Editing graphs
never touches the device, DB writes, or the planner; it is safe to iterate live
within the normal autonomy policy. (It still goes to prod, so verify renders.)

---

## 1. Architecture & deploy path

```
grafana/dashboards/<name>.json        ← SOURCE OF TRUTH (hand-edited)
        │  scripts/gen-grafana-dashboard-cms.py   (refresh CM data keys)
        ▼
deploy/k8s/components/grafana/generated/dashboards-cm-{0,1,2}.yaml   ← ConfigMaps
        │  ArgoCD app `verdify-prod-dark` (manual-sync) / SSA apply
        ▼
Grafana pod (ns verdify-prod, deploy/verdify-grafana)
  • dashboards dir-mounted at /etc/grafana/provisioning/dashboards/json/bucket{0..3}
  • provider config (provisioning-cm.yaml) updateIntervalSeconds: 300  ← reload cadence
  • anonymous org role = Viewer (public, no login); admin login requires the
    out-of-band verdify-grafana-secrets/GRAFANA_ADMIN_PASSWORD prerequisite
  • image-renderer auth requires the out-of-band verdify-grafana-secrets/
    GRAFANA_RENDERER_TOKEN prerequisite in both containers
    (no defaults; missing Secret/key leaves a new pod CreateContainerConfigError)
  • image-renderer sidecar → server-side PNG via /render/d-solo/...
        ▼
verdify-grafana-render-cache (five-minute PNG cache; one-minute browser freshness)
        ▼
graphs.verdify.ai (interactive dashboards) + lab.verdify.ai (automatic
viewport-bounded interactive panels; distant iframe release; cached PNG
best-effort failure fallback)
```

Key facts:
- **One CM per shard**, split only to stay under the **1 MiB ConfigMap limit**.
  `site-home.json` currently lives in `verdify-grafana-dashboards-0`
  (`dashboards-cm-0.yaml`). The gen script preserves the CM→dashboard assignment,
  so a JSON edit re-renders in place without re-sharding.
- The dashboard JSON is **dir-mounted** (the bucket dirs), so a CM update
  propagates to the pod (~60 s) and the provisioner reloads it on its **300 s**
  cycle — **no pod restart needed**, but reload is not instant. (The *provider
  config* `provisioning-cm.yaml` is `subPath`-mounted; changing the provider
  itself would need a restart. The dashboards themselves do not.)
- The CM is ~750 KB. **Apply it server-side** (`kubectl apply --server-side`).
  A client-side `kubectl apply` stores a `last-applied-configuration` annotation
  that doubles the size and **exceeds the etcd object limit** → fails. ArgoCD
  already annotates the CM `ServerSideApply=true`.
- `/render/` is intentionally routed through the dedicated cache while every
  other Grafana path goes directly to `verdify-grafana`. A warm response carries
  `X-Cache-Status: HIT`; a cold first render is `MISS`. Cache identity includes
  the full query string, so never omit panel variables, theme, range, or size.

---

## 2. Authoring workflow — how to create or change a graph

1. **Edit the source JSON** in `grafana/dashboards/<name>.json`. This is the
   source of truth; never hand-edit the generated CM as the durable fix.
   - For large/structured edits, transform with a Python script and write back
     with `json.dump(obj, fh, indent=2, ensure_ascii=True)` **+ a trailing
     newline**. That round-trips the existing files **byte-identically**, so the
     diff is limited to your real change (verified: a no-op round-trip = 0 diff).
2. **Test panel SQL read-only against prod _before_ deploying.** Macros
   (`$__timeFrom()`, `$__timeTo()`, `$__timeFilter(ts)`, `$__time(ts)`) do **not**
   expand in raw psql — substitute literal bounds (e.g.
   `ts BETWEEN now()-interval '6 hours' AND now()`) when testing:
   ```bash
   SQL="...generated query with literal bounds..."
   echo "SELECT ... FROM ( $SQL ) r ..." | bash scripts/verdify-db.sh prod
   ```
   `scripts/verdify-db.sh prod` kubectl-execs psql in the `verdify-db-0` pod
   (read-only SELECTs are safe; never run destructive prod DB work without a gate).
3. **Regenerate the CMs:** `python3 scripts/gen-grafana-dashboard-cms.py`
   (validates JSON, warns near the 1 MiB limit). CI enforces this:
   `make grafana-cm-check` (`--check` mode, run by `scripts/ci-local.sh`)
   fails on any source↔CM drift (#392).
4. **Commit + push** (`grafana/dashboards/*.json` + the regenerated CM) so
   git == live.
5. **Deploy the CM** (server-side):
   ```bash
   kubectl apply --server-side --force-conflicts --field-manager=codex-ssa \
     -n verdify-prod -f deploy/k8s/components/grafana/generated/dashboards-cm-0.yaml
   ```
6. **Reload.** Either wait for the 300 s provisioner cycle, or to **iterate
   fast** force it (≈15 s graphs.verdify.ai blip — Grafana is stateless,
   re-provisions on boot):
   ```bash
   kubectl -n verdify-prod rollout restart deploy/verdify-grafana
   kubectl -n verdify-prod rollout status deploy/verdify-grafana --timeout=90s
   ```
7. **Verify by rendering from the PUBLIC edge** (authoritative — it is what
   users see). Do **not** trust the localhost port-forward `/api/dashboards`
   reload-detection; it returns stale results even after the pod has reloaded.
   ```bash
   curl -s -o /tmp/p30.png \
     "https://graphs.verdify.ai/render/d-solo/site-home/site-home?orgId=1&from=now-6h&to=now%2B30h&width=1300&height=470&theme=light&tz=America%2FDenver&panelId=30"
   ```
   Then **view the PNG** and check it at both an overview range and a zoomed-in
   range (zoom exposes resolution problems — see §4).

---

## 3. The homepage climate compliance-band panels

`site-home.json` panels: **id 30 "Temperature Compliance Band"**, **id 31 "VPD
Compliance Band"**, **id 40 "Zone Temperature vs House Band"**. The two
compliance panels (30/31) are the canonical pattern; keep them mutually
consistent.

### Hero layers (the focus — keep these the loudest objects)
- **Target Band** — translucent green corridor that rides the diurnal curve.
  It is the **pinched** band: `low + f·(target−low)` … `high − f·(high−target)`
  where `f = band_track_fraction` (live **0.25**), sourced from `v_band_curve`
  (15-min grid, **projects ~4 days into the future**) + the live `frac` from
  `setpoint_snapshot`.
- **Target** — dashed centerline (`#2E7D32`), the setpoint curve.
- **Greenhouse** — actual temp / VPD trace (the hero line).
- **Outdoor** + **Outdoor Forecast** (grey solid/dashed), **Solar** + **Solar
  Forecast** (yellow fill on a *hidden* 0–1200 axis).

**The graphed pinched band IS the running device's control band.** The device
runs `2026.6.17.2042.dcc6078` (band-compliance, verified from
`diagnostics.firmware_version`) where the pinch is **wired** into band-first
actuation (`apply_band_track_pinch` feeds `determine_mode_band_first` +
`resolve_equipment`: `firmware/lib/greenhouse_logic.h` ~1326/1338/1350/1672/2255)
with `band_track_fraction = 0.25` live — so the controller actively tracks the
pinched band toward the target. (Don't be fooled by `firmware/artifacts/last-good.version`
= `cc1bb19`: that's the rollback floor, which lags the running binary through the
48 h bake.)

When climate sits *outside* the pinched band with actuators idle, it's the
**cooling-priority arbitration** (can't seal-and-mist while venting for heat),
not a wider tolerance — visible on the temp panel firing at the same timestamps.
Change the band itself by editing `crop_band_anchors` / `band_track_fraction`
(the dispatcher pushes to device NVS — no OTA), not the dashboard. The ADR-0004
direction is `band_track_fraction → 0` (#377); the graph reads it live, so a flip
to 0 auto-widens the shaded band to the full crop-tolerance corridor (and the
device floats it). See `docs/band-traceability-contract.md`,
`docs/adr/0004-floating-corridor-control.md`, project memory
`band-single-source-of-truth`, and `docs/reviews/control-and-graphs-state-2026-06-18.md`.

### Equipment overlay
Every climate actuator is drawn as a **fixed-y wide stripe** that blinks on/off —
see the recipe in §4. Both panels show **all** actuators (they are cross-coupled),
consistent color per actuator, escalation-ordered.

---

## 4. The equipment-state STRIPE recipe (the reusable core)

Goal: show each actuator's on/off state as a **wide horizontal stripe at a fixed
y**, crisp at any zoom, no dots, the band/trace unobstructed.

### SQL — one wide-form target **per actuator**, from raw events
Query `equipment_state` **directly** (its rows are state-change *events*) — **not**
a `generate_series`/`time_bucket` grid. Event-based = granular at any zoom; a
30-second pulse renders as a 30-second rectangle, not a 5-min bin.

```sql
-- single actuator (refId per actuator: E,F,G,…):
SELECT $__time(ts),
       CASE WHEN state THEN <y_high>::float8 ELSE <y_low>::float8 END AS "Vent",
       <y_low>::float8 AS "Vent Base"
FROM equipment_state
WHERE greenhouse_id='vallery' AND equipment='vent' AND $__timeFilter(ts)
ORDER BY ts
```

**CRITICAL — use `ELSE <y_low>`, NOT `ELSE NULL`.** With `ELSE NULL`, every ON
event becomes a value with NULLs on both sides; under `lineInterpolation:
stepAfter` it cannot form a line segment, so Grafana renders it as a **point —
even with `showPoints: never`** → a field of dots. Dropping the line to its
`y_low` (the Base level) when off keeps the series **continuous**: no islands →
no dots, and off periods sit flush on Base so the fill is zero-height (invisible).

Composite stripe (e.g. **Misters** = OR of `mister_center/south/west`) — merge
the OR-state per event:
```sql
WITH ev AS (SELECT ts,equipment,state FROM equipment_state
            WHERE greenhouse_id='vallery' AND equipment IN ('mister_center','mister_south','mister_west') AND $__timeFilter(ts)),
     pts AS (SELECT DISTINCT ts FROM ev),
     merged AS (
       SELECT p.ts, bool_or(COALESCE(l.state,false)) AS on
       FROM pts p CROSS JOIN (VALUES ('mister_center'),('mister_south'),('mister_west')) k(key)
       LEFT JOIN LATERAL (SELECT e.state FROM ev e WHERE e.equipment=k.key AND e.ts<=p.ts ORDER BY e.ts DESC LIMIT 1) l ON true
       GROUP BY p.ts)
SELECT $__time(m.ts),
       CASE WHEN m.on THEN <y_high>::float8 ELSE <y_low>::float8 END AS "Misters",
       <y_low>::float8 AS "Misters Base"
FROM merged m ORDER BY m.ts
```

### Per-series render overrides
- ON series `"<Name>"`: `lineInterpolation: stepAfter`, `showPoints: never`,
  `lineWidth: 0`, `fillBelowTo: "<Name> Base"`, `fillOpacity: ~80`, color = the
  actuator color, and **`hideFrom: {tooltip: true, legend: false, viz: false}`**.
  (`spanNulls` is moot once the series is continuous.) The `hideFrom.tooltip`
  matters: the series value is just the **lane y-offset** (e.g. 93.0, 101.4) —
  meaningless to a viewer — so it must be **hidden from the hover card** while the
  stripe stays drawn (`viz:false`) and the actuator stays in the legend
  (`legend:false`). Without this the tooltip lists junk offsets for every relay.
- Base series `"<Name> Base"`: same color, `lineWidth: 0`, `fillOpacity: 0`,
  `lineInterpolation: stepAfter`, `showPoints: never`,
  `hideFrom: {legend:true, tooltip:true, viz:false}` (invisible fill anchor).
- The fill renders only where the ON series rises above Base (i.e. while ON) →
  crisp rectangle whose width = the real run duration.

**Reference implementation:** `site-climate-cooling.json` panel "Cooling
Equipment State" is the original of this pattern (`ELSE 0` + stepAfter +
showPoints never, fill-to-baseline). `site-home.json` panels 30/31 are the
**gutter-overlaid** variant (fill between two fixed lane lines instead of to
zero), which lets the stripes sit in clear gutters over the climate chart.

### Layout law — fixed gutters, escalation order
- Park stripes in **gutters clear of the data envelope**, not on top of the band
  (lanes that ride the curve become unreadable spaghetti — see §6).
- **Distance from the centerline encodes escalation rank: the least-frequently-
  used actuator is furthest from center.** First-to-fire near the band edge,
  last-resort furthest out.
- **Which actuators on which side** comes from the firmware escalation ladder
  and cross-coupling:
  - TEMP above (too hot → cool/evap): Vent → Fan 1 → Fan 2 → Fog → Misters
  - TEMP below (too cold → heat): Heat 1 (band interior) → Heat 2 (low edge)
  - VPD above (too dry → humidify): Fog → Misters
  - VPD below (too wet → dehum): Vent → Fan 1 → Fan 2 → Heat 1 → Heat 2
  - Cross-coupling: **Heat = cold + wet; Fog = hot + dry; Vent/Fans = hot +
    wet(dehum); Misters = dry (+ evap-cool on temp).** Each appears on both
    panels at matching timestamps — so you can *see* arbitration (e.g. VPD rides
    high while the temp panel shows venting, because you can't seal-and-mist
    while venting for heat).

### VPD axis crowding — the top-rail + sub-zero solution
Humidify equipment (Fog/Misters) fires when VPD is **high (dry)** — the same
region the data spikes into. So:
- **Dry-side stripes → a top rail** above the data (≈4.20–4.55 kPa).
- **Wet-side stripes → a sub-zero status lane** below the data (0 → −0.96 kPa;
  VPD is never negative, so it's free gutter space).
- **Pin the VPD axis hard** (`min: -1.0, max: 4.6`) so the rail and lane never
  autoscale-drift. (Temp uses soft bounds 44/104.)

### Current homepage stripe coordinates (panels 30 temp °F / 31 vpd kPa)
| Actuator | key | temp side / y_low–y_high | vpd side / y_low–y_high | color |
|---|---|---|---|---|
| Vent | `vent` | above 90.6–93.0 | below −0.16–0.0 | `#90A4AE` |
| Fan 1 | `fan1` | above 93.4–95.8 | below −0.36–−0.20 | `#4DB6AC` |
| Fan 2 | `fan2` | above 96.2–98.6 | below −0.56–−0.40 | `#5C6BC0` |
| Fog | `fog` | above 99.0–101.4 | above 4.20–4.36 | `#4FC3F7` |
| Misters | `mister_center∥south∥west` | above 101.8–104.2 | above 4.39–4.55 | `#E040FB` |
| Heat 1 (Electric) | `heat1` | below 52.0–54.4 | below −0.76–−0.60 | `#FF9800` |
| Heat 2 (Gas) | `heat2` | below 48.4–50.8 | below −0.96–−0.80 | `#F4511E` |

(3 mister circuits merge into one "Misters" stripe to cut clutter; South `#CE93D8`
/ West `#F48FB1` are retired from these two panels.)

---

## 5. Gotchas (hard-won — read before touching graph SQL/rendering)

- **Dots:** `ELSE NULL` for off-state → NULL-island points that render even with
  `showPoints: never`. Use **`ELSE <y_low>`** (continuous). (§4)
- **Not granular at zoom:** `generate_series`/`time_bucket` quantizes state. Use
  **raw `equipment_state` events**. (§4)
- **Panel defaults fight you:** the timeseries default is `spanNulls: true` +
  `lineInterpolation: smooth`. Heroes want those; equipment stripes need
  `stepAfter` set **per-series via override** (don't change the panel default or
  the band/trace go stepped).
- **Reload is 300 s,** not instant. Force with `rollout restart deploy/verdify-grafana`
  (~15 s blip) when iterating.
- **Verify from the public edge, not the port-forward.** `localhost:3000
  /api/dashboards/uid/...` reload-detection is unreliable (returns stale for
  minutes); the public `/render` is authoritative.
- **Apply the CM server-side only** (~750 KB > etcd client-side annotation limit).
- **JSON formatting:** `indent=2, ensure_ascii=True` + trailing newline → exact
  byte round-trip → clean diffs.
- **SQL macros don't expand in psql:** substitute literal bounds to test.
- **Do not use the Grafana admin API as an authoring path.** The required admin
  and renderer Secret key names are delivered out of band and must be verified
  without reading their values before any rollout. Production remains a
  task-authorized manual sync; dashboards remain Git-provisioned, so preview
  through the validated deployment path.
- **Band panels project into the future** (`now+30h`); `equipment_state` only
  exists up to now, so stripes correctly stop at "now".
- **`rg` is unreliable in this repo** (silently misses, esp. `.sql`); use
  `grep -rnE` or Python.

---

## 6. How the current homepage equipment overlay was arrived at

The design space and the dead-ends, so we don't repeat them:

1. **Goal:** drive both climate graphs to consistency — show *all* equipment on
   both, lay it out "in the escalation sequence, at offsets from the setpoint it
   influences, least-frequently-used furthest from centerline."
2. **Dead-end A — lanes that ride the band curve at each actuator's trigger
   offset.** Result: 7–9 thin lines bunched in a narrow vertical band,
   overlapping each other and the trace → unreadable spaghetti. **Rejected.**
   Lesson: equipment belongs in **gutters clear of the data**, not on the band.
3. **Owner correction:** "fixed offset **wide stripe** on/off state is better
   than trying to follow the curve." → fixed-y stripes, not curve-following.
4. **Design pass (multi-agent workflow):** five independent Grafana/UX designs →
   3-judge panel → builder. Winner: *"Faithful Wide-Stripe Restoration with
   Escalation-Ranked Gutters + Dry-Side Top Rail"* — restore the wide-stripe
   aesthetic, add the missing actuators (Vent, misters on temp, fans on vpd),
   fix escalation order + colors, and solve VPD crowding with a **top rail +
   sub-zero status lane** rather than a second axis.
5. **Dead-end B — `ELSE NULL` off-state → dots** (NULL islands; §4). **Fixed**
   with `ELSE y_low` + event-based queries (mirroring the proven "Cooling
   Equipment State" panel) → crisp, granular, dotless.

Design principles that came out of it (apply to future control-overlay graphs):
- The **band + greenhouse trace are the heroes**; equipment is secondary, in
  gutters, quiet until ON.
- **Position encodes meaning**: side = which dimension/direction the actuator
  pushes; distance-from-center = escalation rank (rare = far).
- **Consistency across sibling panels** (same layout law, same colors, same
  stripe geometry) so temp and VPD read as one system.
- The overlay should make the **control story legible** (cross-coupling +
  arbitration visible across the two panels).

---

## 7. Quick checklist

```
[ ] edit grafana/dashboards/<name>.json (source of truth)
[ ] test any new SQL read-only on prod (literal bounds for $__ macros)
[ ] python3 scripts/gen-grafana-dashboard-cms.py
[ ] commit + push (json + generated CM)
[ ] kubectl apply --server-side --force-conflicts -n verdify-prod -f .../dashboards-cm-0.yaml
[ ] (fast iterate) kubectl -n verdify-prod rollout restart deploy/verdify-grafana
[ ] render from https://graphs.verdify.ai/render/d-solo/<dash>/<dash>?...&panelId=N — VIEW it
[ ] check at overview AND zoomed-in time ranges
```
