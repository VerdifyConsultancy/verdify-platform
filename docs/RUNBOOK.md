# Verdify Operations Runbook

**VM:** vm-docker-iris (192.168.30.150)
**Last updated:** 2026-05-16 (Hermes/refocus reconciliation)

---

## 2026-04-20 Updates (since 2026-03-29)

Consolidated delta from the dual-Iris rollout + overnight incidents. The gateway section is historical; production planning routes through Hermes. Repo source selects Cortex's OpenAI-compatible 98K text route `llm.primary.longctx` with explicit context/output budgets and tool-use guidance, while Verdify activation remains separately gated. Cortex vLLM 0.27.1 supplies the verified medium reasoning default and advertises the route context/output contract. Read these before debugging any planner/alert-related issue.

### Contract v1.4 + dual-Iris plumbing

- **`docs/iris-planner-contract.md`** is the historical v1.5 wire-protocol record. The current production gateway is Hermes `hermes-iris`; `plan_delivery_log.hermes_run_id`, `trigger_id`, lifecycle status, `acknowledge_trigger`, and the MCP-only allowlist are the active correlation surfaces.
- **Migration 093 applied** (2026-04-20 08:05 MDT): `plan_delivery_log` extended with `trigger_id`, `instance`, `acked_at`, `status` columns + CHECK constraint on status values. `plan_journal` and `setpoint_changes` got `planner_instance` + `trigger_id`.
- **Pydantic v1.4 audit fields** landed in `verdify_schemas/plan.py` and `setpoint.py` (commit `d822485`).
- Rollout phase: schema ✅ landed. genai Sub-scope A (prompt split) ✅ committed local on `genai/sprint-3-mcp-contract` branch. Sub-scope B (MCP header stamping + acknowledge_trigger) blocked on Iris's Q5 FastMCP smoke test answer. Ingestor sprint-25 (consume new signature) blocked on genai B.

### Alert changes

- **`planner_stale` threshold: 8h → 14h** (PR #15 merged `6c2f5b3` 2026-04-20). Rationale: SUNSET→SUNRISE gap is ~12.7h; 14h = that + 1.3h slack, fires only on genuine SUNRISE miss. See `ingestor/tasks.py:675-691`.
- **`setpoint_unconfirmed` lifecycle fixed** (sprint-24.7, `51c4781`). Was flapping every 5 min because `alert_monitor`'s auto-resolve iterated ALL open alerts and closed any whose key wasn't in its cycle's active_keys; `setpoint_confirmation_monitor` now owns full lifecycle of its own alerts. Alert rule auto-resolve now filters to `source='system'`.
- **OBS-3 rules added** (sprint-24.7): `firmware_relief_ceiling` (warn@2, crit@3) and `firmware_vent_latched` (warn@600s, crit@1200s). Reads diagnostics columns Sprint 18 added but alert_monitor never consumed.
- **`midnight_watch` task** (sprint-24.7): ops stopgap that polls `plan_delivery_log` at 00:05–00:10 MDT for MIDNIGHT/TRANSITION:midnight_posture delivery. Posts one Slack message (✅ / 🟡 / 🔴). Retires when sprint-25's per-pair SLA rule lands.
- **MIDNIGHT dispatch bug** (sprint-24.8, `98ff9a1`): single-line fix — cached milestone was set to `today + 1 day` at date rollover, making it perpetually 24h in the future. Now `datetime.combine(today, …)` so first task_loop past 00:00 hits firing window. Overnight `midnight_watch` 🔴 at 00:05 correctly surfaced this.

### Firmware changes (sprint-11, `b8bcfb7`)

- **Day/night setpoint pairs REMOVED entirely.** Sprint-10 phase-0 added them; they silently outranked the dispatcher's crop band overnight 2026-04-19/20, plants tracked firmware's invented 62-68°F night band for 10h, temp bottomed at 53.7°F, 41 SEALED_MIST transitions.
- **New architectural rule:** **two sources of band in the firmware, and only two:**
  1. **Safety rails** (`safety_min/max`, `vpd_min_safe`, `vpd_max_safe`) — hard backstops, fallback only.
  2. **Dispatcher-pushed band** (`temp_low/high`, `vpd_low/high`) — drives the band-first controller.
- No photoperiod switches, no day/night resolution, no `resolve_active_band()` helper. If the dispatcher goes silent, firmware falls through to permissive defaults (40-95°F / 0.35-2.8 kPa) + safety rails.

## iris-dev /loop (permanent operating mode)

When Jason is away, iris-dev runs `/loop` in dynamic mode to monitor + coordinate without active user input.

- **Start:** Jason invokes `/loop "<coordinator prompt>"` when disconnecting. Prompt is in `~/.claude-agents/iris-dev/plans/yo-iris-dev-you-help-humming-stonebraker.md`.
- **Stop:** Any non-`/loop` message to iris-dev ends the cycle and hands control back.
- **Cadence:** Dynamic via `ScheduleWakeup`. 60-270s cache-warm when active, 1200-1800s idle default, 3600s quiet-night cap.
- **Each cycle:** scans tmux (agent-iris-iris-planner, agent-iris-iris, agent-verdify-{genai,ingestor,web,firmware,saas}), git log on main, `plan_delivery_log` status distribution, `setpoint_changes` age, open critical alerts. Updates `~/.claude-agents/iris-dev/projects/-mnt-iris-verdify/memory/STATUS.md`.
- **Emergency triggers** (wake Jason via PushNotification + best-effort Slack):
  - `temp_safety` critical (<40°F or >100°F inside greenhouse)
  - `leak_detected` critical
  - DP margin <2°F (condensation imminent)
- **NOT emergencies** (daily digest instead): Iris silent across scheduled events, ESP32 push failures, dispatcher tick gaps, non-critical alerts, routine infra restarts.
- **Commit Monitor** armed (task id varies per session) — fires notification on any new commit on main. iris-dev handles in-context and reschedules.

## Quick health check (expanded, 2026-04-20)

Run in this order; all should pass. Takes <30 seconds.

```bash
# 1. DB
docker exec verdify-timescaledb pg_isready  # → accepting connections

# 2. Hermes gateway (Iris's dispatch endpoint)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8642/health  # → 200

# 3. Core services
for s in verdify-ingestor verdify-mcp verdify-setpoint-server; do
  systemctl is-active $s
done  # → active, active, active

# 4. Dispatcher recency (should be within last 6 min)
docker exec verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT now() - max(ts) AS since_last_push FROM setpoint_changes;"

# 5. Active plan horizon (should extend at least 12h into the future)
docker exec verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT plan_id, max(ts) AS horizon FROM setpoint_plan WHERE is_active AND ts > now() GROUP BY plan_id;"

# 6. Open critical alerts (should be 0)
docker exec verdify-timescaledb psql -U verdify -d verdify -c \
  "SELECT count(*) FROM alert_log WHERE disposition='open' AND severity='critical';"

# 7. Grafana
curl -s -o /dev/null -w '%{http_code}\n' https://graphs.verdify.ai/api/health  # → 200
```

If any returns unexpected, consult the failure-mode section below.

## Applying a migration to prod

Discovered 2026-04-20: `docker exec -i verdify-timescaledb psql -U verdify -d verdify < db/migrations/NNN-*.sql` works. Online-safe ALTERs are routine; destructive DDL (DROP/TRUNCATE) needs explicit validation.

Example (migration 093 applied this morning):
```bash
docker exec -i verdify-timescaledb psql -U verdify -d verdify < /mnt/iris/verdify/db/migrations/093-planner-instance-audit.sql
```

Verify:
```bash
docker exec verdify-timescaledb psql -U verdify -d verdify -c "\d plan_delivery_log" | grep -E "trigger_id|instance|acked_at|status"
```

---



| Service | Type | Restart | Check |
|---------|------|---------|-------|
| verdify-ingestor | systemd | `sudo systemctl restart verdify-ingestor` | `systemctl status verdify-ingestor` |
| verdify-setpoint-server | systemd | `sudo systemctl restart verdify-setpoint-server` | `curl -s http://127.0.0.1:8200/setpoints \| head -3` |
| verdify-timescaledb | Docker | `cd /srv/verdify && docker compose restart timescaledb` | `docker exec verdify-timescaledb pg_isready` |
| verdify-grafana | Docker | `cd /srv/verdify && docker compose restart grafana` | `docker exec verdify-grafana curl -s http://localhost:3000/api/health` |
| verdify-traefik | Docker | `cd /srv/verdify && docker compose restart traefik` | `curl -sk https://127.0.0.1:443/` |

## Quick Health Check

```bash
bash /mnt/agents/iris/health-check.sh
```

Expected: 22/22 passing, HEALTHY.

## Log Locations

| Service | Log Command |
|---------|------------|
| Ingestor | `journalctl -u verdify-ingestor -f` |
| Setpoint server | `journalctl -u verdify-setpoint-server -f` |
| TimescaleDB | `docker logs verdify-timescaledb --tail 50` |
| Grafana | `docker logs verdify-grafana --tail 50` |
| Liveness | `tail -20 /srv/verdify/state/liveness.log` |
| Alert monitor | part of ingestor (task_loop) |

## Common Failure Modes

### ESP32 unreachable (ping fails)
**Symptoms:** Climate data stops, equipment state stale, health check fails on "Climate" freshness.
**Fix:**
1. Check ESP32 power (physical)
2. Check WiFi (ESP32 connects to IoT VLAN 192.168.10.x)
3. Restart ingestor: `sudo systemctl restart verdify-ingestor`
4. If ESP32 needs reboot: power cycle the controller box

### Ingestor crash loop
**Symptoms:** `systemctl status verdify-ingestor` shows repeated restarts.
**Fix:**
1. Check logs: `journalctl -u verdify-ingestor --since "10 min ago" --no-pager`
2. Common causes: ESP32 unreachable, DB connection refused, Python error
3. RestartSec=30 prevents tight loops

### No data in Grafana ("No data" panels)
**Symptoms:** Dashboard panels blank.
**Fix:**
1. Check data freshness: `docker exec verdify-timescaledb psql -U verdify -d verdify -c "SELECT MAX(ts) FROM climate;"`
2. If stale: restart ingestor
3. If data exists but Grafana blank: reload dashboards:
   ```bash
   GRAFANA_PW=$(grep GRAFANA_ADMIN_PASSWORD /srv/verdify/.env | cut -d= -f2)
   docker exec verdify-grafana curl -s -u "admin:${GRAFANA_PW}" -X POST http://localhost:3000/api/admin/provisioning/dashboards/reload
   ```

### Setpoint compatibility server unreachable
**Symptoms:** `curl http://127.0.0.1:8200/setpoints` fails, lighting diagnostics or recovery tooling cannot inspect the key=value export. Current firmware setpoint delivery should still work through the ingestor's ESPHome push/readback path.
**Fix:**
1. Check: `curl -s http://127.0.0.1:8200/setpoints | head -3`
2. Restart: `sudo systemctl restart verdify-setpoint-server`
3. Verify the ingestor dispatcher is healthy: `systemctl status verdify-ingestor`

### Grow lights don't toggle
**Symptoms:** gl_auto_mode is ON but lights don't turn on/off.
**Fix:**
1. Chain: ESP32 → HA service call / setpoint-server → HA REST → Lutron switch
2. Check the traceability gate: `make lighting-audit-current`
3. Check setpoint-server state: `curl -s http://127.0.0.1:8200/lights`
4. Check HA state directly:
   ```bash
   curl -s -H "Authorization: Bearer $(cat /mnt/agents/shared/credentials/ha_token.txt)" \
     http://192.168.30.107:8123/api/states/switch.greenhouse_main
   curl -s -H "Authorization: Bearer $(cat /mnt/agents/shared/credentials/ha_token.txt)" \
     http://192.168.30.107:8123/api/states/switch.greenhouse_grow
   ```
5. Check the live policy and expected/actual state:
   ```bash
   docker exec verdify-timescaledb psql -U verdify -d verdify -P pager=off -c \
     "SELECT light_key, expected_on, actual_on, natural_lux, lux_on_threshold, lux_off_threshold, firmware_state, firmware_reason FROM v_lighting_circuit_status_now ORDER BY light_key;"
   ```
6. Physical: check Lutron Caseta bridge power, check physical switch position

### Lighting automation proof gate
**Use when:** changing lighting tunables, lighting dashboards, setpoint
dispatch, Lutron control, or firmware lighting logic.

1. Static proof, no live services required:
   ```bash
   make lighting-audit-static
   ```
2. Current production proof. This is allowed to report `BLOCKED` for
   OTA-dependent per-circuit firmware readbacks while the live ESP32 is still on
   the shared lighting firmware:
   ```bash
   make lighting-audit-current
   ```
3. Strict post-OTA proof. This must pass before claiming per-circuit lighting is
   live end-to-end:
   ```bash
   make lighting-audit-live
   ```
4. Completion proof. This treats missing OTA/post-OTA evidence as `FAIL`
   instead of `BLOCKED`, and is the command to use before closing the lighting
   automation objective:
   ```bash
   make lighting-audit-complete
   ```

`make lighting-audit-live` and `make lighting-audit-complete` require all of
the following:

- Per-circuit `cfg_gl_main_*`, `cfg_gl_grow_*`, `cfg_gl_main_auto_mode`, and
  `cfg_gl_grow_auto_mode` readbacks.
- Confirmed per-circuit `setpoint_changes` rows.
- Non-empty `firmware_state` and `firmware_reason` for both circuits.
- ON and OFF `equipment_state` evidence for `grow_light_main` and
  `grow_light_grow` after the latest firmware start.
- Live Grafana/homepage panels bound to the same policy/timeline views.

For firmware source changes before commit or OTA, also run:

```bash
make firmware-invariants
make firmware-replay-worktree
```

### Modbus bus failure (sensors return NaN)
**Symptoms:** Zone temperatures NULL, soil sensors offline.
**Fix:**
1. Check which probes respond: stop ingestor, run ESPHome logs for 30s
2. Common cause: loose RS485 connection at daisy chain splice
3. Physical: check A/B wiring at each junction point
4. Bus order: Case(1) → North(2) → West(3) → South(4) → East(5) → Intake(6) → Soil(7,8,9)

## Backup & Restore

### Daily backup (automatic)
Runs at 01:00 UTC via cron:
```
docker exec verdify-timescaledb pg_dump -U verdify -Fc verdify > /mnt/iris/backups/verdify-YYYYMMDD.dump
```
Retention: 7 days.

### Manual backup
```bash
docker exec verdify-timescaledb pg_dump -U verdify -Fc verdify > /mnt/iris/backups/verdify-manual-$(date +%Y%m%d-%H%M).dump
```

### Restore from backup
```bash
# Stop services
sudo systemctl stop verdify-ingestor verdify-setpoint-server

# Drop and recreate DB
docker exec verdify-timescaledb psql -U verdify -c "DROP DATABASE IF EXISTS verdify_restore;"
docker exec verdify-timescaledb psql -U verdify -c "CREATE DATABASE verdify_restore;"
cat /mnt/iris/backups/verdify-YYYYMMDD.dump | docker exec -i verdify-timescaledb pg_restore -U verdify -d verdify_restore

# Swap databases
docker exec verdify-timescaledb psql -U verdify -c "ALTER DATABASE verdify RENAME TO verdify_old;"
docker exec verdify-timescaledb psql -U verdify -c "ALTER DATABASE verdify_restore RENAME TO verdify;"

# Restart services
sudo systemctl start verdify-ingestor verdify-setpoint-server
```

## Ports & Firewall

| Port | Binding | Service | Access |
|------|---------|---------|--------|
| 443 | 0.0.0.0 | Traefik | Public (graphs.verdify.ai) |
| 22 | 0.0.0.0 | SSH | Fleet |
| 8200 | 0.0.0.0 | Setpoint server | ESP32 only (UFW: 192.168.10.0/24) |
| 9100 | 0.0.0.0 | Node exporter | Prometheus (nexus) |
| 9323 | 0.0.0.0 | Docker metrics | Prometheus (nexus) |
| 5432 | 127.0.0.1 | TimescaleDB | Localhost only |
| 3000 | Docker internal | Grafana | Via Traefik only |

## Secrets

| File | Contains | Perms |
|------|----------|-------|
| /srv/verdify/.env | POSTGRES_PASSWORD, GRAFANA_ADMIN_PASSWORD, GRAFANA_API_TOKEN | 600 |
| /srv/verdify/ingestor/.env | DB credentials, ESP32_API_KEY (noise_psk) | 600 |
| /srv/greenhouse/esphome/secrets.yaml | WiFi SSID/pass, OTA password, API key | 600 |
| /mnt/agents/shared/credentials/ | HA token, Slack bot token | Fleet shared |

## Architecture

```
Internet → Cloudflare → Nexus Traefik → Iris Traefik (443) → Grafana (3000)

ESP32 (192.168.10.111) ←→ Ingestor (aioesphomeapi, port 6053; telemetry + setpoint push/readback)
Setpoint server (port 8200) → compatibility `/setpoints` and light-control helper endpoints

Ingestor (PID 1, systemd) runs:
  - esp32_loop: persistent aioesphomeapi subscription
  - flush_loop: 60s climate/diagnostics writes
  - task_loop: 11 periodic tasks (shelly, tempest, ha_sensor, alerts, dispatcher, forecast, etc.)
  - mqtt_loop: Sentinel occupancy via MQTT
  - setpoint_listener: LISTEN/NOTIFY for real-time ESP32 push

Cron (9 jobs): token sync, daily summary, vault writers, frigate snapshots, checklist, hydro map, DB backup, liveness
```
