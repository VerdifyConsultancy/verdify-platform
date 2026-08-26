# Firmware digital-twin shadow — runbook (#34 / #255 / FW-OPT-6)

The merged-but-not-deployed twin (twin/Dockerfile + twin/offline_driver.py +
db/migrations/155-twin-observability-tables.sql + the
firmware-twin-divergence Grafana dashboard) stood up in k3s as a **read-only,
INSERT-only SHADOW**. Design: `docs/design/firmware-digital-twin.md` §2.1, §5.3,
§6.

## Historical dev shadow proof (retired environment)

The `verdify-dev` namespace/overlay is now retired. The record below is kept as
historical proof of the shadow pipeline, not as a current deployment target.

1. `db/migrations/155-twin-observability-tables.sql` applied to the dev DB
   (additive: net-new `twin_decisions` + `firmware_twin_divergence` hypertables
   + `twin_ro` NOLOGIN role; idempotent; no ALTER of live data).
2. A `twin` LOGIN user grafted onto `twin_ro`; password sealed into
   `verdify-twin-secrets` (`TWIN_DB_PASSWORD` + `TWIN_DSN_BASE`), never in a
   manifest. The dev `greenhouses` table was seeded with a **device-key-free**
   `vallery` reference row (only id/name/timezone — NO esp32_api_key) to satisfy
   the `twin_decisions.greenhouse_id` FK.
3. `kubectl apply -k deploy/k8s/components/firmware-twin -n verdify-dev` — the
   shadow Deployment (gcc initContainer compiles `replay_emit_follow` from the
   src ConfigMap; python container runs `offline_driver.py` looping the corpus),
   its egress NetworkPolicy, and `allow-db-from-firmware-twin` (the namespace
   default-denies db ingress and the shared `allow-db-from-app` does not list
   `firmware-twin`).

**Proven:** pod 1/1 Running, 0 restarts; 400 `twin_decisions` rows written via
the INSERT-only role; L2 read-only verified at the DB
(`has_table_privilege('twin','equipment_state','INSERT') = f`,
`...'twin_decisions','INSERT' = t`); all 13 divergence-dashboard panel queries
resolve against the shadow data.

## NOT YET TRUSTWORTHY — the #31 gate (prod-vs-reality divergence)

`replay_emit_follow` itself warns that **48 `sp_*` Setpoints fields are absent
from the export corpus** and fall back to `default_setpoints()`. For any field
the dispatcher tuned away from default, the twin diverges for a CONFIG reason,
not a code reason — manufacturing **false prod-vs-reality alarm**. So this
shadow proves the *pipeline + dashboard render*; it does **NOT** license acting
on the divergence metric. **#31 (setpoint coverage) is the blocker** before the
live prod-vs-reality signal is a gate. The seeded `firmware_twin_divergence` row
is tagged `cmp_twin_ref='SEED-render-proof ...'` so it is never mistaken for a
real measurement.

Also still required for the LIVE shadow (vs the offline corpus replay deployed
here), all buildable-now but NOT built (design §6 Phase-1 / TWIN-5):
- a live as-of-join feed adapter (parameterize `export-replay-overrides.sh`'s
  inner SELECT with `SINCE_TS` so one SQL body serves batch + live tail);
- the local-hour MDT correction (`AT TIME ZONE 'America/Denver'`) + `dt_ms`
  5 s cap in a live driver (the offline corpus already carries UTC ts).

## PROTECTED BY RUNTIME SAFEGUARDS — any future prod-DB shadow

Standing up the shadow in **verdify-prod** is a **live-prod schema change**
(migration 155 on the prod DB) + a prod `twin` login user. It is additive /
idempotent / non-destructive, but per the change-gating rule it needs an
explicit go + a DB snapshot first. When validated:

```
# 1. snapshot the prod DB (PITR/backup already runs; take a fresh logical dump).
# 2. apply migration 155 (additive, idempotent):
cat db/migrations/155-twin-observability-tables.sql \
  | kubectl exec -i -n verdify-prod verdify-db-0 -c postgres -- \
      psql -U verdify -d verdify -v ON_ERROR_STOP=1
# 3. create the prod twin login user + seal verdify-twin-secrets in verdify-prod
#    (prod greenhouses already has 'vallery' — no seed needed).
# 4. kubectl apply -k deploy/k8s/components/firmware-twin -n verdify-prod
# 5. wire the divergence dashboard into the grafana bucket CM + mount (same
#    pattern as band-tuning cm-2) — coordinate with the in-flight grafana PR to
#    avoid a duplicate-resource collision.
```

The twin remains read-only / INSERT-only / no-device-route in prod too (L1–L4).
The durable form replaces the gcc-initContainer with the pre-baked
`twin/Dockerfile` image (GHCR-pinned) once a build/push is wired.
