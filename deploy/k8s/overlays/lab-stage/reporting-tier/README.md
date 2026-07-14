# Lab stage reporting tier (source-only)

This nested Kustomize overlay records the exact Phase 4c pass-1 integration
contract. It is deliberately absent from the parent `lab-stage` kustomization,
creates no public route, and renders both Deployments at `replicas: 0`. Nothing
in this directory is evidence that a reporting feed, credential, image,
object-store binding, or live 143+2 release exists.

The resource-name contract is fixed:

- private renderer gateway: `verdify-lab-reporting-tier:8080`;
- operator projection: `verdify-lab-reporting-projection`, PostgreSQL on 5432
  and the closed source-watermark endpoint on 8080;
- reporting reader: `verdify-lab-reporting-reader` with `PGUSER`, `PGPASSWORD`,
  and `PGDATABASE`;
- Grafana runtime: `verdify-lab-reporting-runtime` with
  `GRAFANA_ADMIN_PASSWORD` and `GRAFANA_RENDERER_TOKEN`;
- one-shot producer: ServiceAccount and Deployment
  `verdify-lab-occurrence-producer`, with command
  `node /app/scripts/run-reporting-occurrence-producer.mjs once`;
- existing occurrence-store name contract:
  `verdify-lab-occurrence-store-metadata` and
  `verdify-lab-occurrence-store-writer`.

The operator projection is not implemented here. The Service has a selector but
no matching workload, so it has no endpoints. `projection-readiness.sql` is a
verification-only, read-only transaction: it creates no role, schema, view,
table, grant, or credential. It requires a distinct non-Track-A reader, only
selectable views/materialized views, no object-creation or relation-write
privileges, no selectable non-system relation outside `lab_reporting`, and
exactly one canonical `lab-public-v1` source-watermark row. The
reader's default schema must be `lab_reporting`, so the existing dashboards'
unqualified table/view names cannot resolve against a primary schema. The
private HTTP adapter must expose that same row at `/v1/source-watermark` using
the closed response consumed by `reporting-tier-runtime.mjs`.

The generated assets pin 18 dashboards, 139 unique panels, and 143 graph
occurrences to source manifest
`e455309736cf785914141d1641ec2569f623e048cf9073b3ea6fce181726160d`.
Grafana 12.4.5 and renderer 5.10.0 are immutable-digest pinned. Anonymous and
basic auth are off. Only the loopback nginx gateway supplies the fixed auth
proxy identity, and it exposes only `/healthz` plus the 18 exact dashboard UID
render paths. The renderer reuses the 5.10 environment contract already pinned
by the production Grafana component (`SERVER_ADDR`, `AUTH_TOKEN`, `LOG_LEVEL`,
`RATE_LIMIT_*`, readiness timeout, timezone, and `HOME`). Pass 1 limits both
Grafana and the renderer to two concurrent renders, gives Chromium a 4 GiB / 2
CPU ceiling plus 512 MiB `/dev/shm`, and allows five minutes of startup before
liveness can intervene. No Ingress or IngressRoute is present.

## Deliberate activation blockers

It is not safe to raise either replica count from zero yet. The overlay carries
a zero-digest exporter sentinel; its current packaged exporter target is
offline-only and does not contain the runtime command. The canonical source
manifest and approved-policy ConfigMaps are also intentionally absent. The
library validates policy/manifest semantics and the canonical manifest digest,
but no exporter image yet bakes and verifies the exact Jason-approved policy
byte digest. A mutable ConfigMap alone does not satisfy that executable binding.
operator projection, both Secrets, and occurrence-store metadata/writer binding
must exist and pass their separate reviews. NetworkPolicy currently permits the
producer to reach only DNS, the private reporting gateway, and the projection's
private watermark endpoint; public-camera and object-store egress remain absent
until their reviewed integration is added.

Before any activation patch, independently render this directory and require:

1. the projection verifier passes using the reporting reader without logging
   credential values;
2. the projection Service has ready endpoints on both named ports;
3. a reviewed exporter digest packages the one-shot runtime and matches the
   exact source revision;
4. the canonical source manifest and Jason-approved policy are mounted;
5. camera and object-store egress are constrained through reviewed endpoints;
6. object-store selector reads and conditional writes pass against the real
   stage store; and
7. T0 then T+10 acceptance proves all 143 graph and two camera occurrences,
   a true source watermark, no pending/invalid records, and LKG/freshness
   behavior.

Until item 7 passes, this work must not be described as a successful 143+2
rollout.
