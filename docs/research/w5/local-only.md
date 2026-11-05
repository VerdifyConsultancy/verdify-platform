# W5 research: local-only Verdify (images, NTP, Slack, external dependencies)

Track `local-only`. Issues #1351 (external-dependency audit), #1363 (images through the local registry), #1364 (local NTP for the controller) and #1352 (Slack decommission). Code read at origin/main `cf21fa421`; every live read on 2026-11-05 between 20:30Z and 21:15Z, read-only.

## Questions

1. What does Verdify reach outside the estate, by host, purpose and component? Which of these does Jason accept, and which get replaced?
2. For every image that is not local, what is the exact estate reference that replaces it, and what does each workload need before it can pull that reference?
3. Which NTP servers should the controller use, and is the estate NTP service ready for an IoT VLAN 10 client?
4. Who sends to Slack today, and what replaces each sender?

## Bottom line

- **Runtime egress has no enforced "local only" boundary.** In `verdify-prod`, 14 of the 22 running components (api, operator-api, grafana, render-cache, hermes-iris, planner, setpoint-server, vision, mqtt, edge-traefik, db, db-backup-exporter, lab-publisher, plus `verdify-dashboard-web`) are covered only by `egress-deny-management`. That policy allows `0.0.0.0/0` apart from the management IPs. Add a Verdify-owned private-ranges-only egress policy for those components (the vendored network-infra deny stays as is). The only public egress left is then `verdify-outbound` (Open-Meteo only, once the OpenAI and Slack server blocks are gone) and the firmware builder (GitHub and PlatformIO at build time). That policy enforces the decision, and a CI host guard keeps it from drifting.
- **Images: 11 distinct third-party digests, 12 refs.** Two already have custody copies in the zot origin: Traefik `v3.7.16-c8a121e4bb13` (the same digest traefik-apps uses), and python 3.12-alpine (our pin `2d077476…` is the linux/amd64 child of the custody index `3.12-alpine-236173eb7400`). The other nine need a `storage-infra` custody `plan.json` entry: nginx-unprivileged, mosquitto, postgres:16, grafana 12.4.14, the image renderer, esphome, cosign, the descheduler and python 3.13.5-alpine. Hermes is the tenth only if #1350 does not delete it first.
- **New blocker on #1363:** 9 workloads have **no `imagePullSecrets`**: mqtt, traefik, grafana, render-cache, lab-cache-origin, outbound, db-backup-exporter, firmware-builder and firmware-flasher. Namespace `verdify-descheduler` has no `zot-origin-cluster-pull` Secret at all, because it is missing from the reflector allow-list in `agent-fleet-runners`. The zot origin denies anonymous reads, so a bare repin to `registry.vallery.net/...` would fail with ImagePullBackOff. Each repin must add the pull secret, and agents must add `verdify-descheduler` to the reflection regex. #1363 currently says "verdify-prod pods already carry imagePullSecrets". That holds for the first-party workloads only.
- **The node mirror is not enough on its own.** The k3s docs confirm that containerd always tries the upstream default endpoint last, and storage-infra keeps that fallback on. The cache also deletes untagged digests 30 days after their last pull. esphome and cosign are still absent from the cache (HTTP 404 on 2026-11-05). Use custody copies, as in #1363 option B.
- **NTP: set the servers to `192.168.7.54` (VM305 chrony), then `192.168.100.1` (the Starlink dish).** Remove `192.168.10.1`, which does not serve NTP, and `pool.ntp.org`. The live data proves failover works: the first server refuses udp/123 today and the clock still syncs from the second, with 0 invalid samples in 30 days apart from 2 at a reboot. A second server is worth having. VM305 lost its reply routing from 13:37Z to 19:38Z today (network-infra#953/#954), and the dish covers an outage like that.
- **Slack has 7 code senders, not 5.** Besides the alert, brief, midnight-watch and two heartbeat paths, `scripts/forecast-action-engine.py` (run by the ingestor's forecast task) posted **31 "Forecast Alert" messages in 30 days** that exist only in Slack and `forecast_action_log`, with no `alert_log` row. Under estate Alertmanager, Verdify Prometheus alerts also fall through to the `default`/`orbit-actionable` Slack receivers. The replacement is `alert_log` → dashboard, plus a `verdify_alert_open` gauge → a monitoring-stack rule labelled `local_push="true"`. That label already routes to `ha-local-push`; `VerdifyControllerDark` uses it today. Add a terminal `area="verdify"` route so nothing Verdify reaches Slack.
- **Paging set default: severity=critical only.** That was 38 alerts across 6 types in 30 days (about 1.3 a day). Warnings (1,372 in 30 days, 368 of them posted to Slack, 210 of those `vpd_stress`) go to the dashboard only.
- Smaller local-only gaps: Grafana still has its gravatar, news-feed and plugin-update checks on. The lab-publisher's npm update notifier calls registry.npmjs.org at runtime. No issue exists yet for moving MCP embeddings to Cortex.

## Evidence from our data

Window: the last 30 days, the longest window covered by both the current SNTP diagnostics columns and the alert-policy shape. The firmware block `servers: [192.168.10.1, pool.ntp.org]` with `CONFIG_LWIP_SNTP_MAX_SERVERS=2` has been unchanged since c9e1133d0 (2026-09-30), so the whole window is valid for NTP.

### Controller clock

```sql
SELECT date_trunc('week',ts)::date wk, count(*) n, sum(1-sntp_valid) not_valid, max(sntp_miss_count) max_miss,
       max(last_sntp_sync_age_s) max_age, percentile_cont(0.5) within group (order by last_sntp_sync_age_s) p50_age,
       round(min(abs(controller_time_epoch - extract(epoch from ts)))::numeric,1) best_abs_offset
FROM diagnostics WHERE ts > now()-interval '35 days' AND sntp_valid IS NOT NULL GROUP BY 1 ORDER BY 1;
-- 2026-09-28  4303 0 0   1067 457 0.2
-- 2026-10-05  9691 0 0   1009 443 0.2
-- 2026-10-12  9679 0 0   1116 450 0.2
-- 2026-10-19  9596 0 0 486726 500 0.1
-- 2026-10-26  9486 2 0 486726 434 0.2
-- 2026-11-02  5303 0 0 486726 460 0.1
```

The `486726` value is a data artifact, not a five-day sync gap. It is one constant repeated on 1,308 rows between 2026-10-22 and 11-02 under firmware `2026.10.2.0637.620a218a-wifi-bound`, and a real age would grow over time. Excluding it:

```sql
SELECT count(*) n, count(*) FILTER (WHERE last_sntp_sync_age_s=486726) frozen_rows, sum(1-sntp_valid) not_valid,
       max(sntp_miss_count) max_miss,
       max(last_sntp_sync_age_s) FILTER (WHERE last_sntp_sync_age_s<>486726) max_age_real,
       percentile_cont(0.99) within group (order by last_sntp_sync_age_s) FILTER (WHERE last_sntp_sync_age_s<>486726) p99_age
FROM diagnostics WHERE ts > now()-interval '30 days' AND sntp_valid IS NOT NULL;
-- 41144 | 1308 | 2 | 0 | 1116 | 890
SELECT ts, sntp_valid, last_sntp_sync_age_s, firmware_version FROM diagnostics
WHERE ts > now()-interval '30 days' AND sntp_valid=0;
-- 2026-10-29 22:20Z and 22:21Z only (a reboot)
```

Reading: the controller re-syncs about every 15 minutes (p99 890 s, max 1,116 s), with no heartbeat misses and an offset within 0.1-0.2 s. Its first server, 192.168.10.1, returns ICMP port-unreachable (network-infra `ntp/lan-ntp/README.md`, "Why not the UDM"). Every sync in this window therefore came from **server 2, pool.ntp.org**. That proves the ESP-IDF lwIP client fails over to a second server on this firmware, which the new two-server design relies on.

### Alerts and Slack

```sql
SELECT severity, count(*) alerts_30d, count(*) FILTER (WHERE slack_ts IS NOT NULL) slack_posted,
       count(DISTINCT alert_type) types, max(ts) FILTER (WHERE slack_ts IS NOT NULL) last_post
FROM alert_log WHERE ts > now()-interval '30 days' GROUP BY 1 ORDER BY 2 DESC;
-- warning  1372 368 13 2026-11-05 20:34Z
-- critical   38  38  6 2026-11-04 21:09Z
-- info        1   0  1
```

The types posted to Slack in the last 30 days (alert type, severity, count, posted):

```
vpd_stress warning 210/210, heap_pressure_warning warning 86/86, house_band_drift warning 60/60,
soil_dryout critical 14/14, vpd_extreme warning 9/9, planner_required_plan_missed critical 9/9,
leak_detected critical 8/8, planner_gateway_delivery_failed critical 4/4, heap_pressure_critical critical 2/2,
planner_plan_horizon_missing, heat_staging_inversion, planner_stale (critical), firmware_version_mismatch: 1 each
```

```sql
SELECT source, event_type, status, count(*), min(ts)::date, max(ts) FROM slack_notification_events GROUP BY 1,2,3;
-- ingestor operator_brief posted 85  2026-05-26 .. 2026-11-05 14:30Z  (+3 one-off validation/smoke rows)
SELECT count(*) total, count(*) FILTER (WHERE ts > now()-interval '3 days') last3d, max(ts) FROM slack_command_audit;
-- 2974 | 0 | 2026-11-02 13:39Z     (the command surface has had no traffic for 3 days)
SELECT left(action_taken,40), count(*) FILTER (WHERE triggered_at > now()-'30 days'), count(*), max(triggered_at)
FROM forecast_action_log GROUP BY 1 ORDER BY 3 DESC LIMIT 10;
-- evaluated_ok 20598/134549; alert_posted 31 (30 d) / 201 total, last 2026-11-04 21:43Z; logged 3/18; ...
```

`alert_posted` is `scripts/forecast-action-engine.py:520-523` calling `post_slack(msg)` for rules whose action is `alert`. Nothing writes those to `alert_log`, so they appear only in Slack.

### Live cluster

- Non-local images. Read with `kubectl get pods` (imageID) and with `kubectl get deploy,sts,ds,cronjob` (templates, init containers included). The result matches `kustomize build` of `deploy/k8s/overlays/prod`, `deploy/k8s/descheduler` and `deploy/k8s/dashboard` at cf21fa421: the 12 refs in the image table below. `verdify-dashboard` is fully local.
- `imagePullSecrets` per workload: see the bottom line. Reflector source `agent-fleet-runners/zot-origin-cluster-pull`, read from metadata only: its `reflection-allowed-namespaces` lists `verdify-prod` and `verdify-dashboard` but not `verdify-descheduler`.
- The python digest checked against the cache (`GET /v2/docker.io/library/python/manifests/sha256:236173eb…`): the index's linux/amd64 child is `sha256:2d0774766164…`, which is exactly the digest our manifest pins.
- Cache presence (`/v2/<repo>/tags/list`): esphome **404**, cosign **404**; hermes-agent, grafana and eclipse-mosquitto **200**.
- Egress policies in `verdify-prod`:
  - `egress-deny-management` selects every component except the 12 that have their own policy. Its only rule is `0.0.0.0/0` minus 25 management /32s and /24s, so those pods have the open internet.
  - Policies with public `0.0.0.0/0:443`: `verdify-outbound-boundary`, `allow-firmware-builder-egress` and `experiment-v2-selector`.
  - The live `verdify-outbound-config` holds `proxy_ssl_name` for `api.openai.com`, `slack.com` and `api.open-meteo.com` (listens 8081/8082/8083).
- The Grafana env sets `GF_ANALYTICS_REPORTING_ENABLED=false`, `GF_ANALYTICS_CHECK_FOR_UPDATES=false`, `GF_PLUGINS_PREINSTALL_DISABLED=true` and `GF_SNAPSHOTS_EXTERNAL_ENABLED=false`. Gravatar, the news feed and the plugin-update check are left at their defaults (on).
- The lab-publisher log carries `npm notice New major version of npm available! 10.9.9 -> 12.2.2`. The npm CLI fetched that from registry.npmjs.org at runtime.
- Argo: `verdify-descheduler` (source this repo, `deploy/k8s/descheduler`) is Synced/Healthy. Yet the live CronJob has no `maintenance-gate` init container, while Git has one (`cronjob.yaml:72-80`). The Application is pinned to an older revision by agents, so Git-to-live drift continues after #802 closed. `verdify-prod-dark` is Synced at cf21fa421.
- The estate NTP prober (`lan-ntp-prober`) was not readable from the Mac: the API-server service proxy returns 502 through NetworkPolicy, and its logs are empty. The VM305 serving state comes from the network-infra README (fixed and verified today in #954), not from my own probe.

## Public research

- ESPHome SNTP component (https://esphome.io/components/time/sntp/): `servers` takes up to 3 entries and defaults to `0/1/2.pool.ntp.org`. A device with a manual IP has to be given DNS servers or IP-literal NTP servers, which supports using IP literals here and removing the controller's only DNS-dependent setting. The page does not mention DHCP-provided NTP, which is consistent with #1364: option 42 cannot reach a static-IP device.
- RFC 8633, NTP best current practice (https://www.rfc-editor.org/rfc/rfc8633.html), §3.2: use at least four independent sources where accuracy matters; with two, a disagreement cannot be resolved. §3.6: run local servers and point local clients at them. An SNTP leaf with two local servers is acceptable because VM305 does the multi-source selection upstream (dish plus two NIST). Two servers on the device are for availability, not for voting.
- k3s private registry docs (https://docs.k3s.io/installation/private-registry): containerd always tries the default upstream endpoint last, even with mirrors configured. Only `--disable-default-registry-endpoint` removes that fallback, and only for registries listed in `registries.yaml`. Rewrites are not applied to the default endpoint. This is why the transparent mirror alone does not get Verdify off Docker Hub.
- zot mirroring (https://zotregistry.dev/v2.1.23/articles/mirroring/): `onDemand` sync fetches an image on first request and caches it locally, and `content.prefix`/`destination` map upstream paths under a local prefix (here `/docker.io`, `/ghcr.io`, …). Docker Hub should be mirrored on demand only, not polled. The page does not say how retention treats on-demand content. The estate config does say: `keepUntagged pulledWithin 720h`, `deleteUntagged true` (`jvallery/storage-infra` `deploy/registry-cache/configmap.yaml`). Cached digests are therefore not durable custody.

## Inventory: every outbound dependency

The "Decision" column records Jason's 2026-11-05 decisions on #1351.

| # | Host | Purpose | Component (file) | Path | Decision | Local replacement |
|---|---|---|---|---|---|---|
| 1 | api.openai.com (chat) | Iris planner | `verdify-hermes-iris` (`deploy/k8s/components/hermes-iris/hermes-config.yaml:416`), direct, no relay | runtime | Remove | Cortex `llm.primary.longctx` single deterministic call (#1350); delete the Hermes component |
| 2 | api.openai.com (embeddings) | MCP `lessons_search`/`knowledge_search`, `scripts/embed-corpora.py` | `verdify-mcp` via `verdify-outbound:8081` | runtime | Remove | Cortex `embed.main` (Qwen3-Embedding-4B, 2560-d): a vector migration and a re-embed. **No issue filed yet** |
| 3 | api.openai.com (idle) | none (0 runs) | `verdify-planner` (`planner_graph/config.py:58`) | runtime | Remove | Delete the Deployment with #1350 |
| 4 | api.openai.com (selector) | experiment-v2 AI-admission arm, inert | `experiment-v2-selector` (`deploy/k8s/components/experiment-v2-orchestrator/configmap.yaml:15`), egress `0.0.0.0/0:443` | runtime | Remove | Cortex via #1350/#1360, or delete the arm; then remove its 443 policy |
| 5 | slack.com | alerts, briefs, pages, command surface | ingestor, MCP, Hermes, scripts (see the Slack section) | runtime | Remove | dashboard + Alertmanager → `ha-local-push` (#1352) |
| 6 | api.open-meteo.com | 72 h forecast | ingestor `forecast_sync` via `verdify-outbound:8083` (`ingestor/tasks/_common.py:1074`) | runtime | **Accepted** | Keep, with the stale-forecast fallback |
| 7 | Cloudflare DNS, tunnel, Let's Encrypt DNS-01, Turnstile | remote access, TLS, contact form | network-infra/agents edge; `api/main.py` Turnstile | runtime (remote only) | **Accepted for now** | LAN clients already bypass the tunnel through Pi-hole split DNS (192.168.7.10) |
| 8 | fonts.googleapis.com/gstatic, cdn.jsdelivr.net, cdnjs.cloudflare.com, googletagmanager.com | lab.verdify.ai visitor assets and GA | `site/quartz.config.ts:16`, `latex.ts`, `mermaid.inline.ts`, `Head.tsx:147` | visitors' browsers | **Accepted for now** | none now |
| 9 | docker.io, ghcr.io, registry.k8s.io (pull) | runtime image pulls | 12 refs below | runtime | Remove | zot origin custody copies |
| 10 | pool.ntp.org | controller clock (the only working source today) | `firmware/greenhouse.yaml:314-318` | runtime, device | Remove | 192.168.7.54, then 192.168.100.1 (#1364) |
| 11 | registry.npmjs.org | npm update-notifier check | `verdify-lab-publisher` CronJob | runtime, incidental | Not decided; falls under "everything else local" | `NPM_CONFIG_UPDATE_NOTIFIER=false` in the publisher image or CronJob env |
| 12 | grafana.com, secure.gravatar.com | Grafana news feed, avatar proxy, plugin-update check | `verdify-grafana` (`deploy/k8s/components/grafana/grafana.yaml`) | runtime, incidental | Same as 11 | `GF_NEWS_NEWS_FEED_ENABLED=false`, `GF_SECURITY_DISABLE_GRAVATAR=true`, `GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false` |
| 13 | github.com, PlatformIO registry and toolchain downloads, ghcr (esphome/cosign) | firmware compile: git clone (`firmware-builder.yaml:180`), xtensa toolchain into a per-Job cache (`:197`) | `verdify-firmware-builder` (`allow-firmware-builder-egress` 443) | build-time, gates OTA | Not decided | See decision D3 |
| 14 | docker.io, ghcr.io, PyPI, npm, packagecloud.io, apt.postgresql.org, github.com (pgvector) | Kaniko image builds (`FROM`, `pip`, `npm ci`, `deploy/k8s/cnpg/image/Dockerfile:66,88`, `ci/validate.Dockerfile:62,79`) | agents in-cluster CI | build-time | Not decided | Base pulls through the cache (agents#5374); package indexes accepted as build-time (D3) |
| 15 | github.com | source of truth, Argo source, CI status | estate | delivery | Estate-wide | none (estate decision) |
| 16 | cloudflare-dns.com | DoH check in `scripts/dashboard-smoke.sh:106` | test only | test | n/a | allowlist as test-only |

Already local, no action: Tempest (UDP 50222 to the ESP32), Home Assistant 192.168.30.107, MQTT, Frigate, Garage S3, zot, Cortex vision, Authentik (`auth.vallery.net`), the dashboard's self-hosted fonts, the Grafana renderer.

## Images: exact local reference for each

Custody naming rule (`jvallery/storage-infra` `deploy/registry-origin/custody/README.md`): `registry.vallery.net/jvallery/mirror-<registry>-<repo with / and . turned into ->:<tag>-<first 12 hex>@sha256:<same digest>`. The reference style matches what monitoring-stack already uses, for example `registry.vallery.net/jvallery/mirror-quay-io-prometheus-alertmanager:v0.34.1-84967b9b7ba4@sha256:…`. Custody copies are read with `zot-origin-cluster-pull`, because the origin ACL grants `cluster-pull` read on `jvallery/**`. The cache endpoint is `http://registry-cache.cluster.vallery.net:5000` (nodes) or `http://registry-cache.registry-cache.svc:5000` (in-cluster, the custody Job's `source.base`). It is a custody **source**, never a ref in an app manifest.

| # | Current ref (digest) | Workloads / source line | Local ref | Custody status | Pull secret today |
|---|---|---|---|---|---|
| 1 | `nginxinc/nginx-unprivileged:stable-alpine-slim@sha256:5d8bb9437d74…` | outbound, lab, lab-cache-origin, render-cache (`verdify-outbound.yaml:91`, `lab-site.yaml:159`, `lab-cache-origin.yaml:58`, `grafana-render-cache.yaml:49`) | `registry.vallery.net/jvallery/mirror-docker-io-nginxinc-nginx-unprivileged:stable-alpine-slim-5d8bb9437d74@sha256:5d8bb943…` | **new** (in cache) | lab only; outbound, lab-cache-origin and render-cache have **none** |
| 2 | `docker.io/traefik:v3.7.16@sha256:c8a121e4bb13…` | verdify-traefik (`verdify-traefik-deployment.yaml:88`) | `registry.vallery.net/jvallery/mirror-docker-io-library-traefik:v3.7.16-c8a121e4bb13@sha256:c8a121e4…` | **exists** (`tier0-traefik-apps`) | **none** |
| 3 | `eclipse-mosquitto:2@sha256:12436df85d6b…` | verdify-mqtt (`mqtt-broker.yaml:68`) | `…/mirror-docker-io-library-eclipse-mosquitto:2-12436df85d6b@sha256:…` | new (in cache) | **none** |
| 4 | `postgres:16@sha256:485935f94cc7…` | ingestor init `install-psql` (`ingestor-gather-mount.patch.yaml:60`) | Preferred: drop it and copy `psql` from the zot-hosted `verdify-timescaledb` image the ingestor already uses. Otherwise `…/mirror-docker-io-library-postgres:16-485935f94cc7@sha256:…` | new, or not needed | yes |
| 5 | `python:3.12-alpine@sha256:2d0774766164…` (linux/amd64 child) | db-backup-exporter (`backup-freshness-exporter.yaml:59`) | `registry.vallery.net/jvallery/mirror-docker-io-library-python:3.12-alpine-236173eb7400@sha256:2d0774766164…`. The by-digest pull of the child works inside the custody index; keep the child pin | **exists** (index `236173eb…` holds this child) | **none** |
| 6 | `grafana/grafana:12.4.14@sha256:ae509d9ae8c1…` | verdify-grafana init + main (`grafana.yaml:78,108`) | `…/mirror-docker-io-grafana-grafana:12.4.14-ae509d9ae8c1@sha256:…` (the custody repo exists, but holds 13.2.4 only) | new tag | **none** |
| 7 | `grafana/grafana-image-renderer:v5.12.9@sha256:24df1663f600…` | grafana renderer (`grafana.yaml:307`) | `…/mirror-docker-io-grafana-grafana-image-renderer:v5.12.9-24df1663f600@sha256:…` | new (in cache) | **none** |
| 8 | `nousresearch/hermes-agent@sha256:a7111ab1cc43…` | hermes-iris (`hermes-iris.yaml:116,141`) | Delete with #1350. If it outlives the PR: `…/mirror-docker-io-nousresearch-hermes-agent:pinned-a7111ab1cc43@sha256:…` | delete (in cache) | yes |
| 9 | `ghcr.io/esphome/esphome:2026.6.5@sha256:ffe23402d169…` | firmware builder/flasher/verify (`firmware-builder.yaml:126`, `firmware-flasher.yaml:75,151`; `tests/test_firmware_builder_image.py:14`) | `…/mirror-ghcr-io-esphome-esphome:2026.6.5-ffe23402d169@sha256:…` | new; **not in cache (404)**: warm it or copy straight from ghcr | **none** |
| 10 | `ghcr.io/sigstore/cosign/cosign@sha256:0f06154836d2…` | builder signer, flasher verify (`firmware-builder.yaml:240`, `firmware-flasher.yaml:117`) | `…/mirror-ghcr-io-sigstore-cosign-cosign:pinned-0f06154836d2@sha256:…` | new; **not in cache (404)** | **none** |
| 11 | `registry.k8s.io/descheduler/descheduler:v0.36.0@sha256:7ca92c0a7b4f…` | descheduler CronJob (`deploy/k8s/descheduler/cronjob.yaml:99`) | `…/mirror-registry-k8s-io-descheduler-descheduler:v0.36.0-7ca92c0a7b4f@sha256:…` | new (in cache) | **namespace has no zot Secret** |
| 12 | `python:3.13.5-alpine3.22@sha256:37b14db89f58…` | descheduler `maintenance-gate` init (`cronjob.yaml:74`; Git only, not live) | `…/mirror-docker-io-library-python:3.13.5-alpine3.22-37b14db89f58@sha256:…` (the estate descheduler in agents pins the same digest, so one copy serves both) | new | same as 11 |

Unrendered and dead, delete rather than copy: `deploy/k8s/components/umami/` (`postgres:16-alpine` and `umami:postgresql-latest`, both floating), `deploy/k8s/components/twin-builder/` (`alpine/git`, kaniko), `deploy/k8s/diagnostics/device-vlan-spike.yaml`.

Build-time `FROM` lines without a digest: `planner_graph/Dockerfile:16,32` (dies with #1350), `twin/Dockerfile:16,36`, `scripts/Dockerfile.lab-publisher:8` and `scripts/Dockerfile.setpoint-server:38`. Pin them by digest now. Base pulls go through the cache once agents#5374 lands (Kaniko does not read `registries.yaml`).

## NTP

- **Estate servers** (`jvallery/network-infra` `ntp/lan-ntp/vm305/chrony.conf`):
  - VM305 (`vm-network-services`, running on oxide) serves on **192.168.7.54** (`bindaddress`, `allow 192.168.0.0/16`).
  - Upstreams: the dish `192.168.100.1` (prefer, polling 16-64 s) and NIST `128.138.140.44` and `129.6.15.28`. `local stratum 10 orphan` keeps it serving with both WANs down.
- **IoT reachability:**
  - Step P (2026-11-03 17:57Z) added 192.168.7.54 to "SEG IoT -> NTP gateway". The L3 matrix G2 MUST-KEEP allows any → 192.168.100.1 and 192.168.7.54 on udp/123.
  - VLAN 10 has no DNAT (N-10 is unapplied; the broker refuses NAT creates on network-infra#864). A direct query from 192.168.10.111 therefore reaches VM305 itself.
  - VM305 rule 754 sends NTP replies out of `eth1` through 192.168.7.1, so the reply comes back through the UDM.
- **Today's fix:** network-infra#953/#954 (merged 12:48 local). An unattended upgrade restarted systemd-networkd at 13:37Z and deleted rule 754 and its routes. Every Servers-30 client lost NTP until 19:38Z. The new networkd drop-in (`ManageForeignRoutingPolicyRules=no`) prevents a repeat, and `install.sh verify` fails without it.
  - The reply path for an IoT client depends on that same rule. This outage is why the dish should stay as a second server on the device.
- **Firmware:** `firmware/greenhouse.yaml:313-319` and `:152-154`. Neither file has any other hostname or external IP: grep of `firmware/*.yaml`, `firmware/greenhouse/` and `firmware/lib/` finds only `192.168.10.x`. Once `pool.ntp.org` is removed, the controller needs no DNS for anything. `dns1: 192.168.10.1` can stay, and nothing will use it.
- **Target block:**

  ```yaml
  time:
    - platform: sntp
      id: sntp_time
      servers:
        - 192.168.7.54    # VM305 chrony (network-infra ntp/lan-ntp), orphan stratum 10 with the WAN down
        - 192.168.100.1   # Starlink dish, Jason's option-42 secondary (2026-11-01)
  ```

  Keep `CONFIG_LWIP_SNTP_MAX_SERVERS: "2"` and change its comment to say that the second slot is the dish fallback. In `tests/test_12_fidelity.py:1016`, assert exactly these two IPs and reject any hostname in the time block.
- **Unproven:** whether the dish answers NTP with Starlink itself down. It probably takes time from GPS, but that is not verified. VM305 orphan mode is the documented T1 path, and network-infra#869 (GPS stratum 1) is the long-term fix.

## Slack: every sender and its replacement

| # | Sender | File | Volume | Replacement |
|---|---|---|---|---|
| S1 | Alert transport (posts, threads resolutions on `alert_log.slack_ts`) | `ingestor/tasks/alerts.py:3119-3350`, `_common.py:711` `_post_slack` | 406 posts/30 d (all critical, 368 warning) | `alert_log` → dashboard (exists); `verdify_alert_open` gauge → `VerdifyGreenhouseAlertOpen` (critical, `local_push="true"`) → `ha-local-push` |
| S2 | Operator briefs | `ingestor/tasks/watch.py:91-132`, `slack_ops/briefs.py` | 85 total, about 3/day | Retire. The Now-hero snapshot (#1346) replaces it |
| S3 | "Midnight watch" plan check | `watch.py:60-79` | — | A `planner_*` alert_log type (already covered by `planner_required_plan_missed`/`planner_stale`); delete |
| S4 | "PLAN DELIVERY FAILED" | `heartbeat.py:1557-1565` | Slack only | Dedupe into the existing `planner_gateway_delivery_failed` alert_log type (critical, 4 in 30 d) |
| S5 | "MCP server unreachable" | `heartbeat.py:1598-1603` | Slack only | New `mcp_unreachable` alert_log type (critical, auto-resolve) plus a `verdify_schemas` model |
| S6 | **Forecast-action "Forecast Alert"** | `scripts/forecast-action-engine.py:113-125, 520-523` (run by `ingestor/tasks/forecast.py:39,158`) | 31/30 d, 201 total; Slack and `forecast_action_log` only | Write `alert_log` type `forecast_action_alert` (warning: dashboard only) |
| S7 | Alert-transport qualification | `ingestor/tasks/component_alert_qualification.py:90-107` | test harness | Delete |
| S8 | MCP `slack_ops` command surface (inbound) | `mcp/server.py:1541`, `slack_ops/` | 2,974 audit rows, about 90% failed, no traffic since 11-02 13:39Z | Delete; the MCP read tools and dashboard cover it |
| S9 | Iris prompts' "post to Slack" step | `ingestor/iris_planner.py`, `iris-inventory.yaml:309,347`, `scripts/gather-plan-context.sh` | per plan | Gone with #1350 |
| S10 | Legacy scripts | `scripts/alert-monitor.py:155`, `scripts/checklist-to-slack.sh:51` (direct `slack.com`), `slack-ops.py`, `slack-channel-archive.py` | not scheduled | Delete |
| S11 | Estate Alertmanager → Slack `#alerts` | monitoring-stack `alertmanager.yaml` `default`/`orbit-actionable` | every `area: verdify` Prometheus alert (21 rules) falls to these | Add a terminal route `area="verdify"` → `"null"`, after the `local_push` legs and the top-level `kepler-estate` catch-all (continue: true); the HA push and kepler copies still happen |

Alertmanager finding: no new receiver is needed. The `local_push="true"` matcher already routes to `ha-local-push` (`alertmanager.yaml:169-176`, continue: true), and `VerdifyControllerDark` already carries `local_push: "true"`, `area: verdify`. The monitoring-stack change is one rule plus one terminal route.

Gauge host: the operator API (`verdify-operator-api`) already serves the dashboard's open-alert query (`verdify_services/alerts.py`). Exporting `/metrics` from the same query keeps "what the dashboard shows" and "what pages" on one definition. It also needs no new role or Deployment, and Prometheus discovers it with the `prometheus.io/scrape` annotation, the same pattern as the backup exporter. Alternative: a micro-exporter like `verdify-db-backup-exporter`, which needs a new read-only DB role.

## Options considered

- **Images.**
  - (A) Rely on the transparent mirror. Rejected: upstream fallback and 30-day GC.
  - (B) Custody copies plus explicit refs. Recommended.
  - (C) Cache-host refs. Rejected: plain HTTP, GC, topology in app manifests.
  - (D) Turn on `--disable-default-registry-endpoint` estate-wide. Out of Verdify's scope and still subject to cache GC; worth suggesting to proxmox-infrastructure as a follow-up, not as our fix.
- **NTP.**
  - VM305 only: one point of failure; today's 6 h routing outage shows the risk.
  - VM305 plus the dish: recommended.
  - VM305, dish and NIST: breaks "forced local".
  - Wait for N-10 DNAT and leave `pool.ntp.org` in place: works only when the broker blocker clears, and still names a public host.
- **Paging.**
  - Critical only: recommended (about 1.3 a day).
  - Critical plus `vpd_extreme`: +9 in 30 days.
  - Everything posted to Slack today: about 13 a day, too noisy for phone push.
- **Egress enforcement.**
  - CI host guard only: catches code, not runtime behaviour.
  - CI guard plus a namespace internet-deny NetworkPolicy: recommended.
  - FQDN egress policy: no FQDN-capable CNI policy today (the existing policies say so).

## Recommended design

1. **Egress boundary** (`deploy/k8s/overlays/prod` and `deploy/k8s/dashboard`).
   - NetworkPolicy is allow-only, so a second "deny" policy cannot remove egress that another policy allows.
   - `egress-deny-management` is vendored byte-identical from `jvallery/network-infra`, and by design it keeps public egress (`tests/test_egress_deny_management.py:95`). Do not edit it.
   - Add one Verdify-owned policy, `egress-local-only`, that selects the 14 open components. It allows egress only to `10.0.0.0/8`, `172.16.0.0/12` and `192.168.0.0/16`, with the same management `except` list. Add those components to the overlay's `NotIn` patch, so the vendored deny no longer selects them (the existing test `test_deny_never_selects_a_pod_with_its_own_egress_allowlist` already enforces that pairing).
   - Add a test that the `except` list of `egress-local-only` equals the vendored one, so a network-infra re-vendor cannot silently drift.
   - Do the same in `deploy/k8s/dashboard` for `verdify-dashboard-web`.
   - The policies that allow `0.0.0.0/0:443` (`outbound`, `firmware-builder`, `experiment-v2-selector`) are unaffected.
  - Remove the 443 rule of `experiment-v2-selector` with #1350.
  - Remove the openai and slack server blocks from `verdify-outbound`.
2. **CI guard** (`scripts/check-external-hosts.py` and `scripts/check-image-sources.py`, both in `scripts/ci-local.sh`).
   - Allowlist file `deploy/external-dependencies.yaml`: one row per accepted host with its decision reference (Open-Meteo, Cloudflare, the lab CDNs and GA, build-time hosts).
   - The image guard applies the regex from #1363 to the three renders plus all YAML under `deploy/`.
3. **Images:** custody plus repin plus pull secrets, as in the table above.
4. **NTP:** the two-server firmware block above.
5. **Slack:** S1-S11 above. DB: rename `slack_alert_runbooks` to `alert_runbooks`, archive the other `slack_*` tables, and drop them 30 days later (#1352 step 7, unchanged).

## Implementation plan (PR-sized, one per owner repo where possible)

1. **storage-infra (one PR): custody entries.** Add 9 `plan.json` entries (rows 1, 3, 4 if kept, 6, 7, 9, 10, 11, 12; row 8 only if #1350 slips). Use the `registry` source for the cached ones. For esphome and cosign, either warm the cache with one pull or add ghcr as the source. Run the Job, require `"verified": true` and update `docs/ownership/container-images.md`. Maps to #1363 (filed there as a cross-repo child).
2. **agents (one PR):**
   - Add `verdify-descheduler` to the `zot-origin-cluster-pull` reflection regex.
   - Bump the `verdify-descheduler` Application pin to bring in `maintenance-gate`.
   - Delete the KSOPS `secret-verdify-slack` and update its estate-record rows after PR 4 is live (#1352).
   - Optional: the agents#5374 Kaniko mirror.
3. **verdify-platform PR A: images and egress (#1363, #1351).**
   - Repin rows 1-12.
   - Add `zot-origin-cluster-pull` to the 9 workloads without it (outbound, traefik, mqtt, grafana, render-cache, lab-cache-origin, db-backup-exporter, firmware-builder, firmware-flasher).
   - Drop `ghcr-jvallery-readonly`, delete the umami, twin-builder and diagnostics manifests, and pin the four bare `FROM`s.
   - Add the Verdify-owned `egress-local-only` policy and its `NotIn` pairing; add the Grafana env hardening and turn the npm notifier off.
   - Add both CI guards. Update `tests/test_firmware_builder_image.py`.
   - Deliver with one attended full sync of `verdify-prod-dark`. Ride the ingestor init change (row 4) on a promotion sync, because it Recreates the ingestor.
   - Verify: imageIDs are all `registry.vallery.net`; one firmware-builder run on the custody images; `k3s-smoke.sh smoke` + `device-monitor`.
4. **verdify-platform PR B: Slack removal (#1352).**
   - S3-S7 become `alert_log` types (with `verdify_schemas` models).
   - Add the operator-api `/metrics` gauge.
   - Remove S1, S2, S8 and S10, and the Slack env, volume and outbound block.
   - Migration: runbook table rename plus archive.
   - `Post-merge restart: verdify-ingestor, verdify-mcp, verdify-operator-api, verdify-outbound`.
   - S9 rides #1350. Splitting B in two (B1: new alert types and the gauge, live first; B2: removal) is the safe order.
5. **monitoring-stack (one PR, after B1 is live):** `VerdifyGreenhouseAlertOpen` (critical, `local_push: "true"`, `area: verdify`) and the terminal `area="verdify"` → `"null"` route. Verify with a synthetic critical: the HA push arrives and `#alerts` stays silent.
6. **verdify-platform PR C: firmware NTP (#1364).**
   - The servers block and the fidelity test. Run `make firmware-check` and `make test-firmware` in cluster CI.
   - Flash in an attended session with `VERDIFY_DB_BACKEND=kube`.
   - Verify for 24 h: `sntp_valid=1`, age ≤ 960 s, offset ±2 s, and a `chronyc clients` readback of 192.168.10.111 from network-infra.
   - Before flashing, ask network-infra to confirm a VLAN 10 → 192.168.7.54 answer (prober vantage, or `chronyc clients` after a test).
7. **network-infra (existing #864):** N-10 DNAT as defence in depth. No new PR from this track.
8. **New issue to file:** MCP embeddings on Cortex `embed.main` (2560-d vector migration and re-embed; #1351 row 2). It currently has no issue.

## Decisions Jason must make

- **D1 Paging set.** Default: page (HA push) on `severity=critical` only (6 types, 38 in 30 days). All warnings stay on the dashboard. `soil_dryout` (14 criticals in 30 days) may be meaningless while the hydro rails are empty; consider suppressing it until replanting.
- **D2 Operator briefs.** Default: stop them when PR B2 lands, without waiting for #1346. The dashboard Now screen already shows the same state.
- **D3 Build-time internet** (firmware compile from GitHub/PlatformIO; Kaniko from PyPI, npm and packagecloud). Default: accept as build-time exceptions in the allowlist, because runtime stays local and a WAN-down build simply waits. Alternative: bake the PlatformIO toolchain into a zot-hosted builder image, so OTA builds work offline.
- **D4 Incidental runtime calls** (Grafana news, gravatar and plugin checks; the npm notifier). Default: turn them off in PR A; nothing depends on them.
- **D5 Dish as the controller's second NTP server.** Default: yes, matching your 2026-11-01 option-42 order.

## Open risks

- The repins restart mqtt and traefik, which are on the telemetry and serving path. Do them in the attended sync with `device-monitor`. A missing pull secret shows up as ImagePullBackOff on the new ReplicaSet while the old pods keep serving.
- The esphome and cosign custody copies need a source; the cache has never seen them.
- `chronyc clients` and the VLAN 10 reply path are unverified from an IoT vantage. The rule-754 class of failure that hit Servers 30 today would also hit IoT, and the dish is the mitigation.
- The `last_sntp_sync_age_s = 486726` frozen value on 1,308 rows (2026-10-22 to 11-02) shows that `diagnostics` can carry a stale device value forward. Any 24 h acceptance query must exclude repeated constants.
- `verdify-descheduler` Git-to-live drift remains after #802 closed (`maintenance-gate` absent live), because the agents Application pin is behind.
- `egress-local-only` could break an undocumented public call. Do a pilot first: diff the policy against the pods' known destinations, then apply with a 10-minute soak (L10) and watch the logs for connection errors.
