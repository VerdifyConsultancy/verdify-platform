# MQTT fan-out env-contract (#113 publish-all / #114 subscribe)

**Status:** DESIGN / authored — NOT deployed. The broker workload, the prod
publish-all ConfigMap, and the dev subscribe ConfigMap all live in-tree and
render green, but no env beyond prod publishes and only `overlays/dev` carries
the subscribe contract today. DEPLOY is M3, gated on prod being a real cluster
instance plus Nexus cross-VLAN / cross-namespace `1883` flow validation
(`needs:nexus`). This doc is the authoritative env-key contract between the
publisher and the subscribers so a future stage subscribe-overlay (or a second
dev env) can be wired without re-deriving it.

This bus carries **telemetry only**. It does **not** change the single-writer
posture: only the prod ingestor talks to the ESP32, and the `#79`
`VERDIFY_DEVICE_WRITE_ENABLED` gate is independent of every key below. Nothing
here is a device writer.

## Topology

```
                 prod ingestor (the ONLY telemetry capturer + the ONLY device writer)
                 VERDIFY_MQTT_PUBLISH_ALL=1
                          │  publishes every flushed row (best-effort, QoS 0)
                          ▼
              ┌────────────────────────────┐
              │  verdify-mqtt (broker)      │  eclipse-mosquitto:2
              │  prod overlay ONLY          │  ClusterIP :1883, emptyDir, no PVC
              │  allow-mqtt-fanout NetPol   │  allow_anonymous, persistence false
              └────────────────────────────┘
                          ▲                ▲
                          │ subscribe      │ subscribe
                 dev ingestor       (future) stage ingestor
                 VERDIFY_INGEST_SOURCE=mqtt-subscribe
                 mirrors into its OWN per-env DB; opens NO ESP32 / HA / occupancy loop
```

The broker is reachable **cross-namespace** as
`verdify-mqtt.verdify-prod.svc.cluster.local`. The in-namespace prod publisher
can use the short name `verdify-mqtt`; cross-namespace subscribers MUST use the
FQDN. The `allow-mqtt-fanout` NetworkPolicy accepts `1883` from the in-namespace
ingestor (publisher) and from any namespace labelled
`app.kubernetes.io/part-of=verdify` (subscriber envs), so a new subscriber
namespace does not require a policy edit.

## Env keys

All keys are read from the environment at call time. Both modes are **default
OFF** — an ingestor with none of these set is the current VM-side single
ingestor and is completely unaffected (`publish_all_enabled()` and
`subscribe_mode_enabled()` both return `False`, fan-out is inert).

### Publisher (prod ONLY)

Set in `deploy/k8s/overlays/prod/publish-all-configmap.yaml` (strategic-merge
patch onto base `verdify-config`):

| Key | Prod value | Meaning |
|---|---|---|
| `VERDIFY_MQTT_PUBLISH_ALL` | `"1"` | Enables publish-all. **Exact-string `1`** — mirrors the `#79` device-write gate, so `true`/`yes`/`2` do NOT enable it. Set HERE and only here. |
| `FANOUT_MQTT_HOST` | `verdify-mqtt` | Broker host. Short name resolves in-namespace for the prod publisher. |
| `FANOUT_MQTT_PORT` | `"1883"` | Broker port. |
| `FANOUT_MQTT_TOPIC_ROOT` | `verdify/fanout` | Topic prefix. MUST match the subscribers. |

A bus outage NEVER blocks the prod DB write: `FanoutPublisher.publish_row()`
swallows transport errors (telemetry capture is Track A; the prod DB is the
source of truth).

### Subscriber (dev / stage)

Set in `deploy/k8s/overlays/dev/env-configmap.yaml` (and the same shape in a
future stage subscribe-overlay):

| Key | Subscriber value | Meaning |
|---|---|---|
| `VERDIFY_INGEST_SOURCE` | `mqtt-subscribe` | Ingest telemetry FROM the bus only. Case-insensitive exact match. Opens NO ESP32 / HA / occupancy loop. |
| `VERDIFY_DEVICE_WRITE_ENABLED` | `"0"` | `#79` interlock pinned OFF. A subscriber can never write the device. |
| `FANOUT_MQTT_HOST` | `verdify-mqtt.verdify-prod.svc.cluster.local` | Cross-namespace FQDN of the prod broker. |
| `FANOUT_MQTT_PORT` | `"1883"` | Broker port. |
| `FANOUT_MQTT_TOPIC_ROOT` | `verdify/fanout` | Topic prefix. MUST match the publisher. |

### Optional auth (both sides, not set today)

| Key | Default | Meaning |
|---|---|---|
| `FANOUT_MQTT_USER` | `""` (anonymous) | Username. If set, layered via a `password_file` mounted from a Secret without changing the broker manifest shape. |
| `FANOUT_MQTT_PASS` | `""` | Password. **Never** commit a value — Secret reference only. |

The in-cluster bus runs `allow_anonymous true`: acceptable because it is
ClusterIP-only and firewalled by `allow-mqtt-fanout`. Real creds are a layer-on,
not a manifest reshape.

## Mutual-exclusion invariant

`assert_modes_consistent()` (called at ingestor startup) fails loudly if a
single process has BOTH `VERDIFY_MQTT_PUBLISH_ALL=1` AND
`VERDIFY_INGEST_SOURCE=mqtt-subscribe`. An ingestor is either the prod publisher
OR a dev/stage subscriber, never both — running both would re-publish what it
just consumed (a self-feeding topic storm). The overlays enforce this by
construction: only prod patches `publish-all-configmap.yaml`; only the
subscribe overlays set `VERDIFY_INGEST_SOURCE=mqtt-subscribe`.

## Topic + payload layout

```
{FANOUT_MQTT_TOPIC_ROOT}/{table}/{greenhouse_id}
e.g. verdify/fanout/climate/vallery
```

- `retain=False`, `qos=0` — telemetry is a stream, not retained state.
- Payload is a JSON envelope `{"table", "greenhouse_id", "row"}`; timestamps are
  ISO-8601 UTC.
- Allow-listed tables (see `ingestor/mqtt_fanout.py::FANOUT_TABLES`):
  `climate`, `equipment_state`, `system_state`, `setpoint_snapshot`,
  `diagnostics`. A new write path does NOT silently leak onto the bus — it must
  be added to the allow-list. Subscribers drop any payload for a
  non-allow-listed table (`decode_payload` raises `ValueError`).

## Why staging is not a subscriber yet

`overlays/staging` is the only overlay the live `verdify-local-staging` ArgoCD
app syncs. It stays inert for the fan-out bus: `ingestor` at `replicas:0`,
`VERDIFY_DEVICE_WRITE_ENABLED=0`, and the broker excluded (it is a kustomize
Component referenced only by `overlays/dev` + `overlays/prod`). Wiring staging
as a subscriber means copying the dev subscriber key block above into a staging
env patch and lifting `replicas` to `1` — a separate, gated change, not part of
this DESIGN pass.

## Deploy gates (for the M3 DEPLOY pass — NOT this PR)

1. prod must be a real cluster instance (today prod is a validated target shape,
   inert on merge — no `verdify-prod` ArgoCD Application reconciles it).
2. Nexus cross-VLAN / cross-namespace `1883` flow validated from a pod
   (`needs:nexus`).
3. No device contact is introduced by any deploy of this component — verify the
   render still excludes the broker from `overlays/staging` and that no
   subscriber overlay carries `VERDIFY_DEVICE_WRITE_ENABLED=1`.

## Source of truth

- Broker workload: `deploy/k8s/components/mqtt-broker/mqtt-broker.yaml`
- Component wiring: `deploy/k8s/components/mqtt-broker/kustomization.yaml`
- Prod publish-all patch: `deploy/k8s/overlays/prod/publish-all-configmap.yaml`
- Dev subscribe patch: `deploy/k8s/overlays/dev/env-configmap.yaml`
- Ingestor fan-out code (gating + topic + publisher/subscriber):
  `ingestor/mqtt_fanout.py`, `ingestor/ingestor.py`, `ingestor/config.py`
