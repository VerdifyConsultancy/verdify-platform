# systemd unit files (tracked copies)

These are every systemd `.service`, `.timer`, and `.path` unit that runs on `vm-docker-iris`, plus the Verdify logrotate policy and user crontab for the same host. Canonical systemd install location is `/etc/systemd/system/`; the logrotate file installs to `/etc/logrotate.d/verdify`; the crontab installs with `crontab systemd/jason.crontab`. These copies exist so a fresh VM rebuild can restore them from git:

```bash
sudo cp systemd/*.service systemd/*.timer /etc/systemd/system/
sudo cp -r systemd/*.service.d /etc/systemd/system/
sudo install -m 0644 systemd/logrotate-verdify /etc/logrotate.d/verdify
crontab systemd/jason.crontab
sudo systemctl daemon-reload
sudo systemctl enable --now \
  verdify-ingestor.service \
  verdify-mcp.service \
  verdify-api.service \
  verdify-setpoint-server.service \
  verdify-site-poll.timer
# Forecast-page, Grafana cache warm, and site build services are triggered by
# timer/poller units and do not need to be enabled directly except where noted.
```

## Files

| File | Type | Purpose | How it starts |
|---|---|---|---|
| `verdify-ingestor.service` | `simple` (always-on) | ESP32 → TimescaleDB data pipeline, 15 periodic tasks | `enable --now` |
| `verdify-mcp.service` | `simple` (always-on) | MCP server on localhost:8000; 18 tools for Iris planner | `enable --now` |
| `verdify-api.service` | `simple` (always-on) | FastAPI crop/catalog API (port 8300 where systemd-managed) | `enable --now` |
| `verdify-setpoint-server.service` | `simple` (always-on) | ESP32 text `/setpoints` endpoint and light HA bridge (port 8200) | `enable --now` |
| `verdify-ingestor.service.d/restart.conf` | drop-in | Keeps ingestor restart pacing aligned with production | copied with unit files |
| `verdify-mcp.service.d/bind.conf` | drop-in | Binds MCP HTTP host to `0.0.0.0` as production currently runs it | copied with unit files |
| `verdify-setpoint-server.service.d/restart.conf` | drop-in | Keeps setpoint server restart pacing aligned with production | copied with unit files |
| `verdify-forecast-page.timer` | `Timer` | Runs the unified generated-site publisher after forecast refresh cadence | `enable --now` |
| `verdify-forecast-page.service` | `oneshot` | `scripts/publish-site-content.sh --reason forecast` | triggered by timer |
| `verdify-site-poll.timer` | `Timer` | Fires every 10 s | `enable --now` |
| `verdify-site-poll.service` | `oneshot` | `scripts/site-poll-and-rebuild.sh` — mtime check vs marker; rebuilds if vault changed | triggered by timer |
| `verdify-site-build.service` | `oneshot` | `scripts/rebuild-site.sh` (flock + 5 s debounce + `npx quartz build` + `docker restart verdify-site`) | invoked from poll script, or `make site-rebuild` |
| `logrotate-verdify` | logrotate policy | Rotates `/var/local/verdify/state/*.log`; uses `su root users` because systemd append logs are root-owned | installed to `/etc/logrotate.d/verdify` |
| `jason.crontab` | crontab | Captures DB backup, daily vault writers, snapshots, metrics, Slack archive, and plan publish jobs | installed with `crontab` |

## Why polling instead of inotify for site-build

inotify on NFS mounts does not reliably fire for writes originated by the NFS server (e.g. a file that arrives via Syncthing on the NAS). The original `verdify-site-build.path` (inotify-backed) unit confirmed this in production — it didn't trigger on a real Mac→Obsidian save, even though the VM could see the new mtime. Replaced 2026-04-18 with the 10-second `verdify-site-poll.timer`, which is filesystem-agnostic. Latency: 10–20 s typical, worst 20 s.

## Secrets referenced by these units

These `.env` / secrets files live on the VM outside the git tree and are preserved by Proxmox VM snapshots:

- `/srv/verdify/.env` — `POSTGRES_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`
- `/srv/verdify/api/.env` — API-specific
- `/srv/verdify/ingestor/.env` — `ESP32_HOST`, `ESP32_PORT`, `ESP32_API_KEY`, DB credentials
- `/srv/greenhouse/esphome/secrets.yaml` — WiFi SSID/password, ESP32 API key, OTA password
- Fleet-shared credentials in `/mnt/agents/shared/credentials/`

Canonical recovery (if VM snapshots are unavailable): Orbit vault → `/mnt/agents/root/secrets/` → `/mnt/agents/shared/credentials/` → per-VM `.env`. See fleet `ARCHITECTURE.md` and the credential-management doc.
