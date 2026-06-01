# Verdify System Architecture

**Last Updated:** 2026-05-23
**Host:** vm-docker-iris (192.168.30.150)
**Platform:** Debian 13, Docker 28, Python 3.13

## Overview

Verdify is a greenhouse automation platform running primarily on a single VM. An ESP32 controller manages 367 sq ft of greenhouse climate (fans, heaters, misters, fog) using a deterministic controller (`greenhouse_logic.h`). A Python ingestor captures 172 sensor entities into TimescaleDB, and an AI agent named Iris runs through Hermes with OpenAI GPT-5.5 high-reasoning as the sole planner gateway. Iris manages tunables event-driven at solar milestones and deviations through typed MCP tools, but the ESP32 owns real-time relay control.

```
ESP32 (192.168.10.111, IoT VLAN)
  ├─ aioesphomeapi (encrypted, port 6053) ──→ Ingestor ──→ TimescaleDB
  ├─ MQTT (mqtt.verdify.ai:1883) ──→ Mosquitto (state publishing, occupancy)

TimescaleDB (44 tables, 54 views, 23 functions, 2.54M+ rows)
  ├─→ Grafana (graphs.verdify.ai, 54 dashboards)
  ├─→ API (api.verdify.ai, crop endpoints + compatibility /setpoints)
  └─→ lab.verdify.ai (Quartz static lab site with embedded Grafana panels)

Iris Planner (Hermes + GPT-5.5, MCP-only tool surface)
  └─→ Event-driven (sunrise/transitions/sunset/forecast/deviation)
      → MCP tools (climate, scorecard, set_tunable) → setpoint_changes table
```

## Network Architecture

### Public Routing (Internet → VM)

```
Internet → Cloudflare (DNS, DDoS protection)
  → nexus Traefik (192.168.30.100, TLS termination, LE wildcard cert *.verdify.ai)
    ├─ auth.verdify.ai (priority 110) → Authentik SSO (nexus)
    ├─ verdify.ai / www (priority 100) → iris:443 (redirects to lab.verdify.ai)
    └─ *.verdify.ai (priority 50) → iris:443 (insecureSkipVerify)
      → iris Traefik (self-signed cert, Docker label routing)
        ├─ lab.verdify.ai → verdify-site:80 (Quartz)
        ├─ verdify.ai / www → permanent redirect to lab.verdify.ai
        ├─ graphs.verdify.ai → grafana-proxy:80 → grafana:3000
        ├─ api.verdify.ai → verdify-api:8080 (FastAPI)
        └─ traefik.verdify.ai → dashboard (BasicAuth)
```

### MQTT (ESP32 → VM, port 1883)

```
ESP32 (192.168.10.111, IoT VLAN)
  → mqtt.verdify.ai (local DNS → 192.168.30.150)
  → Mosquitto Docker container (port 1883)
  → User: vallery, password auth
```

MQTT bypasses Traefik (TCP, not HTTP). DNS resolution handled by local DNS override on the IoT VLAN gateway (192.168.10.1).

### ESP32 Data Path (primary)

```
ESP32 ──aioesphomeapi (encrypted, noise PSK)──→ Ingestor (systemd)
  → on_state_change() callback routes entities by type:
    SensorState   → CLIMATE_MAP (49 cols) → climate table (60s batch)
    BinarySensor  → EQUIPMENT_BINARY_MAP (15) → equipment_state (immediate)
    Switch        → EQUIPMENT_SWITCH_MAP (19) → equipment_state (immediate)
    TextSensor    → STATE_MAP (5) → system_state (immediate)
    NumberInfo    → SETPOINT_MAP (72) → setpoint_changes (immediate)
    SensorInfo    → DIAGNOSTIC_MAP (5) → diagnostics (60s batch)
    SensorInfo    → DAILY_ACCUM_MAP (19) → daily_summary (midnight)
    SensorInfo    → CFG_READBACK_MAP (34) → setpoint_snapshot (60s batch)
```

### Setpoint Push Path (Dispatcher → ESP32)

```
fn_band_setpoints(now()) ──→ Setpoint Dispatcher (300s)
  ├─ Compute band: temp_low/high, vpd_low/high from crop profiles
  ├─ Compute zone targets: vpd_target_south/west/east/center
  ├─ Derive mister tuning: engage_kpa = vpd_high, all_kpa = vpd_high + 0.3
  ├─ Mirror main-light runtime into activity_start_* and activity_duration_min
  ├─ Overlay generic per-zone direct-wet defaults and irrigation/fert day masks
  ├─ Apply planner overrides (clamped within band)
  ├─ Write to setpoint_changes table (source = 'band' or 'plan')
  ├─ Direct push via aioesphomeapi number_command()/switch_command()
  └─ Confirm landing through cfg_* readbacks in setpoint_snapshot

/setpoints endpoints still exist as key=value compatibility surfaces for
diagnostics/recovery, but current production firmware does not run an HTTP
setpoint poller.
```

## Docker Services (14 running containers)

All managed via `/srv/verdify/docker-compose.yml`:

| Container | Image | Port | Network | Purpose |
|-----------|-------|------|---------|---------|
| verdify-traefik | traefik:v3.6.7 | 0.0.0.0:443 | proxy | Reverse proxy, Docker label routing |
| verdify-timescaledb | timescaledb:latest-pg16 | 127.0.0.1:5432 | internal | Primary database (PG16 + hypertables) |
| verdify-grafana | grafana-oss:latest | 3000 (internal) | proxy + internal | 54 dashboards, anonymous Viewer access |
| verdify-grafana-renderer | grafana-image-renderer:latest | 8081 (internal) | internal | Static PNG renderer for mobile Grafana panels |
| verdify-grafana-proxy | nginx:alpine | 80 (internal) | proxy + internal | CSS injection to hide Grafana branding |
| verdify-api | verdify-api (local build) | 8080 (internal) | proxy + internal | Crop API + compatibility /setpoints |
| verdify-mqtt | eclipse-mosquitto:2 | 0.0.0.0:1883 | internal | MQTT broker for ESP32 + Sentinel |
| verdify-site | nginx:alpine | 80 (internal) | proxy | Quartz static lab site (lab.verdify.ai) |
| verdify-umami-db | postgres:16-alpine | 5432 (internal) | internal | Umami analytics database |
| verdify-umami | umami:postgresql-latest | 3000 (internal) | proxy + internal | Public website analytics |
| verdify-goaccess | allinurl/goaccess:latest | — | default | Traefik access-log HTML generator |
| verdify-goaccess-site | nginx:alpine | 80 (internal) | proxy | `logs.verdify.ai` static GoAccess report |
| verdify-promtail | grafana/promtail:latest | host network | host | Docker/journald/state log shipper to Loki |
| hermes-iris | nousresearch/hermes-agent | 127.0.0.1:8642 | internal | Iris planner gateway; profile-gated but kept running in production |

There is no production shadow planner profile; full plans enter through the
single Hermes Iris gateway and bounded `climate_intent` `set_plan` path.

### Docker Networks

| Network | Subnet | Purpose |
|---------|--------|---------|
| verdify-proxy | 172.28.0.0/16 | Public-facing services (Traefik routes here) |
| verdify-internal | 172.27.0.0/16 | Backend services (DB, MQTT — no external access) |
| verdify_default | Docker-assigned | Default-network holdout for the GoAccess generator container |

### Docker Volumes

| Volume | Purpose |
|--------|---------|
| verdify_tsdb_data | TimescaleDB persistent data |
| verdify_grafana_data | Grafana state (users, preferences, annotations) |
| verdify_mqtt_data | Mosquitto message persistence |
| verdify_promtail_positions | Promtail read cursors |
| verdify_umami_db_data | Umami analytics database |

## Systemd Units

Tracked source copies live in `/srv/verdify/systemd/`; install details are in `systemd/README.md`.

| Unit | Type | Purpose | Enablement |
|------|------|---------|------------|
| verdify-ingestor.service | always-on service | ESP32 data ingestor + 12 periodic tasks | enabled |
| verdify-mcp.service | always-on service | MCP server for Iris planner tools | enabled |
| verdify-api.service | always-on service | Host-managed crop/catalog API path | enabled |
| verdify-setpoint-server.service | always-on service | Compatibility `/setpoints` export + Lutron light helper | enabled |
| verdify-plan-publish.path | path unit | Watches planner output and publishes public plan archive | enabled |
| verdify-forecast-page.timer | timer | Runs generated-site publisher on forecast cadence | enabled |
| verdify-site-poll.timer | timer | Polls vault content every 10 seconds and rebuilds site when needed | enabled |
| verdify-forecast-page.service | oneshot | Generated-site publisher | timer-triggered |
| verdify-site-poll.service | oneshot | Site poller | timer-triggered |
| verdify-site-build.service | oneshot | Quartz rebuild and nginx restart | poller-triggered |
| verdify-plan-publish.service | oneshot | Plan archive generation/publishing | path-triggered |

## Ingestor Architecture

The ingestor (`/srv/verdify/ingestor/ingestor.py`, 945 lines) is the core data engine. It runs 5 concurrent async loops:

| Loop | Purpose |
|------|---------|
| `esp32_loop` | Maintains persistent aioesphomeapi connection, routes entity states to DB buffers |
| `flush_loop` | Writes buffered data to DB every 5s (climate/diagnostics batched at 60s) |
| `task_loop` | Runs 12 periodic background tasks (see below) |
| `mqtt_loop` | Subscribes to Sentinel MQTT for greenhouse occupancy |
| `setpoint_listener` | PostgreSQL LISTEN/NOTIFY for real-time setpoint push to ESP32 |

### Periodic Tasks (12)

| Task | Interval | Purpose | External Deps |
|------|----------|---------|---------------|
| water_flowing_sync | 60s | Derive water_flowing from flow_gpm, detect leaks | — |
| matview_refresh | 300s | Refresh v_relay_stuck + v_climate_merged | — |
| shelly_sync | 300s | Shelly EM50 energy meter → energy table | HA REST API |
| tempest_sync | 300s | Tempest weather station → climate outdoor columns | HA REST API |
| ha_sensor_sync | 300s | Hydro sensors, grow lights, switches, occupancy | HA REST API |
| alert_monitor | 300s | 11 alert rules → alert_log + Slack | Slack API |
| setpoint_dispatcher | 300s | Band-driven setpoints → ESP32 | DB functions |
| forecast_sync | 3600s | Open-Meteo 16-day forecast → weather_forecast | Open-Meteo API |
| forecast_actions | 900s | Auto-respond to forecast deviations | — |
| deviation_check | 900s | Compare conditions to forecast, trigger replan | — |
| daily_summary_live | 1800s | Rolling daily cost/energy accumulator | — |
| grow_light_daily | 86400s | Equipment runtime rollup + utility_cost | — |

## Cron Jobs (host crontab excerpt)

| Schedule (MDT) | Script | Purpose |
|----------------|--------|---------|
| 0 1 * * * | pg_dump (inline) | Daily DB backup → /mnt/iris/backups/ |
| 5 0 * * * | daily-summary-snapshot.py | End-of-day climate/cost finalization |
| 10 0 * * * | vault-daily-writer.py | Daily summary → Obsidian vault markdown |
| 15 0 * * * | vault-crop-writer.py | Per-crop status → Obsidian vault |
| 15 0 * * * | generate-hydro-map.py | 60-position hydroponic layout HTML |
| (event-driven) | iris_planner.py | Iris planner via Hermes `/v1/runs`, audited in `plan_delivery_log` |
| 0 12,16,20,0 * * * | frigate-snapshot.py | Camera snapshots + Gemini vision analysis |
| 0 13 * * * | checklist-to-slack.sh | Daily checklist → #greenhouse Slack |

## API Endpoints (api.verdify.ai)

**Container:** verdify-api, FastAPI on port 8080
**Code:** `/srv/verdify/api/main.py` (491 lines)

| Method | Path | Purpose |
|--------|------|---------|
| GET | /setpoints | Compatibility setpoint export (key=value, band-driven) |
| GET | /health | DB health + latest climate timestamp |
| GET | /api/v1/status | System status (crop count, observation count) |
| GET | /api/v1/crops | List active crops (filter: zone, stage, active) |
| GET | /api/v1/crops/{id} | Crop detail + 7-day health avg |
| POST | /api/v1/crops | Create crop + auto-record planted event |
| PUT | /api/v1/crops/{id} | Update crop, auto-record stage_change |
| DELETE | /api/v1/crops/{id} | Soft-delete (set active=false) |
| GET | /api/v1/crops/{id}/observations | List observations |
| POST | /api/v1/crops/{id}/observations | Create observation |
| GET | /api/v1/crops/{id}/events | List events |
| POST | /api/v1/crops/{id}/events | Create event |
| GET | /api/v1/zones | Zone summary with crop counts |
| GET | /api/v1/zones/{zone} | Zone detail + crops |

### /setpoints Compatibility Logic

The `/setpoints` endpoint is kept aligned with the live dispatcher. It computes values in this order (later overrides earlier):
1. Latest per-parameter from `setpoint_changes` table
2. Planner overrides from `v_active_plan`
3. Band values from `fn_band_setpoints(now())` — authoritative for temp/vpd
4. Zone VPD targets from `fn_zone_vpd_targets(now())`
5. Per-circuit lighting policy and activity/direct-wet defaults
6. Mister tuning derived from band ceiling (engage = vpd_high, all = vpd_high + 0.3)
7. Outdoor conditions from latest `climate` row

## Database Schema

**Engine:** TimescaleDB (PostgreSQL 16 + hypertable compression)
**Schema file:** `/srv/verdify/db/schema.sql`

### Core Tables (6 hypertables)

| Table | Rows | Interval | Purpose |
|-------|------|----------|---------|
| climate | 217K | 60s | Temperature, humidity, VPD, CO2, light, soil, outdoor weather |
| equipment_state | 56K | on-change | Relay states (fan, heat, mister, drip, fog, vent, lights) |
| system_state | 35K | on-change | State machine labels (greenhouse_state, lead_fan, mister_zone) |
| setpoint_changes | 13K | on-change | Parameter changes with source (band, plan, esp32, manual) |
| diagnostics | 90K | 60s | WiFi RSSI, heap, uptime, probe health, reset reason |
| energy | 516K | 300s | Shelly EM50 power draw (watts total, heat, fans, other) |

### Reference Tables

| Table | Rows | Purpose |
|-------|------|---------|
| crops | 7 | Active crop inventory (name, zone, stage, dates) |
| crop_events | 4 | Planting, transplant, harvest, pest events |
| observations | 48 | Human/AI crop health observations |
| image_observations | 25 | Gemini vision analysis results + embeddings |
| daily_summary | 248 | Daily rollup (climate, stress, runtime, cost) |
| weather_forecast | 157K | Open-Meteo 16-day hourly forecast |
| setpoint_plan | 3.1K | AI planner waypoints (parameter, value, timestamp) |
| plan_journal | 38 | Plan metadata (summary, reasoning, score) |
| planner_lessons | 15 | Validated operational lessons |
| crop_target_profiles | 144 | Per-crop diurnal VPD/temp bands by season |
| equipment_assets | 15 | Physical equipment inventory |
| alert_log | 435 | Alert history with resolution tracking |
| forecast_action_rules | 9 | Automated forecast response rules |
| utility_cost | 27 | Monthly utility cost tracking |

### Key Functions

| Function | Purpose |
|----------|---------|
| fn_band_setpoints(ts) | Compute crop-science temp/VPD band for given timestamp |
| fn_zone_vpd_targets(ts) | Per-zone VPD targets from crop profiles |
| fn_target_band_smooth(ts) | Smoothed band with stress thresholds |
| fn_equipment_health() | Equipment health score (0-100) |
| fn_stress_summary() | Human-readable stress description |
| fn_compliance_pct(start, end) | Band compliance percentage |
| fn_operational_health() | System health score |

### Key Views

| View | Purpose |
|------|---------|
| v_climate_latest | Latest climate row |
| v_stress_hours_today | Today's stress accumulation by type |
| v_equipment_runtime_daily | Equipment on-time per day |
| v_relay_stuck (materialized) | Detect stuck relays |
| v_climate_merged (materialized) | Joined climate + equipment + state |
| v_active_plan | Currently active planner waypoints |
| v_sensor_staleness | Time since last reading per sensor |
| v_gdd | Growing degree days per crop |
| v_dif | Day/night temperature differential |

## Credentials

### Credential Files

| File | Location | Purpose |
|------|----------|---------|
| gemini_api_key.txt | /mnt/agents/shared/credentials/ | Google AI Studio API key (planner, vision, embeddings) |
| ha_token.txt | /mnt/agents/shared/credentials/ | Home Assistant long-lived access token |
| iris_slack_bot_token.txt | /etc/verdify/slack/ | Iris Slack app bot token for #greenhouse |
| .env | /srv/verdify/ | POSTGRES_PASSWORD, GRAFANA_ADMIN_PASSWORD |
| .env | /srv/verdify/ingestor/ | ESP32_API_KEY, DB credentials |
| .env | /srv/verdify/api/ | DB credentials |
| secrets.yaml | /srv/greenhouse/esphome/ | WiFi, API key, OTA password, MQTT credentials |
| password_file | /srv/verdify/mqtt/ | Mosquitto user/password (hashed) |

### External Service Connections

| Service | Host | Port | Auth | Used By |
|---------|------|------|------|---------|
| ESP32 | 192.168.10.111 | 6053 | Noise PSK | Ingestor |
| Home Assistant | 192.168.30.107 | 8123 | Bearer token | Ingestor tasks, setpoint-server |
| Sentinel MQTT | 192.168.30.107 | 1883 | user/pass | Ingestor mqtt_loop |
| Slack API | api.slack.com | 443 | Bot token | Alert monitor, checklist |
| Open-Meteo | api.open-meteo.com | 443 | None (public) | Forecast sync |
| Google AI Studio | generativelanguage.googleapis.com | 443 | API key | Planner, vision, embeddings |
| Frigate NVR | 192.168.30.142 | 5000 | None | Snapshot capture |
| Loki | 192.168.30.100 | 3100 | None | ESP32 log shipping |

## File Structure

```
/srv/verdify/                          # Main project directory
  docker-compose.yml                   # 7 containers
  .env                                 # DB + Grafana passwords
  traefik/                             # Reverse proxy config
    traefik.yml                        # Static config (websecure:443)
    dynamic/                           # Host-based routes
      dashboard.yml                    # traefik.verdify.ai
  mqtt/                                # MQTT broker config
    mosquitto.conf                     # Listener, auth, persistence
    password_file                      # Hashed credentials
  provisioning/                        # Grafana provisioning
    datasources/timescaledb.yml        # verdify-tsdb datasource
    dashboards/provider.yml            # Dashboard folder config
    dashboards/json/                   # 54 dashboard JSON files
  grafana-custom/                      # Grafana branding
    nginx-grafana.conf                 # Proxy config with CSS injection
    build/custom/                      # Custom CSS/JS
  api/                                 # Crop catalog API
    main.py                            # FastAPI app (491 lines)
    Dockerfile                         # python:3.13-slim
    .env                               # DB credentials
  ingestor/                            # Data ingestor
    ingestor.py                        # Main entry (945 lines)
    tasks.py                           # 12 periodic tasks (1097 lines)
    entity_map.py                      # ESP32 entity routing (362 lines)
    shared.py                          # Shared ESP32 client ref
    .env                               # ESP32 + DB credentials
  scripts/                             # 40+ operational scripts
    planner-gemini.py                  # Gemini 2.5 Pro planner
    analyze-greenhouse-snapshot.py     # Gemini vision analysis
    generate-observation-embeddings.py # Gemini embeddings
    daily-summary-snapshot.py          # End-of-day rollup
    vault-daily-writer.py              # Obsidian vault export
    vault-crop-writer.py               # Per-crop vault export
    frigate-snapshot.py                # Camera capture
    setpoint-server.py                 # Grow light HTTP control
    forecast-sync.py                   # Open-Meteo forecast
    gather-plan-context.sh             # Planner context collection
    standardize-dashboards.py          # Dashboard style automation
    ...                                # 30+ additional scripts
  verdify-site/                        # Quartz static site
    content/                           # 56 markdown pages
    public/                            # Built HTML (nginx serves this)
  db/                                  # Database
    schema.sql                         # Authoritative schema
    migrations/                        # Applied migrations
    init/                              # Docker init scripts
  docs/                                # Project documentation
  state/                               # Runtime state + logs
  archive/                             # Archived cloud configs

/srv/greenhouse/                       # ESPHome toolchain
  esphome/
    greenhouse.yaml                    # Main firmware config
    greenhouse/                        # Sub-configs (controls, sensors, globals, tunables, hardware)
    secrets.yaml                       # WiFi, API keys, MQTT creds
    .esphome/                          # Build cache
  .venv/                               # Python 3.13 virtualenv

/mnt/agents/iris/                # Agent config (NAS, survives VM rebuild)
  CLAUDE.md                            # VM operations manual
  BACKLOG.md                           # Sprint tasks and priorities
  MEMORY.md                            # Long-term memory index
  memory/                              # Session memory files
  docs/                                # Project reference docs

/mnt/iris/                             # Iris data volume (NAS)
  backups/                             # Daily pg_dump files
  verdify-vault/                       # Obsidian vault (daily summaries, crop records)
```

## Portability

To rebuild this VM from scratch:

1. **Provision VM** — Debian 13, 4 vCPU, 8GB RAM, 125GB disk
2. **Mount NFS** — `/mnt/jason` (agents), `/mnt/iris` (data)
3. **Clone repo** — `git clone git@github.com:jvallery/agents.git /mnt/agents`
4. **Install Docker** — `apt install docker.io docker-compose-plugin`
5. **Install Python** — Build 3.13 venv at `/srv/greenhouse/.venv/`
6. **Copy secrets** — `.env` files from vault, `secrets.yaml` from vault
7. **Start stack** — `cd /srv/verdify && docker compose up -d`
8. **Start ingestor** — `systemctl enable --now verdify-ingestor`
9. **Restore DB** — `pg_restore -U verdify -d verdify /mnt/iris/backups/latest.dump`
10. **Install crontab** — From this document's cron section
11. **DNS** — Point `*.verdify.ai` through nexus Traefik, `mqtt.verdify.ai` via local DNS to VM IP

All configuration lives on NAS (`/mnt/agents/iris/`) or in git. The VM itself is disposable — only Docker volumes (DB data, Grafana state) need backup, and DB is backed up nightly to NFS.
