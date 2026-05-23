# BCDR + Operations — Verdify + Iris

> Where the stack is safe, where it isn't, and what to do about it.

Last updated: 2026-05-23

This document is prescriptive: "here's what could go wrong and what's in place (or missing) to handle it." It's a companion to `FOLDER-HIERARCHY.md` (where things live) and `IRIS-PROVISIONING-RUNBOOK.md` (how to build a new VM from scratch — in `/mnt/agents/iris/docs/`).

## 1. What is actually protected right now

| Asset | Backup mechanism | RPO | RTO | Tested? |
|---|---|---|---|---|
| **Source code (verdify)** | Pushed to `jrvallery/verdify` on GitHub after every commit | = last push | minutes (git clone) | yes — continuously |
| **Vault content (verdify-vault)** | Pushed to `jvallery/verdify-vault` on GitHub + replicated to Mac via Syncthing | = last push / last save | minutes (git clone or Mac restore) | yes — verified during Sprint 16.4 migration |
| **Agent config (iris/)** | Pushed to `jvallery/agents` on GitHub | = last push | minutes | yes |
| **TimescaleDB data** | Nightly `pg_dump -Fc` to `/mnt/iris/backups/verdify-YYYYMMDD.dump` | 24 h | ~10 min restore | **no — never practiced** |
| **NAS itself** | Synology SHR-2 (2-drive redundancy) + some folders replicated to external drive via Hyper Backup (per Jason) | varies | varies | partial |
| **Firmware binaries** | Git-tracked + compile-on-demand via ESPHome | = last commit | ~3 min to recompile + OTA | yes — validated by deploy protocol |
| **Grafana dashboards** (JSON) | Git-tracked in verdify repo | = last push | seconds to restore | yes |

## 2. What is NOT protected (the real gaps)

### 2a. Host service definitions must stay source-controlled

**Risk:** VM rebuild loses service configuration if `/etc/systemd/system/`, logrotate, or the user crontab drift from git.

Current status after the 2026-05-23 cleanup:
- All live `verdify-*` unit files under `/etc/systemd/system/` are represented in `/mnt/iris/verdify/systemd/`.
- Active drop-ins for `verdify-ingestor`, `verdify-mcp`, and `verdify-setpoint-server` are tracked under `systemd/*.service.d/`.
- Verdify logrotate policy is tracked as `systemd/logrotate-verdify`.
- Jason's Verdify crontab is tracked as `systemd/jason.crontab`.
- Disabled legacy `verdify-forecast.service` and `verdify-forecast.timer` were moved to `/mnt/iris/backups/verdify-host-cleanup-20260523/systemd-legacy/`; live forecast sync is owned by ingestor.

**Mitigation:** After host service edits, copy the changed unit/drop-in/logrotate/crontab artifact back into `/mnt/iris/verdify/systemd/`, commit it, and run `systemctl daemon-reload` where needed.

### 2b. DB restore has never been practiced

**Risk:** Nightly dumps exist but no one has ever restored one. A corrupted dump format, an incompatible Postgres/TimescaleDB version upgrade, or a pg_dump flag regression would go unnoticed until a real disaster.

**Mitigation:** Quarterly restore-test into a scratch container. A `scripts/restore-test.sh` could do this end-to-end:
1. Spin up an ephemeral TimescaleDB container
2. `pg_restore` the latest dump
3. Run 3–5 sanity queries (row counts on `climate`, `alert_log`, latest ts)
4. Destroy the container
5. Alert if any step fails

Tracked as `OB-7` (new). Est. 2 hours.

### 2c. No continuous WAL archiving

**Risk:** Up to 24 h of data loss if the TimescaleDB volume is destroyed between 01:00 UTC dumps. That's 86,400 potential climate rows, alerts, planner decisions.

**Mitigation:** Enable PostgreSQL WAL archiving to `/mnt/iris/backups/wal/`. Reduces RPO from 24 h to ~5 min. Adds some NAS I/O. Non-trivial config change (`archive_mode`, `archive_command`, restore procedure).

Tracked as `OB-8` (new). Est. 4 hours. Value: medium — a greenhouse doesn't meaningfully change in 24 h, but losing a day of planner learning stings.

### 2d. Docker volumes aren't snapshot-backed

**Risk:** `verdify_grafana_data` (UI prefs, custom alert rules), `verdify_mqtt_data` (retained messages), `verdify_promtail_positions` — all lost on volume corruption. Minor individually; annoying combined.

Grafana state is recoverable-ish because dashboards are provisioned from git. UI edits and any manually-created alerting live only in the volume.

**Mitigation:** `docker run --rm -v verdify_grafana_data:/source -v /mnt/iris/backups:/dest alpine tar czf /dest/grafana-$(date +%Y%m%d).tar.gz -C /source .` — cron this nightly alongside pg_dump. Tracked as `OB-9`. Est. 30 min.

### 2e. Secrets recovery is undocumented

**Risk:** Today's secrets live across several paths:
- `/srv/verdify/.env` (POSTGRES_PASSWORD, GRAFANA_ADMIN_PASSWORD)
- `/srv/verdify/ingestor/.env` (ESP32 credentials)
- `/srv/greenhouse/esphome/secrets.yaml` (WiFi SSID/pw, ESP32 API key, OTA pw)
- `/mnt/agents/shared/credentials/` (Anthropic key, Gemini key, HA token, Slack bot token)
- Traefik basic-auth tokens in `/mnt/iris/verdify/traefik/dynamic/`

If this VM is destroyed, rebuilding requires retrieving all of these from the Orbit vault (per CLAUDE.md credential flow).

**Mitigation:** A `docs/SECRETS-RECOVERY.md` with:
- Exact paths of every secret on the VM
- Which ones come from the Orbit vault (`credentials.md`) vs. which are generated
- The rebuild procedure: vault → root/secrets/ → shared/credentials/ → per-VM .env files
- Who holds the master vault (single point of authority)

Tracked as `OB-10`. Est. 1 hour. **High value** — a VM rebuild without this doc would take a day to figure out.

### 2f. ESP32 firmware has no auto-rollback

**Risk:** Two OTAs happened in quick succession today (Sprint 16 + Sprint 16.1). Both went clean, but if a future OTA wedged the ESP32 into a reboot loop, there's no automatic rollback. The recovery procedure is: serial-console onto the ESP32, flash a factory image.

Firmware test layers (unit + replay + post-deploy sensor health) catch a LOT, but not: memory corruption, undefined behavior, I²C driver regressions under specific hardware conditions.

**Mitigation:** Keep a known-good binary alongside every OTA — `/mnt/iris/verdify/firmware/artifacts/good/greenhouse-YYYYMMDD-HASH.ota.bin`. On post-deploy `make sensor-health` failure, script an auto-rollback via `esphome upload --device ... previous.bin`. Tracked as `FW-15` (new). Est. 3 hours. **High value** — the greenhouse can't run without the ESP32.

### 2g. NAS is a single point of failure for everything on NFS

**Risk:** If the NAS goes down, the VM's NFS mounts hang. The ingestor, MCP, and site-poller would block on file reads. Eventually the kernel would mark the mount "hard" and wedge.

What happens to the greenhouse: **nothing, short-term**. The ESP32 keeps running on its last setpoints for hours or days (deterministic firmware is the safety layer, per design). But the planner stops planning, the website freezes, and no new data lands in the DB.

**Mitigation options, in priority order:**
1. **NAS redundancy** — Synology SHR-2 already survives single-drive failure. Verify snapshots exist + are healthy. Low-effort; probably already in place.
2. **Mount with `soft,timeo=30`** (already set per `mount | grep iris`) — NFS calls fail instead of hang. Services return errors, systemd restarts them in a loop until NAS recovers. Better failure mode than hang.
3. **Read-only failover** — a secondary NAS or local cached snapshot of `verdify/` so the ingestor can run degraded during NAS outages. Significant work; probably not worth it for this use case.
4. **Alert on NFS stall** — Prometheus node_exporter can expose `node_nfs_requests_total`. Alert if it stops incrementing.

Tracked as `OB-11`. Est. 2 hours for #1 + #4 verification. #3 is deferred.

### 2h. The site-poll timer has no health alert

**Risk:** If `verdify-site-poll.timer` silently stops firing, the website stops updating and nobody notices until someone saves a file in Obsidian and says "hey the site isn't updating."

**Mitigation:** The existing `sensor-health-sweep.sh` could learn one more check: last `/var/local/verdify/state/site-build-last-run` marker age should be < 10 minutes. If stale, warn. Tracked as `OB-12`. Est. 20 min.

### 2i. GitHub is a single point of failure for the bus factor

**Risk:** If `jvallery` account is compromised or suspended, every repo vanishes. Three repos × one account.

**Mitigation:** Weekly `git bundle` of each repo to `/mnt/iris/backups/git-bundles/`. `git bundle create X.bundle --all`. Restorable from bundle with `git clone file.bundle`. Tracked as `OB-13`. Est. 30 min.

## 3. Risk matrix (prioritized)

| Risk | Likelihood | Impact | Priority | Tracked |
|---|---|---|---|---|
| Secrets recovery undocumented (VM rebuild) | Medium (once a year-ish) | **High** (days of figuring out) | **P0** | OB-10 |
| ESP32 wedge after bad OTA | Low | **High** (no climate control) | **P0** | FW-15 |
| Host service config drifts from git | Medium (VM rebuild) | Medium | **P1** | `systemd/README.md` |
| DB restore untested | Low-medium | High | **P1** | OB-7 |
| Site-poll silently stops | Low | Low-medium | **P2** | OB-12 |
| 24 h data loss from missing WAL | Low | Medium | **P2** | OB-8 |
| Grafana volume lost | Low | Low (regenerable) | **P3** | OB-9 |
| GitHub account gone | Very low | High | **P3** | OB-13 |
| NAS SPOF | Very low (SHR-2) | **High** (read degradation) | **P2** | OB-11 |

## 4. Recovery runbooks (quick reference)

### 4a. Ingestor stopped writing to DB

```
systemctl status verdify-ingestor       # running? failed?
journalctl -u verdify-ingestor --since "15 min ago" | tail -40
ping 192.168.10.111                     # ESP32 reachable?
docker exec verdify-timescaledb psql -U verdify -d verdify -c "SELECT max(ts) FROM climate"
systemctl restart verdify-ingestor      # if in doubt
make sensor-health                      # validate end-to-end
```

### 4b. Website stops updating

```
systemctl status verdify-site-poll.timer
systemctl list-timers verdify-site-poll.timer
tail -20 /var/local/verdify/state/site-build.log
bash /srv/verdify/scripts/rebuild-site.sh   # manual kick if needed
```

### 4c. TimescaleDB corruption / data loss

```
# 1. Stop writes
docker compose stop timescaledb ingestor mcp
# 2. Backup current (even if corrupt)
docker run --rm -v verdify_tsdb_data:/src -v /mnt/iris/backups:/dest alpine \
  tar czf /dest/tsdb-pre-restore-$(date +%Y%m%d-%H%M).tar.gz -C /src .
# 3. Drop and recreate the container volume
docker volume rm verdify_tsdb_data
docker compose up -d timescaledb
# 4. Restore from latest nightly dump
latest=$(ls -t /mnt/iris/backups/verdify-*.dump | head -1)
docker exec -i verdify-timescaledb pg_restore -U verdify -d verdify -Fc < "$latest"
# 5. Restart everything, run smoke tests
docker compose up -d && make test
```

### 4d. Full VM rebuild

See `/mnt/agents/iris/docs/IRIS-PROVISIONING-RUNBOOK.md`. Summary:

1. Provision new Debian VM, give it `192.168.30.150`, install docker + python3.13 + esphome
2. Mount NFS (`/mnt/iris/` from NAS)
3. Clone `jrvallery/verdify` into `/mnt/iris/verdify/` (if not already there — the repo is already NFS-resident, so this step is often skipped)
4. `ln -s /mnt/iris/verdify /srv/verdify`
5. `mkdir -p /var/local/verdify/{state,reports}` + symlinks from NFS tree
6. Install systemd units from `/mnt/iris/verdify/systemd/` (once OB tasks bring those to parity)
7. Retrieve secrets from Orbit vault (see OB-10 once written) → populate `.env` files
8. `docker compose up -d`
9. `systemctl enable --now verdify-{ingestor,mcp,api,setpoint-server,site-poll.timer}`
10. `make sensor-health` + `make test` to validate

### 4e. OTA wedged the ESP32

Current procedure (until FW-15 automates rollback):
1. Attempt `make firmware-deploy` of the prior commit's build
2. If that fails: serial-console via USB, flash `firmware/test/data/` recovery image OR the original factory bin
3. ESP32 is designed to survive OTA interruption — the prior image stays flashed until the new one is verified

## 5. Summary of follow-up backlog items

| ID | Priority | Estimate | Description |
|---|---|---|---|
| OB-10 | P0 | 1 h | Secrets recovery doc |
| FW-15 | P0 | 3 h | ESP32 OTA auto-rollback on sensor-health failure |
| IN-13 | P1 | done 2026-05-23 | Track live Verdify systemd units/drop-ins/logrotate/crontab in `systemd/` |
| OB-7 | P1 | 2 h | Quarterly DB restore-test script |
| OB-11 | P2 | 2 h | Verify NAS snapshot health + Prometheus NFS-stall alert |
| OB-12 | P2 | 20 min | Extend sensor-health with site-poll marker age check |
| OB-8 | P2 | 4 h | PostgreSQL WAL archiving to NAS |
| OB-9 | P3 | 30 min | Nightly tar backup of Grafana + MQTT + Promtail volumes |
| OB-13 | P3 | 30 min | Weekly git bundle of each of the three repos |

Total: 5 items at P0/P1 (~8 hours), 4 items at P2/P3 (~7 hours).
