# HA grow-light command ownership

The ESP32 native-API writer invariant applies to climate/setpoint commands from
`verdify-ingestor`. The two Lutron grow-light switches have a separate HA path
with three declared origins: the ESP32's on-chip lighting policy, the Verdify
setpoint server's manual HTTP route, and the HA exterior master override while
its boolean is on. The exact source and target mapping is in
[`SERVICE_MAP.md`](../SERVICE_MAP.md#device-command-ownership).

Use this read-only check after changing a light route or its image. It does not
toggle lights or restart either writer:

```sh
kubectl -n verdify-prod get deploy verdify-ingestor verdify-setpoint-server \
  -o custom-columns=NAME:.metadata.name,GEN:.metadata.generation,OBS:.status.observedGeneration,REPLICAS:.spec.replicas,READY:.status.readyReplicas,STRATEGY:.spec.strategy.type,IMAGE:.spec.template.spec.containers[0].image
kubectl -n verdify-prod get pods -l app.kubernetes.io/component=setpoint-server \
  -o custom-columns=NAME:.metadata.name,UID:.metadata.uid,PHASE:.status.phase
bash scripts/k3s-smoke.sh device-monitor  # exactly one ESP32 native-API connection
```

From a pod with its HA token already mounted, use HA `GET /api/states` to read
only these IDs: `switch.greenhouse_main`, `switch.greenhouse_grow`, the
`switch.greenhouse_grow_light_{main,grow}` ESPHome proxies,
`input_boolean.exterior_lights_override`, and
`timer.exterior_lights_override`. Check for any active greenhouse or grow-light
HA automations. Never print the token. `GET /api/logbook` can show state
transitions, but a state transition alone does **not** identify which caller
sent a service command; use HA trace/context records if event provenance is
required. Do not infer physical Lutron load state from the Alarm.com
`light.greenhouse_{main,grow}` wrappers.

The 2026-09-28 read-only snapshot found one Ready `verdify-setpoint-server`
pod with `replicas=1`, `strategy=Recreate`, image digest
`sha256:aae6c8ba5aa79f3103be95c4bf21c553a1e6968bdc19dfc4f7415cbc1b3ba5ab`.
Both canonical Lutron switches and both ESPHome proxy switches were `off`;
the exterior override boolean was `off` and its timer `idle`. HA had the
exterior override engage/enforce/release automations loaded, and no HA
automation entity named for a greenhouse or grow-light policy. The separate
Alarm.com `light.greenhouse_grow` wrapper reported `on` while the canonical
Lutron switch was `off`, demonstrating why wrapper state is not a command
confirmation. This snapshot establishes presence and state, not the origin of
each prior Lutron transition.

The focused source assertion is `tests/test_ha_light_writer_boundary.py`.
It accepts both exact Lutron targets, rejects climate switches, ESPHome proxy
targets, Alarm.com wrappers, and unsupported services before an HA request,
checks the firmware's HA light targets, and fails a second-pod or
`RollingUpdate` manifest fixture. The HA exterior override source belongs to
`jvallery/homeassistant`; its current `origin/main` path is
`packages/controls_global/lighting_master_override.yaml`.
