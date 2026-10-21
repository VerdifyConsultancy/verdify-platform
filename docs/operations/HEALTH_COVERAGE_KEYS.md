# Verdify health coverage keys

This is the producer handoff for [#1000](https://github.com/VerdifyConsultancy/verdify-platform/issues/1000), with alert delivery owned by [monitoring-stack #1032](https://github.com/jvallery/monitoring-stack/issues/1032). These are observed metric keys, not a claim that each alert is implemented. The October 20 [metric receipt](../../planning/execution/2026-10-20-followthrough/evidence/1000-observed-metric-keys.json) records exact queried series and values.

## Workload availability

Use `kube_deployment_status_replicas_available{namespace="verdify-prod",deployment="NAME"}` for each exact name below. All five series were present with value 1 in the readback. Required coverage detects both zero available replicas and an individually absent series; a regex-wide absent() alone cannot detect one missing member.

| Deployment | Owning source | Alert gap |
|---|---|---|
| `experiment-v2-freezer` | `deploy/k8s/components/experiment-v2-orchestrator/` | Dedicated zero/absence alert required |
| `experiment-v2-lifecycle` | `deploy/k8s/components/experiment-v2-orchestrator/` | Dedicated zero/absence alert required |
| `experiment-v2-selector` | `deploy/k8s/components/experiment-v2-orchestrator/` | Dedicated zero/absence alert required |
| `verdify-mqtt` | `deploy/k8s/components/mqtt-broker/` | Dedicated zero/absence alert required |
| `verdify-hermes-iris` | `deploy/k8s/components/hermes-iris/` | Dedicated zero/absence alert required |

Namespace ingest/Lease alerts do not establish these availability checks. The same is true of an HTTP endpoint probe that never traverses an internal worker or broker.

## Scheduled work

| Namespace / CronJob | Exact last-success metric selector | Live cadence | Required rule behavior |
|---|---|---|---|
| `verdify-descheduler/descheduler` | `kube_cronjob_status_last_successful_time{namespace="verdify-descheduler",cronjob="descheduler"}` | Hourly, minute 15 | Latest scheduled failure and overdue/missing success; preserve an explicit operator pause |
| `verdify-prod/verdify-fixed-panel-native-freezer` | `kube_cronjob_status_last_successful_time{namespace="verdify-prod",cronjob="verdify-fixed-panel-native-freezer"}` | Daily, local 00:30 | Latest scheduled failure and overdue/missing success; no experiment activation |
| `verdify-prod/verdify-winter-feasibility` | `kube_cronjob_status_last_successful_time{namespace="verdify-prod",cronjob="verdify-winter-feasibility"}` | Daily, local 00:30 | Latest scheduled failure and overdue/missing success; no scientific eligibility inference |

All three last-success series were present. Confirm `spec.timeZone`, deadlines and suspend from live source before choosing thresholds. A historical failed Job must not remain the latest-failure alarm after a newer scheduled success. Kubernetes Job failure telemetry is operational evidence, not proof that an outcome freeze or physical operation was scientifically valid.

## Declared probe addresses 04 / 08 / 09

The [firmware topology](https://github.com/VerdifyConsultancy/verdify-platform/blob/33ed324e9a74aa34892bd35f365ce94d564798c8/firmware/greenhouse/hardware.yaml) declares the following software aliases. HA's current Prometheus export contains `homeassistant_entity_available{job="home-assistant",entity="ENTITY"}` for each named entity; all four values were 0 in the October 20 readback.

| Address | Declared controller alias | HA entity key |
|---|---|---|
| 04 | `south_wall_probe` | `sensor.greenhouse_south_temp_degf`, `sensor.greenhouse_south_rh` |
| 08 | `south_2_soil_probe` | `sensor.greenhouse_south_2_soil_moisture` |
| 09 | `west_soil_probe` | `sensor.greenhouse_west_soil_moisture` |

`homeassistant_sensor_state{entity="sensor.greenhouse_active_probe_count"}` was 4; the aggregate probe-health entity was available. Neither aggregate proves the unavailable per-probe entities recovered. Handle known faults as maintenance information with a pointer to [#751](https://github.com/VerdifyConsultancy/verdify-platform/issues/751); do not add a new readiness gate or manufacture recovery. Physical placement, soil calibration and crop topology remain unqualified under #751/#298 and their existing contracts.

## Shelly meters

| Canonical product item | Device identity | Controller-presence key | Existing service expectation key |
|---|---|---|---|
| `physical-device:iot/ha-shelly-b3a979c8` | MAC `AC15186DAAFC` | `iot:unifi_client_expected{client_id="unifi-211920907986099"}` | `fleet_ip_probe_expected{endpoint_id="unifi-211920907986099",probe_job="fleet-ip-services",probe_target="192.168.10.36:502"}` |
| `physical-device:iot/ha-shelly-2b1c9427` | MAC `08F9E0E7CEB0` | `iot:unifi_client_expected{client_id="unifi-46687778400400"}` | `fleet_ip_probe_expected{endpoint_id="unifi-46687778400400",probe_job="fleet-ip-services",probe_target="http://192.168.10.132:80/"}` |

Both expectation keys exist on both service-contract collectors. Actual availability joins `fleet:ip_probe_available` by `endpoint_id,probe_job,probe_target`; existing `FleetIPProbeUnavailable` covers down/absent services after 30m while the blackbox exporter is healthy. `UniFiIoTClientAbsent` covers controller-presence loss. Avoid duplicate rules if the existing rule is correctly bound. Monitoring #1032 still needs exact two-meter firing/recovery and exporter-outage negative evidence; the current general IoT fixture's healthy meter inputs do not supply that proof.

Targets above identify observed probe series; address reservations remain in [network-infra IPAM](https://github.com/jvallery/network-infra/blob/main/ipam/allocation.md). A successful listener or UniFi presence does not prove fresh channel samples. [Source clock consumer semantics](SHELLY_SOURCE_CLOCK_CONSUMER.md), calibration, reversed CT and circuit scope remain separate.

## Authority and retained obligations

Device-authority references are in [Home Assistant PR #933](https://github.com/jvallery/homeassistant/pull/933), at the committed [authority document](https://github.com/jvallery/homeassistant/blob/8788ae3d75da11681719ca28ea79f7ad16f3f594/docs/operations/GREENHOUSE_DEVICE_AUTHORITY.md). They document existing items under D-06; canonical product ownership and firmware/write authority do not move. The central inventory source-link companion must land before claiming the cross-lane handoff complete.

The retained rehearsal CNPG data/Cluster remains the explicit [#992](https://github.com/VerdifyConsultancy/verdify-platform/issues/992) custody/retirement obligation; no production-CNPG cutover or new long-term rehearsal alert is introduced. Existing substrate health and retained-data protection continue. None of these producer keys alone closes #1000: owner rules must be checked, deployed, loaded in Prometheus, and the meter firing receipt linked.
