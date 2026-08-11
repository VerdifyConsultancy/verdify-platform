# GATED Runbook — Band Dashboard Deploy + Live `crop_target_profiles` Apply

**Status:** GATED. Lane laptop-root LANE C firmware-optimization (#249; #250–#253).
**Author:** laptop-root. **Date:** 2026-06-07.
**Gate:** every step that mutates **prod** (`verdify-prod`) is **root executor**,
snapshot-first. The dashboard deploy is read-only-viz (additive). No firmware flash. No secrets printed.

Companion: `docs/runbooks/verdify-band-tuning-safe.md` (the safe-iteration loop, from #258) and
`docs/proposals/verdify-band-redesign-2026-06-07.md` (the design + shadow analysis).

---

## A. Dashboard deploy (#252) — additive, read-only viz, safe

The "Band Tuning — Diurnal Adjustment" dashboard (`uid band-tuning-diurnal`) ships as a
**separate** ConfigMap `verdify-grafana-dashboards-2` mounted at
`/etc/grafana/provisioning/dashboards/json/bucket2`. It does NOT edit the existing
`dashboards-cm-{0,1}` and does NOT collide with any other resource. All 6 panels' SQL is
verified against live prod (read-only SELECTs over `crop_target_profiles` + `fn_band_setpoints`).

**The `verdify-prod-dark` ArgoCD app is manual-sync and currently OutOfSync** — a full
`argocd app sync` could pull in OTHER lanes' pending drift. So deploy the dashboard with a
**targeted additive apply**, NOT a full sync:

```bash
# 1. Apply ONLY the new ConfigMap (harmless on its own — grafana ignores it until mounted).
ssh jason@192.168.30.32 "sudo k3s kubectl apply -n verdify-prod -f -" \
  < deploy/k8s/components/grafana/generated/dashboards-cm-2.yaml

# 2. Patch the grafana Deployment to add the bucket2 volume + mount (additive).
ssh jason@192.168.30.32 "sudo k3s kubectl -n verdify-prod patch deploy verdify-grafana --type=json -p='[
  {\"op\":\"add\",\"path\":\"/spec/template/spec/volumes/-\",
   \"value\":{\"name\":\"dashboards-2\",\"configMap\":{\"name\":\"verdify-grafana-dashboards-2\"}}},
  {\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/volumeMounts/-\",
   \"value\":{\"name\":\"dashboards-2\",\"mountPath\":\"/etc/grafana/provisioning/dashboards/json/bucket2\",\"readOnly\":true}}
]'"

# 3. Verify (the file provider rescans every 300s; or restart rollout to force).
ssh jason@192.168.30.32 "sudo k3s kubectl -n verdify-prod rollout status deploy/verdify-grafana --timeout=120s"
# Then in grafana: Dashboards -> Site folder -> 'Band Tuning — Diurnal Adjustment'.
```

The PR to `live/platform-main` (this branch) makes the same change **durable** so the next
`verdify-prod-dark` sync keeps it (the kubectl patch and the git change are identical — no
drift introduced). Re-probe ≥10 min after: the dashboard still renders, panels non-empty.

> If you prefer GitOps-only: merge the PR, then `argocd app sync verdify-prod-dark
> --resource ':ConfigMap:verdify-grafana-dashboards-2' --resource 'apps:Deployment:verdify-grafana'`
> to sync ONLY these two resources (avoids pulling unrelated drift).

---

## B. Live band apply (#250 / #251) — the orchid VPD realign — GATED

> **DO NOT RUN WITHOUT JASON.** This changes the live setpoints the dispatcher pushes to the
> sole device writer. The proposed migration is `db/migrations/160-orchid-vpd-band-realign-PROPOSAL.sql`
> (lives on `main`; dev-tested 2026-06-07). It re-authors the orchid VPD ideal band + widens the
> VPD stress envelope to 1.62. It does NOT touch temp, dli, or any other crop.

### B.1 Pre-flight (read-only)
```bash
# Snapshot the CURRENT orchid curve to a timestamped backup table (reversible).
ssh jason@192.168.30.32 "sudo k3s kubectl exec -n verdify-prod verdify-db-0 -c postgres -- \
  psql -U verdify -d verdify -c \"
  CREATE TABLE IF NOT EXISTS crop_target_profiles_bak_$(date +%Y%m%d_%H%M) AS
  SELECT * FROM crop_target_profiles WHERE crop_type='orchid';\""

# Record the current served setpoints + 7d compliance baseline (for the re-probe).
ssh jason@192.168.30.32 "sudo k3s kubectl exec -n verdify-prod verdify-db-0 -c postgres -- \
  psql -U verdify -d verdify -c \"SELECT * FROM fn_band_setpoints(now());\""
```

### B.2 Gated apply (verify the exact target and prerequisites)
```bash
# Apply 160 to PROD. Idempotent + transactional; the feasibility DO-block rolls back
# automatically if any width<0.30 or ideal outside stress.
ssh jason@192.168.30.32 "sudo k3s kubectl exec -i -n verdify-prod verdify-db-0 -c postgres -- \
  psql -U verdify -d verdify -v ON_ERROR_STOP=1 -f -" \
  < db/migrations/160-orchid-vpd-band-realign-PROPOSAL.sql

# Re-resolve: fn_band_setpoints(now()) must reflect the new VPD ceiling.
ssh jason@192.168.30.32 "sudo k3s kubectl exec -n verdify-prod verdify-db-0 -c postgres -- \
  psql -U verdify -d verdify -c \"SELECT * FROM fn_band_setpoints(now());\""
```

The dispatcher/ingestor refresh their served band on their next cycle; the device writer (node4
ingestor) is **never touched directly** — it consumes the resolved setpoints. (If a forced refresh
is desired, the ingestor-owned `refresh_achievable_envelope` + `mv_zone_band_grade` matview bounce
is `kubectl rollout restart deploy/verdify-ingestor -n verdify-prod` — **HELD: do NOT restart the
live ingestor without the required preflight** per LANE C guardrail.)

### B.3 Re-probe (DURABILITY GATE — ≥60 min, control-plane band)
```bash
# Watch graded compliance does not REGRESS and climate stays inside the (now wider) band.
ssh jason@192.168.30.32 "sudo k3s kubectl exec -n verdify-prod verdify-db-0 -c postgres -- \
  psql -U verdify -d verdify -At -F'|' -c \"
  SELECT zone, round(avg(graded_vpd_compliance_pct)::numeric,1)
  FROM daily_zone_compliance WHERE date >= (now()-interval '2 days')::date
    AND zone='center' GROUP BY zone;\""
```
Expected: center VPD compliance trends UP toward the shadow-predicted ~58% in-ideal (was ~19–45%).
Record `GREEN at <T>, re-verified at <T+60min>` with the literal probe.

### B.4 Rollback (one step away)
```bash
ssh jason@192.168.30.32 "sudo k3s kubectl exec -n verdify-prod verdify-db-0 -c postgres -- \
  psql -U verdify -d verdify -c \"
  UPDATE crop_target_profiles t SET vpd_ideal_min=b.vpd_ideal_min, vpd_ideal_max=b.vpd_ideal_max,
         vpd_stress_high=b.vpd_stress_high, source=b.source
  FROM crop_target_profiles_bak_<TS> b
  WHERE t.crop_type='orchid' AND t.season=b.season AND t.hour_of_day=b.hour_of_day
    AND t.growth_stage=b.growth_stage AND t.greenhouse_id=b.greenhouse_id;\""
```

---

## C. #253 season-resolver guard (already-green; apply anytime)

`db/migrations/159-band-season-resolver-guard.sql` is a read-only assertion (mutates nothing).
Apply to dev AND prod freely — it FAILS LOUDLY only if a resolver ever regresses to a hardcoded
season. The live resolver chain is already season-aware (verified 2026-06-07), so it passes today.

```bash
ssh jason@192.168.30.32 "sudo k3s kubectl exec -i -n verdify-prod verdify-db-0 -c postgres -- \
  psql -U verdify -d verdify -v ON_ERROR_STOP=1 -f -" < db/migrations/159-band-season-resolver-guard.sql
# Expect: NOTICE  #253 guard OK: N band resolver function(s) are season-aware.
```
