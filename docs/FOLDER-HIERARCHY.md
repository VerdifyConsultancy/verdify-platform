# Verdify + Iris — folder hierarchy reference

> ⚠️ **STALE / VM-era (2026-05-23).** Describes the retired `/mnt/iris/verdify/`
> NFS layout + VM systemd/crontab, not the current **k3s** deployment. For where
> things live now (repo == source of truth, k8s overlays, GHCR digests, the
> in-cluster DB) see **`docs/handoff/k3s-agent-handoff.md`** and
> `docs/runbooks/laptop-operator.md`. Kept for historical reference; k3s cleanup
> tracked in #322/#339.

> Authoritative map of where every file lives across the stack. When something moves, update this doc.

Last updated: 2026-05-23

## The three git-tracked trees

All source-of-truth content lives on NFS (`/mnt/iris/` or `/mnt/jason/`). Three independent GitHub repos cover the stack:

| Repo | Mount path | Role |
|---|---|---|
| `jrvallery/verdify` | `/mnt/iris/verdify/` | Code, firmware, dashboards, migrations, scripts, tests |
| `jvallery/verdify-vault` | `/mnt/iris/verdify-vault/` | Obsidian vault: website content + Iris operational notes |
| `jvallery/agents` (iris/ subtree) | `/mnt/agents/iris/` | Agent config: CLAUDE.md, BACKLOG, memory, skills, docs |

### 1. `/mnt/iris/verdify/` → `jrvallery/verdify`

```
/mnt/iris/verdify/
├── docker-compose.yml          orchestrates production containers
├── Makefile                    entry point for every developer action
├── pyproject.toml              ruff + pytest config; project deps
├── README.md
│
├── ingestor/                   ESP32 data pipeline (always-running systemd service)
│   ├── ingestor.py                ESP32 subscribe loop + on_state_change router
│   ├── tasks.py                   15 periodic tasks
│   ├── entity_map.py              ESP32 object_id → DB column routing
│   ├── iris_planner.py            Event-driven Hermes planner dispatch
│   ├── config.py / shared.py      env + global state
│   ├── templates.py               planner prompt rendering
│   ├── ai_config.py               AI model selection for planner, vision, embeddings
│   └── requirements.txt
│
├── api/                        FastAPI crop catalog + compatibility /setpoints export
│   ├── main.py                    builds Docker image verdify-api
│   └── Dockerfile
│
├── mcp/                        MCP server (22 production tools exposed to Iris planner)
│   └── server.py                  systemd verdify-mcp.service on localhost:8000
│
├── firmware/                   ESPHome + C++ logic + native tests + replay harness
│   ├── greenhouse.yaml            main ESPHome entry point
│   ├── greenhouse/                controls/sensors/hardware/tunables/globals yaml
│   ├── lib/                       greenhouse_types.h, greenhouse_logic.h (pure C++)
│   ├── test/
│   │   ├── test_greenhouse_logic.cpp    61 native unit tests (make test-firmware)
│   │   ├── replay_overrides.cpp         replay harness against 179K rows of history
│   │   └── data/replay_overrides.csv.gz golden fixture (4.4 MB, 8 months telemetry)
│   └── secrets.example.yaml       template; real secrets.yaml only on VM, never in git
│
├── grafana/
│   ├── provisioning/
│   │   ├── dashboards/json/*.json      55 dashboard files — SINGLE SOURCE OF TRUTH
│   │   ├── dashboards/provider.yml
│   │   └── datasources/
│   └── custom/                         legacy CSS assets (live render cache is under deploy/k8s)
│
├── db/
│   ├── schema.sql                      authoritative dump (make db-dump)
│   ├── migrations/                     80 numbered SQL files
│   └── init/                           TimescaleDB first-boot seed
│
├── scripts/                    ~30 Python + bash scripts (cron-triggered + manual)
│   ├── rebuild-site.sh                 Quartz build + nginx restart (flock + debounce)
│   ├── site-poll-and-rebuild.sh        mtime-based change detector (10s cadence)
│   ├── sensor-health-sweep.sh          make sensor-health (firmware protocol layer 3)
│   ├── export-replay-overrides.sh      regenerates the golden firmware fixture
│   └── (vault writers, frigate snapshot, daily summary, metrics, ...)
│
├── tests/                      101 Python smoke tests (make test)
├── templates/                  Jinja2 planner prompts + reference
├── config/                     ai.yaml, zones.yaml
├── docs/                       SYSTEM-ARCHITECTURE, BCDR-AND-OPERATIONS, this file
├── traefik/                    reverse proxy + Let's Encrypt wildcard cert state
├── mqtt/                       Mosquitto broker config
├── promtail/                   log shipper → nexus Loki
│
├── systemd/                    tracked copies of host service artifacts
│   ├── README.md
│   ├── jason.crontab                  host crontab snapshot
│   ├── logrotate-verdify              /etc/logrotate.d/verdify
│   ├── verdify-*.service / *.timer / *.path
│   ├── verdify-*.service.d/           production drop-ins
│   ├── verdify-site-poll.timer         fires every 10s
│   ├── verdify-site-poll.service       runs site-poll-and-rebuild.sh
│   └── verdify-site-build.service      runs rebuild-site.sh
│
├── site/                       Quartz config (the code, not the content)
│   ├── quartz.config.ts                contentFolder → verdify-site/content (symlinked)
│   └── quartz.layout.ts
│
├── verdify-site/               Quartz runtime + build output (NOT git-tracked)
│   ├── content → /mnt/iris/verdify-vault/website    SYMLINK to vault
│   ├── public/                         Quartz HTML output (nginx serves this)
│   ├── node_modules/                   regenerable via npm install
│   └── nginx.conf                      bind-mounted into verdify-site container
│
├── state → /var/local/verdify/state           SYMLINK — keeps state on local SSD
├── reports → /var/local/verdify/reports       SYMLINK — keeps reports on local SSD
│
└── .github/workflows/ci.yml    ruff lint + esphome config validate on push/PR
```

### 2. `/mnt/iris/verdify-vault/` → `jvallery/verdify-vault`

Obsidian-editable. Syncthing shares this with the Mac. The `website/` subtree drives lab.verdify.ai; everything else is Iris's operational notes.

```
/mnt/iris/verdify-vault/
├── .gitignore                  git-level ignores (workspace.json, .sync-conflict-*)
├── .stignore                   Syncthing ignores — .git is excluded (critical)
├── dashboard.md                top-level dashboard (Obsidian home)
│
├── website/                    lab.verdify.ai public content
│   ├── index.md / about.md / project/
│   ├── greenhouse/             structure, equipment, growing, hydroponics, crops/, zones/
│   ├── climate/                controller, cooling, heating, humidity, lighting, water
│   ├── evidence/               dashboards, operations, economics, plans/ (18 daily plan archives)
│   ├── intelligence/           planner architecture, lessons, compliance
│   └── static/                 photos + built asset backups
│
├── crops/                      Iris-owned crop records (not on website)
├── daily/                      daily operational notes (Iris-authored)
├── zones/                      zone-specific observations
├── planning/                   sprint + planning notes
├── reference/                  crop profiles, pest ID, equipment specs
├── recipes/                    nutrient recipes
├── snapshots/                  plant photos + AI observations
└── assets/                     photo library + derived thumbnails
```

### 3. `/mnt/agents/iris/` → `jvallery/agents/iris/`

Part of the fleet-wide agents repo (siblings: `ace/`, `bloom/`, `backup/`, `cortex/`, `haos/`, `sentinel/`, `nexus/`, `orbit/`, `root/`, ...).

```
/mnt/agents/iris/
├── CLAUDE.md                   this instance's runtime config
├── BACKLOG.md                  sprint roadmap + completed sprints
├── AGENTS.md                   session startup conventions
├── SOUL.md / USER.md / SYSTEM.md / TOOLS.md / IDENTITY.md
├── HEARTBEAT.md
├── MEMORY.md                   long-term memory index
│
├── memory/                     daily session logs (one file per day)
├── docs/
│   ├── VERDIFY-REQUIREMENTS.md
│   ├── DATA-TAXONOMY.md
│   ├── GREENHOUSE-PORTRAIT.md
│   └── IRIS-PROVISIONING-RUNBOOK.md
├── configs/                    agent-specific configs
├── skills/                     custom skills
├── scripts/                    agent-local scripts
│
├── health-check.sh             iris liveness (referenced by orbit supervisor)
└── objectives.yaml             goals / KPIs
```

## Local-only paths (never on NFS, never in git)

```
/var/local/verdify/             operational state + planner reports (local SSD)
├── state/                      reached via SYMLINK /srv/verdify/state → here
│   ├── dispatch/               Iris↔Iris-dev Dispatch Protocol
│   ├── site-build.log          Quartz + nginx restart history
│   └── (cron output, ingestor aux state, backup logs)
└── reports/                    reached via SYMLINK /srv/verdify/reports → here

/srv/greenhouse/                ESPHome toolchain + venv (fast local disk)
└── esphome/
    ├── .venv/                  Python venv (ESPHome, aioesphomeapi, asyncpg, pytest)
    ├── greenhouse.yaml         → /srv/verdify/firmware/greenhouse.yaml (symlink)
    ├── greenhouse/             symlinks into firmware/greenhouse/
    ├── secrets.yaml            WiFi SSID/pw, API key, OTA pw — NEVER in git
    └── .esphome/build/         PlatformIO build cache (~78 MB, regenerable)

/var/lib/docker/volumes/        ALL Docker volumes (Docker engine owns these)
├── verdify_tsdb_data/          TimescaleDB data (~2 GB, grows)
├── verdify_grafana_data/       Grafana sqlite (user prefs — NOT source of truth)
├── verdify_mqtt_data/          Mosquitto persistence
└── verdify_promtail_positions/ log shipper cursor
```

## Compatibility symlinks

Every legacy hardcoded path still resolves through these. Do not add new code that depends on them; prefer the canonical target.

```
/srv/verdify                          → /mnt/iris/verdify
/srv/verdify/state                    → /var/local/verdify/state
/srv/verdify/reports                  → /var/local/verdify/reports
/srv/verdify/verdify-site/content     → /mnt/iris/verdify-vault/website
/srv/greenhouse/esphome/*.yaml        → /srv/verdify/firmware/...  (7 symlinks; resolve through /srv/verdify)
```

## Data flow at a glance

```
YOUR MAC                        THE VM                          THE WORLD
────────                        ──────                          ─────────

Obsidian edit
    │
    ▼
Syncthing (Mac↔NAS)
    │
    ▼
NAS /volume1/iris/verdify-vault/
    │
    ▼ (NFS)
/mnt/iris/verdify-vault/
    │
    ▼
verdify-site-poll.timer (every 10s)
    │ find -newer marker
    ▼
site-poll-and-rebuild.sh
    │
    ▼
rebuild-site.sh
    │ npx quartz build
    ▼
/srv/verdify/verdify-site/public/ (on NFS via symlink)
    │
    ▼ docker restart verdify-site
lab.verdify.ai ───────────────▶   visitors


VS Code edit                                 ESP32 ──aioesphomeapi──▶ ingestor
    │                                            │
    ▼ git push                                   ▼
github.com/jrvallery/verdify                 TimescaleDB (local SSD volume)
    │                                            │
    ▼ CI (ruff + esphome config)                 ▼
    │                                       /var/local/verdify/state/
    ▼ (no auto-deploy)                      (dispatch state + logs, local)
Manual on VM:
  systemctl restart <svc>
  docker compose up -d <svc>
  make firmware-deploy                Iris planner (Hermes) ──HTTP──▶ MCP (22 tools)
                                                                        │
                                                                        ▼
                                                            DB writes + ESP32 dispatches
```

## Quick lookup: "where is X?"

| I'm looking for… | Canonical path | Git repo |
|---|---|---|
| docker-compose.yml | `/mnt/iris/verdify/docker-compose.yml` | verdify |
| Makefile | `/mnt/iris/verdify/Makefile` | verdify |
| Python ingestor source | `/mnt/iris/verdify/ingestor/` | verdify |
| FastAPI app | `/mnt/iris/verdify/api/` | verdify |
| MCP server | `/mnt/iris/verdify/mcp/` | verdify |
| Firmware (ESPHome + C++) | `/mnt/iris/verdify/firmware/` | verdify |
| Firmware working dir (where ESPHome compiles) | `/srv/greenhouse/esphome/` (symlinks to firmware/) | untracked |
| Grafana dashboards (JSON, source of truth) | `/mnt/iris/verdify/grafana/provisioning/dashboards/json/` | verdify |
| DB migrations | `/mnt/iris/verdify/db/migrations/*.sql` | verdify |
| DB schema | `/mnt/iris/verdify/db/schema.sql` | verdify |
| TimescaleDB data itself | `/var/lib/docker/volumes/verdify_tsdb_data/` | untracked, **local SSD** |
| Planner prompts | `/mnt/iris/verdify/templates/*.j2` | verdify |
| Scripts (cron + manual) | `/mnt/iris/verdify/scripts/` | verdify |
| Test suite | `/mnt/iris/verdify/tests/` | verdify |
| systemd unit file copies | `/mnt/iris/verdify/systemd/` | verdify |
| Website source (Obsidian content) | `/mnt/iris/verdify-vault/website/` | verdify-vault |
| Website build output (HTML) | `/srv/verdify/verdify-site/public/` | untracked |
| Iris crop records | `/mnt/iris/verdify-vault/crops/` | verdify-vault |
| Iris daily notes | `/mnt/iris/verdify-vault/daily/` | verdify-vault |
| Agent config (CLAUDE.md, BACKLOG, etc.) | `/mnt/agents/iris/` | agents |
| Agent memory logs | `/mnt/agents/iris/memory/` | agents |
| Dispatch state (Iris↔Iris-dev) | `/var/local/verdify/state/dispatch/` | untracked, **local** |
| Cron + ingestor logs | `/var/local/verdify/state/*.log` | untracked, **local** |
| Site build log | `/var/local/verdify/state/site-build.log` | untracked |
| Secrets (.env, OTA password, API keys) | `/srv/verdify/.env`, `/srv/greenhouse/esphome/secrets.yaml`, `/mnt/agents/shared/credentials/` | **never in git** |
| Backup dumps | `/mnt/iris/backups/verdify-YYYYMMDD.dump` | untracked, NFS |
| Python venv | `/srv/greenhouse/.venv/` | untracked, **local SSD** |

## Where things are NOT (common confusions)

- `/srv/verdify/site/content/` — **deleted Sprint 16.4**. Was a divergent copy of the Quartz source.
- `/srv/verdify/verdify-site/content/` — NOT a real directory; symlink to vault website/.
- `/srv/verdify/provisioning/` — deprecated duplicate of grafana provisioning path.
- `/srv/verdify/state/` and `/srv/verdify/reports/` — real but only as symlinks to `/var/local/verdify/`.
- `/srv/verdify/logs/` — empty, reserved. Logs go to `/var/local/verdify/state/*.log` or systemd journal.
