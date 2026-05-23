# Verdify Full Inventory And Cleanup Audit - 2026-05-23

Audit time: 2026-05-23 10:04 MDT / 2026-05-23 16:04 UTC.

Host: `vm-docker-iris`, Debian 13, kernel `6.12.88+deb13-amd64`, Docker 28, Python 3.13 shared venv at `/srv/greenhouse/.venv`.

This audit covers the Verdify platform repo, adjacent Verdify repos, GitHub state visible through `gh`, live Docker/systemd/crontab runtime, active ports, local Verdify-related paths, generated artifacts, and obvious dead/out-of-place state. It is an inventory and cleanup proposal, not a cleanup execution log.

## Executive Summary

Track A greenhouse control is healthy:

- ESP32 health sweep passed with firmware `2026.5.23.0933.0f3baa0`: `PASS 27`, `FAIL 0`, `WARN 0`.
- Core services are active: `verdify-ingestor`, `verdify-mcp`, `verdify-api`, `verdify-setpoint-server`.
- Public routes respond: `https://lab.verdify.ai`, `https://api.verdify.ai/health`, and `https://graphs.verdify.ai/api/health`.
- Live DB has `0` critical/high blocking alerts, `5` open warnings, and climate freshness around 30-50 seconds during the audit.
- Platform repo `main` is clean and pushed at `1a60063`.

The cleanup problem is not the controller. It is repo/process sprawl:

- `/mnt/iris/verdify-vault` has a large uncommitted generated-content batch.
- `/mnt/iris/verdify/verdify-site` is a nested Quartz fork repo with significant uncommitted code/theme changes.
- `/mnt/iris/planner` is clean but has one unpushed commit on `main`.
- `verdify-plan-publish.service` is failed from a `generate-daily-plan.py` DB timeout.
- Host `logrotate.service` is also failed; no journal details were visible to the audit user.
- The platform GitHub repo has branch protection but no required checks, and GitHub Actions reports `0` workflow runs even though `.github/workflows/ci.yml` has `push` and `pull_request` triggers.
- The root disk is tight: `/` is `88%` used, memory pressure is high, swap is effectively full, Docker has 113 dangling volumes and 57 dangling images, and old local archives consume multiple GB.
- Runtime docs are stale: `docs/SYSTEM-ARCHITECTURE.md` says 7 containers and 2 systemd services; production currently has 14 containers, 13 running, plus multiple active systemd Verdify services and timers.

## Evidence Commands

Representative commands used:

- `git status --short --branch`, `git worktree list --porcelain`, `git for-each-ref`
- `gh repo view`, `gh pr list`, `gh issue list`, `gh api .../actions/runs`, `gh api .../branches`
- `systemctl list-units`, `systemctl status`, `systemctl cat`, `journalctl -u`
- `docker ps -a`, `docker compose ps`, `docker compose images`, `docker volume inspect`, `docker system df`
- `ss -tulpn`, `crontab -l`, `tmux list-sessions`
- `find`, `du -sh`, `rg`
- `curl` health checks and direct TimescaleDB `psql` health queries

## Platform Repository

Primary repo:

- Path: `/mnt/iris/verdify`
- Service symlink: `/srv/verdify -> /mnt/iris/verdify`
- Remote: `https://github.com/VerdifyConsultancy/verdify-platform.git`
- Current commit: `1a600635fe1d86919731b6be9f2a420630628f77`
- Current subject: `Add daily lifecycle artifact exporter`
- GitHub `origin/main`: same SHA
- Status: clean before this report was written
- Tracked files: 823
- Tracked size: about 41 MB
- Working tree size: about 2.8 GB because ignored runtime/build/generated state lives under the repo
- Git object note: `git count-objects -vH` reported `garbage found: /mnt/iris/verdify/.git/worktrees/firmware/refs`

Tracked top-level inventory:

| Area | Tracked file count | Notes |
|---|---:|---|
| `site/` | 285 | Platform-side site source/config, separate from live `verdify-site` Quartz checkout |
| `db/` | 147 | Schema plus 145 tracked migration files |
| `scripts/` | 91 | Operational scripts, generators, audits, deployment helpers |
| `grafana/` | 82 | Provisioned dashboards, datasource config, custom nginx/CSS |
| `docs/` | 62 | Architecture, audits, backlog, agent docs, runbooks |
| `verdify_schemas/` | 44 | Shared typed contracts and tests |
| `tests/` | 23 | Python, DB, fidelity, drift, smoke tests |
| `firmware/` | 20 | ESPHome YAML, C++ controller, replay tests |
| `systemd/` | 14 | Tracked Verdify units, not including two extra installed forecast units |
| `ingestor/` | 14 | Main greenhouse ingestor and task modules |

Ignored platform runtime/build state found under `/mnt/iris/verdify`:

| Path | Size | Purpose / disposition |
|---|---:|---|
| `firmware/` | 1.1 GB | Firmware source plus ignored ESPHome build and artifacts |
| `firmware/artifacts/` | 868 MB | OTA archive, contains production rollback evidence; needs retention policy |
| `firmware/.esphome/` | 181 MB | ESPHome build cache; reproducible, removable when not compiling |
| `verdify-site/` | 937 MB | Nested Quartz site checkout/build, dirty; see separate section |
| `traefik/logs/` | 152 MB | Reverse-proxy logs; rotate or cap |
| `.git/` | 126 MB | Normal plus one garbage warning |
| `.ruff_cache`, `.pytest_cache` | < 1 MB | Safe to clean |

Filesystem junk:

- `@eaDir` directories exist under the platform tree, including `.git/@eaDir`, `db/@eaDir`, `docs/@eaDir`, `firmware/@eaDir`, `grafana/@eaDir`, `site/@eaDir`, `traefik/@eaDir`, `verdify-site/@eaDir`, and `verdify_schemas/@eaDir`.
- `.gitignore` already ignores `@eaDir/` and `**/@eaDir/`, but the existing directories still add noise and one is inside `.git`.

## Worktrees And Branches

All platform worktrees inspected were clean and at `1a60063`.

| Worktree | Branch | Upstream | Status |
|---|---|---|---|
| `/mnt/iris/verdify` | `main` | `origin/main` | clean, current |
| `/mnt/iris/verdify-worktrees/firmware` | `verdify-firmware` | `origin/main` | clean, current |
| `/mnt/iris/verdify-worktrees/ingestor` | `verdify-ingestor` | `origin/main` | clean, current |
| `/mnt/iris/verdify-worktrees/genai` | `verdify-genai` | none | clean, current |
| `/mnt/iris/verdify-worktrees/web` | `verdify-web` | `origin/main` | clean, current |
| `/mnt/iris/verdify-worktrees/web-codex` | `verdify-web-codex` | `origin/main` | clean, current |
| `/mnt/iris/verdify-worktrees/saas` | `verdify-saas` | `origin/main` | clean, current |
| `/mnt/iris/verdify-worktrees/lifecycle-artifact` | `coordinator/daily-lifecycle-artifact` | `origin/main` | clean, current, now redundant |

Local branches:

- `coordinator/daily-lifecycle-artifact` is now identical to `origin/main`; retire the branch and remove the worktree.
- `verdify-web-codex` duplicates `verdify-web` as a parallel Codex worktree; keep only if there is a deliberate two-agent web workflow.
- `verdify-genai` has no upstream configured, unlike most persistent branches.
- `coordinator/lighting-occupancy-task-demand` exists locally and tracks `origin/coordinator/lighting-occupancy-task-demand` at `19886ea`.

Remote platform branches:

| Branch | SHA | Status |
|---|---|---|
| `main` | `1a60063` | protected, current |
| `coordinator/forecast-deviation-alert-envelope` | `f4fe34a` | no open PR; likely stale |
| `coordinator/lighting-occupancy-task-demand` | `19886ea` | no open PR; likely stale |

Recommended branch cleanup:

1. Delete remote coordinator branches after exporting `git diff --name-status main...branch` to the cleanup report or issue.
2. Remove the now-redundant local `coordinator/daily-lifecycle-artifact` branch/worktree.
3. Decide whether `web-codex` remains an intentional persistent worktree.
4. Configure upstreams consistently for persistent branches, especially `verdify-genai`.

## GitHub State

### `VerdifyConsultancy/verdify-platform`

- Visibility: public
- Default branch: `main`
- Open PRs: none
- Open issues: none
- Branches: `main`, `coordinator/forecast-deviation-alert-envelope`, `coordinator/lighting-occupancy-task-demand`
- Branch protection exists on `main`, but:
  - required status checks: none
  - required PR reviews: none
  - admin enforcement: false
- Workflow definitions: one active workflow, `CI` at `.github/workflows/ci.yml`
- GitHub Actions runs API returned total count `0`
- Combined commit status for `1a60063`: no statuses

Finding: the repo has a CI workflow file, but GitHub currently reports no Actions runs. This should be treated as a release-process gap until proven intentional.

### `VerdifyConsultancy/verdify-vault`

- Visibility: private
- Default branch: `main`
- Path on VM: `/mnt/iris/verdify-vault`
- Open PRs: none
- Open issues: none
- Actions runs API returned total count `0`
- Local status: dirty with a large generated-content batch

### `VerdifyConsultancy/verdify-planner`

- Visibility: private
- Default branch: `main`
- Path on VM: `/mnt/iris/planner`
- Open PRs: none
- Open issues: none
- Actions: one recent successful run in GitHub, but local repo is ahead of origin by one commit
- Local unpushed commit: `080a4b1 Add planner-owned memory storage and ingestion`
- Scope of unpushed commit: 20 files, about 2,199 insertions and 75 deletions, including planner memory storage, migrations, API/runtime changes, and tests

Finding: this is real in-flight product code. It should be published through a PR or deliberately reverted/archived; it should not remain only on the VM.

### Quartz Site Repo

Nested checkout:

- Path: `/mnt/iris/verdify/verdify-site`
- Branch: `v4`
- Remote configured: `https://github.com/jvallery/verdify-site.git`
- `gh repo view` resolves this to `VerdifyConsultancy/verdify-site-legacy`
- Open PRs: two Dependabot PRs
- Local status: dirty with Quartz source/theme/icon changes plus `quartz/static/brand/`
- Diff: 20 tracked files changed, 1,047 insertions, 772 deletions, plus untracked brand assets

Finding: this is deployed-site code, not disposable build output. It is ignored by the platform repo and currently uncommitted in its own repo. It needs a dedicated finish-or-abandon decision.

## Live Docker Runtime

All containers from `docker ps -a`:

| Container | Image | Status | Port exposure |
|---|---|---|---|
| `verdify-api` | local `verdify-api` | Up 14h | internal via Traefik, app port 8080 |
| `verdify-site` | `nginx:alpine` | Up 39h | internal port 80 |
| `verdify-grafana` | `grafana/grafana-oss:latest` | Up 18h | internal port 3000 |
| `verdify-grafana-proxy` | `nginx:alpine` | Up 6d | internal port 80 |
| `verdify-traefik` | `traefik:v3.6.7` | Up 6d, healthy | public `0.0.0.0:443` |
| `verdify-mqtt` | `eclipse-mosquitto:2` | Up 6d | public/local `0.0.0.0:1883` |
| `verdify-goaccess` | `allinurl/goaccess:latest` | Exited 5d ago, code 137 | none |
| `verdify-promtail` | `grafana/promtail:latest` | Up 6d | none |
| `hermes-iris` | `nousresearch/hermes-agent` | Up 6d | `127.0.0.1:8642` |
| `verdify-timescaledb` | `timescale/timescaledb:latest-pg16` | Up 6d, healthy | `127.0.0.1:5432` |
| `verdify-goaccess-site` | `nginx:alpine` | Up 6d | internal port 80 |
| `verdify-umami` | `ghcr.io/umami-software/umami:postgresql-latest` | Up 6d | internal port 3000 |
| `verdify-umami-db` | `postgres:16-alpine` | Up 6d, healthy | internal port 5432 |
| `verdify-grafana-renderer` | `grafana/grafana-image-renderer:latest` | Up 6d, healthy | internal port 8081 |

Docker networks:

- `verdify-internal`: active, contains DB, API, Grafana, renderer, MQTT, Umami, Hermes.
- `verdify-proxy`: active, contains Traefik and public-routed services.
- `verdify_default`: exists with no attached containers; likely stale.

Docker volumes:

| Volume | Size | Purpose |
|---|---:|---|
| `verdify_tsdb_data` | 2.0 GB | TimescaleDB data |
| `verdify_grafana_data` | 1.7 GB | Grafana persisted state |
| `verdify_umami_db_data` | 70 MB | Umami database |
| `verdify_mqtt_data` | 80 KB | Mosquitto persistence |
| `verdify_promtail_positions` | 16 KB | Promtail positions |

Docker cleanup pressure:

- `docker system df`: 69 images, 14 containers, 119 local volumes, 87 build-cache entries.
- Reclaimable: 5.575 GB across local volumes and about 716 MB across images.
- Dangling volumes: 113.
- Dangling images: 57, mostly untagged 154-162 MB local `verdify-api` builds.

Recommended Docker cleanup:

1. Stop/remove `verdify-goaccess` if GoAccess is intentionally retired, or restart/document it if still needed.
2. Remove empty `verdify_default` network if no owner remains.
3. Prune dangling images after confirming no rollback image is needed.
4. Inspect/prune dangling volumes in a staged pass; never prune named Verdify volumes without a backup.

## Systemd Runtime

Verdify-related units found:

| Unit | State | Notes |
|---|---|---|
| `verdify-ingestor.service` | active | ESP32 ingestor, started 2026-05-23 09:10 MDT |
| `verdify-mcp.service` | active | MCP server on port 8000, with bind drop-in |
| `verdify-api.service` | active | host FastAPI on port 8300 |
| `verdify-setpoint-server.service` | active | compatibility setpoint/light helper on port 8200 |
| `verdify-plan-publish.path` | active | watches `/var/local/verdify/state/plan-publish-trigger` |
| `verdify-plan-publish.service` | failed | publish job timed out in `generate-daily-plan.py` |
| `verdify-forecast-page.timer` | active | runs `publish-site-content.sh --reason forecast` every 30 min |
| `verdify-grafana-render-cache-warm.timer` | active | warms Grafana render cache every 30 min |
| `verdify-site-poll.timer` | active | polls vault and rebuilds site every 10 sec |
| `verdify-forecast.service` | disabled | installed but not tracked in repo |
| `verdify-forecast.timer` | disabled | installed but not tracked in repo |
| `verdify-timescaledb.service` | not found | referenced by some `After=` dependencies, but DB is Docker-managed |

Installed unit drift:

- Tracked units match `/etc/systemd/system` for all tracked `verdify-*` service/timer/path files.
- Extra installed units not in repo: `verdify-forecast.service`, `verdify-forecast.timer`.
- Drop-ins exist for `verdify-ingestor`, `verdify-mcp`, and `verdify-setpoint-server`; these are not represented in the repo's `systemd/` directory.

Failed unit detail:

- `verdify-plan-publish.service` failed at 2026-05-23 05:43 MDT.
- Failure: `generate-daily-plan.py` timed out after 15 seconds running a `daily_summary` stress-context query through `docker exec verdify-timescaledb psql`.
- Consequence: plan publishing can fail while core greenhouse control remains healthy.

Recommended systemd cleanup:

1. Fix or raise/query-optimize the `generate-daily-plan.py` timeout path, then clear/reset the failed unit.
2. Decide whether `verdify-forecast.*` is retired; if retired, remove the installed units.
3. Track or document systemd drop-ins.
4. Update `docs/SYSTEM-ARCHITECTURE.md` and `docs/RUNBOOK.md` to match actual containers and units.

Host failed-unit note:

- `logrotate.service` is also failed from 2026-05-23 00:06 MDT. `systemctl status` shows exit status 1; `journalctl -u logrotate.service` returned no visible entries for the audit user. This is not Verdify-specific, but it matters because Traefik and service logs are already part of the storage-pressure picture.

## Ports And Public Routes

Listening ports found:

| Port | Bind | Owner / purpose |
|---:|---|---|
| 443 | `0.0.0.0`, `::` | Traefik public HTTPS |
| 1883 | `0.0.0.0`, `::` | Mosquitto MQTT |
| 5432 | `127.0.0.1` | TimescaleDB Docker publish |
| 8642 | `127.0.0.1` | Hermes gateway |
| 8000 | `0.0.0.0` | `verdify-mcp` |
| 8200 | `0.0.0.0` | setpoint server |
| 8300 | `0.0.0.0` | host FastAPI service |

Health checks during audit:

- `http://127.0.0.1:8300/health`: OK.
- Docker `verdify-api` internal `http://127.0.0.1:8080/health`: OK from inside the container.
- `http://127.0.0.1:8200/health`: OK.
- Docker Grafana `/api/health`: OK.
- `https://lab.verdify.ai`: HTTP 200.
- `https://api.verdify.ai/health`: OK.
- `https://graphs.verdify.ai/api/health`: HTTP 200.

Note: both Docker `verdify-api` and host `verdify-api.service` run the same FastAPI code on different ports. This may be intentional public-vs-local split, but docs conflict: `api/main.py` says 8300 internal, while Docker uses 8080 through Traefik. The cleanup sprint should document or simplify this.

## Crontab And Scheduled Jobs

User crontab contains:

| Schedule | Script / command | Purpose |
|---|---|---|
| `0 1 * * *` | `pg_dump` from `verdify-timescaledb` | DB backup to `/mnt/iris/backups` |
| `5 0 * * *` | `daily-summary-snapshot.py` | daily summary finalization |
| `10 0 * * *` | `vault-daily-writer.py` | vault daily summary |
| `15 0 * * *` | `vault-crop-writer.py` | vault crop records |
| `15 0 * * *` | `generate-hydro-map.py` | hydro map |
| `0 12,16,20,0 * * *` | `frigate-snapshot.py` | camera snapshots |
| `0 13 * * *` | `checklist-to-slack.sh` | daily checklist |
| `* * * * *` | `verdify-metrics.py` | Prometheus metrics |
| `0 */6 * * *` | `slack-channel-archive.py` | Slack archive |
| `0 7 * * *`, `15 20 * * *`, `30 0 * * *` | `publish-daily-plan.sh` | plan publishing |

Finding: the crontab is an active operational surface but is not tracked as a deploy artifact in the platform repo. It should be captured under `systemd/` or `docs/runbooks/` with an install/update procedure, or migrated to timers.

## Local Paths And Artifacts

Major Verdify-related paths:

| Path | Size | Status / purpose |
|---|---:|---|
| `/srv/verdify` | symlink | points to `/mnt/iris/verdify` |
| `/mnt/iris/verdify` | 2.8 GB | platform repo + ignored runtime state |
| `/mnt/iris/verdify-worktrees` | 475 MB | persistent agent worktrees |
| `/mnt/iris/verdify-vault` | 996 MB | private content/vault repo, dirty |
| `/mnt/iris/planner` | 113 MB | private planner repo, clean but ahead one commit |
| `/mnt/iris/backups` | 3.4 GB | 53 daily DB dumps from 2026-03-31 to 2026-05-23 |
| `/srv/greenhouse` | 1.1 GB | shared Python venv and ESPHome symlink/archive |
| `/srv/verdify.old` | 645 MB | old platform copy; cleanup candidate |
| `/srv/greenhouse/esphome.symlink-archive-20260427-200114` | 207 MB | old ESPHome symlink archive; cleanup candidate |
| `/var/local/verdify` | 12 MB | live runtime state/export/log path |
| `/var/lib/verdify/hermes` | tiny | Hermes data root; actual data mounted under container path |
| `/mnt/agents` | large / slow to size | agent launchers, configs, archived launchers, credentials, agent memories |
| `/home/jason/.claude-agents` | agent state | Claude persistent memories |
| `/home/jason/.codex-agents` | agent state | Codex persistent memories and plugins |

Firmware artifact inventory:

- Artifact dirs: 25.
- Dirty artifact dirs: 12, e.g. `2026.5.11.1406.c9b842b.dirty` through `2026.5.18.1802.ff57211.dirty`.
- Current deployed artifact: `/mnt/iris/verdify/firmware/artifacts/2026.5.23.0933.0f3baa0`.
- `firmware/artifacts/last-good.ota.bin` remains the rollback/bake marker and must not be pruned casually.

Backup inventory:

- `/mnt/iris/backups` has 53 dumps.
- Oldest: `verdify-20260331.dump`.
- Newest: `verdify-20260523.dump`.
- Total bytes from listed dumps: about 3.6 GB.

Recommended path cleanup:

1. Archive then delete `/srv/verdify.old` if no unique diff remains.
2. Archive then delete old ESPHome symlink archive if no rollback use remains.
3. Define firmware artifact retention: keep latest deployed, last-good, last N clean builds, and explicit incident artifacts; move `.dirty` builds to cold archive or delete after hash manifest.
4. Add DB backup retention or compression policy.
5. Remove `@eaDir` trees from repo/worktree paths, including inside `.git`, after stopping Synology metadata regeneration if possible.

## Active Agent And Planner Processes

Host process inventory at audit time:

- Total `ps` rows including header: 394.
- User distribution: root 214, `jason` 82, Postgres UID `70` 30, `_rpc` 26, `james` 18, container UIDs `65532` 12 and `1001` 5, plus single-service users.
- Largest command families: `sshd-session` 30, `postgres` 30, `nginx` 28, `node` 21, `tmux: client` 14, `containerd-shim` 13, `codex` 9, `chromium` 8, `docker-proxy` 6, `claude` 6, `bash` 6.
- Non-Verdify system families observed: systemd, dbus, rpcbind, qemu guest agent, kernel workers, sshd, unattended-upgrades, node exporter support tasks, NFS/RPC workers.

Relevant host processes:

- `verdify-ingestor`: `/srv/greenhouse/.venv/bin/python ingestor.py`
- `verdify-mcp`: `/srv/greenhouse/.venv/bin/python mcp/server.py`
- host `verdify-api`: `/srv/greenhouse/.venv/bin/uvicorn main:app --port 8300`
- `verdify-setpoint-server`: `/srv/greenhouse/.venv/bin/python3 /srv/verdify/scripts/setpoint-server.py`
- `verdify-grafana-render-cache-warm`: `/srv/greenhouse/.venv/bin/python /srv/verdify/scripts/warm-grafana-render-cache.py ...` when the timer is running.
- Dockerized Hermes gateway, Grafana, TimescaleDB, Mosquitto, Promtail, etc.
- Multiple tmux agent sessions are live: Claude and Codex variants for firmware/genai/ingestor/web/saas, plus `iris`, `iris-planner`, `iris-planner-local`, `iris-dev`, and `verdify-labs-planner`.

Agent config/path findings:

- `/mnt/agents/_archived/stale-launchers-20260522` exists and is appropriately archived.
- `/mnt/agents/bin/@eaDir/*@SynoEAStream` exists and should be cleaned with the rest of Synology metadata.
- `/home/jason/.claude-agents/iris-dev-slot-*` memories remain from the old slot model.
- Current Claude/Codex agent directories exist for all Verdify lanes.

Recommended agent cleanup:

1. Decide whether paired Claude+Codex sessions per lane are intentional.
2. Keep current memories, but archive/remove old `iris-dev-slot-*` agent state once no reference is needed.
3. Remove Synology `@eaDir` metadata from `/mnt/agents`.

## Database State

Live TimescaleDB:

- PostgreSQL: 16.11 on Alpine.
- Public schema: 194 tables, 122 views.
- Migration files in platform repo: 145 tracked SQL files, latest `138-effective-control-diagnostics-and-plan-view.sql`.
- No `schema_migrations` table exists, so migration application is not tracked in-db by a standard ledger.
- `db/init` is mounted into the DB container for initialization only; it is not a live migration ledger.

Finding: migration state is inferred from live schema/views/tests rather than a durable migration ledger. That is workable for a single-site VM but fragile as cleanup and SaaS work resume.

## Uncommitted, Unpushed, Out-Of-Place, Or Dead State

### Must Decide / High Priority

1. `verdify-plan-publish.service` is failed.
   - Impact: generated site/plan publishing can silently miss updates.
   - Evidence: timeout in `generate-daily-plan.py::get_stress_context`.

2. `/mnt/iris/verdify-vault` is dirty.
   - Impact: public content/source of generated site truth is not reproducible from GitHub.
   - Evidence: 97 modified tracked files plus many untracked daily/snapshot/plan/image files.

3. `/mnt/iris/verdify/verdify-site` is dirty.
   - Impact: deployed Quartz theme/source changes are not in its repo.
   - Evidence: 20 tracked files changed plus untracked `quartz/static/brand/`.

4. `/mnt/iris/planner` has unpushed code.
   - Impact: planner memory implementation exists only on the VM.
   - Evidence: local `main` ahead of `origin/main` by commit `080a4b1`.

5. GitHub Actions is not proving platform `main`.
   - Impact: push-to-main can bypass the expected CI net.
   - Evidence: active workflow file, but Actions runs total `0` and no commit statuses.

6. Root disk pressure.
   - Impact: production VM has limited headroom.
   - Evidence: `/` is `88%` used, swap is full, Docker has 113 dangling volumes and 57 dangling images.

### Cleanup Candidates / Medium Priority

| Item | Why it is suspicious | Proposed action |
|---|---|---|
| Remote platform branches `coordinator/*` | no open PRs, no longer active in platform main | export diff then delete |
| Worktree `lifecycle-artifact` | branch now equals main | remove worktree and branch |
| Worktree `web-codex` | duplicate web lane | keep only if explicit agent need |
| `verdify_default` Docker network | no attached containers | remove after compose check |
| `verdify-goaccess` container | exited 5 days, code 137 | restart/document or remove |
| `verdify-forecast.*` systemd units | installed, disabled, not tracked | add to repo or remove |
| `/srv/verdify.old` | 645 MB old copy | archive/delete after diff check |
| ESPHome symlink archive | 207 MB old archive | archive/delete after rollback review |
| `.dirty` firmware artifacts | 12 dirty build archives | move to cold archive or prune |
| `@eaDir` directories | Synology metadata in repos/agent dirs | remove and prevent regeneration |
| `docs/SYSTEM-ARCHITECTURE.md` | stale container/service counts | update from actual inventory |
| `docs/RUNBOOK.md` | stale service inventory and old local-first language | refresh as incident-only runbook |
| `docs/FOLDER-HIERARCHY.md` | already flagged stale | update with agent/worktree split |

### Scripts Needing Ownership Review

The following scripts had zero non-test/non-doc operational references in a simple text-reference scan. Some are known manual or cron tools, so this is not a deletion list. It is an ownership/documentation queue.

- `scripts/backfill-nexus-infra-metrics.py`
- `scripts/backfill-plan-evaluations.py`
- `scripts/check-replan-trigger.sh`
- `scripts/checklist-summary.py`
- `scripts/checklist-to-slack.sh`
- `scripts/compute-grow-light-daily.py`
- `scripts/crop-parser.py`
- `scripts/export-daily-lifecycle-artifact.py`
- `scripts/export-public-sample-dataset.sh`
- `scripts/export-replay-data.sh`
- `scripts/generate-baseline-vs-iris-page.py`
- `scripts/generate-checklist.py`
- `scripts/generate-hydro-map.py`
- `scripts/generate-plans-index.sh`
- `scripts/hermes-trigger.py`
- `scripts/hermes-validation-monitor.py`
- `scripts/liveness-check.sh`
- `scripts/populate-site-content.py`
- `scripts/render-grafana-embed-audit.py`
- `scripts/shelly-sync.py`
- `scripts/slack-channel-archive.py`
- `scripts/standardize-dashboards.py`
- `scripts/transcode-launch-video.sh`
- `scripts/validate-plan-coverage.sh`
- `scripts/vault-harvest-writer.py`
- `scripts/vault-operations-writer.py`
- `scripts/vault-treatment-writer.py`
- `scripts/verdify-metrics.py`

Recommended rule: every script should have one of:

- systemd/timer/cron owner,
- Makefile target,
- runbook reference,
- test/reference proving it is a maintained manual tool,
- or deletion/archive.

## Proposed Cleanup Sprint

Name: `coordinator/cleanup-inventory-2026-05-23`

Principle: no firmware OTA, no schema migration, no behavior change to Track A control unless a production fault requires it. This sprint is cleanup, reproducibility, and documentation truth.

### C-CLEAN-0: Freeze And Safety Gate

Acceptance:

- Confirm `make sensor-health` passes before and after cleanup.
- Confirm `0` open critical/high alerts.
- Confirm no firmware files are changed unless a separate firmware PR is opened.
- Confirm no Docker named production volume is deleted.

### C-CLEAN-1: Publish Or Abandon Non-Platform Repo Work

Scope:

- `/mnt/iris/planner`
- `/mnt/iris/verdify/verdify-site`
- `/mnt/iris/verdify-vault`

Acceptance:

- Planner commit `080a4b1` is pushed to a PR or explicitly archived/reverted.
- Quartz `verdify-site` dirty changes are committed/pushed or reverted with a written decision.
- Vault generated batch is committed/pushed or split into a deliberate archive/ignore decision.
- All three repos end clean or have a documented owner and blocker.

### C-CLEAN-2: Fix Site Publishing Reliability

Scope:

- `verdify-plan-publish.service`
- `scripts/generate-daily-plan.py`
- `scripts/publish-site-content.sh`

Acceptance:

- Failed unit is diagnosed and fixed.
- `systemctl reset-failed verdify-plan-publish.service` leaves no failed Verdify units.
- Manual `systemctl start verdify-plan-publish.service` or equivalent dry-run succeeds.
- Timeout-sensitive DB query path has either a faster query, longer timeout, or cached data strategy.

### C-CLEAN-3: GitHub And Branch Hygiene

Scope:

- `VerdifyConsultancy/verdify-platform`
- stale remote/local branches
- Actions/branch protection

Acceptance:

- Explain why platform Actions has zero runs or fix it.
- Require at least the intended status checks on `main`, or document why direct-push operation remains allowed.
- Delete or explicitly keep remote coordinator branches.
- Remove redundant local worktrees/branches: at minimum `lifecycle-artifact`; decide `web-codex`.
- Fix persistent branch upstreams.

### C-CLEAN-4: Runtime Unit And Container Hygiene

Scope:

- systemd units/drop-ins/crontab
- Docker containers/networks/images/volumes

Acceptance:

- Extra installed `verdify-forecast.*` units are tracked or removed.
- Drop-ins are tracked or documented.
- Crontab is captured in repo docs or migrated to timers.
- `verdify-goaccess` is removed or restored intentionally.
- Empty `verdify_default` network removed if safe.
- Dangling Docker image cleanup performed.
- Dangling volume cleanup plan executed only after named-volume backup confirmation.

### C-CLEAN-5: Storage Retention Policy

Scope:

- firmware artifacts
- DB backups
- `/srv/verdify.old`
- ESPHome archive
- Traefik logs
- Docker unused state

Acceptance:

- Written retention policy committed.
- Old `.dirty` firmware artifacts archived/deleted according to policy.
- `/srv/verdify.old` removed or archived after a diff/export.
- ESPHome symlink archive removed or archived.
- DB backups keep a defined rolling window plus monthly checkpoints.
- Root disk usage drops below 80% or the remaining pressure is explained.

### C-CLEAN-6: Documentation Truth Pass

Scope:

- `README.md`
- `docs/SYSTEM-ARCHITECTURE.md`
- `docs/RUNBOOK.md`
- `docs/FOLDER-HIERARCHY.md`
- relevant agent docs

Acceptance:

- Container count, systemd count, ports, paths, repo split, and planner gateway state match the live audit.
- Runbook distinguishes Track A control from site/publication failures.
- API split (Docker 8080 public vs systemd 8300 host-local) is either documented or simplified.
- Migration-state policy is documented, including the absence of a `schema_migrations` table.

### C-CLEAN-7: Script Ownership Sweep

Scope:

- all `scripts/`
- Makefile targets
- cron/systemd/runbook references

Acceptance:

- Every script has an owner and invocation path, or is archived/deleted.
- Manual-only scripts have a short usage note.
- Cron/systemd-called scripts are represented in source-controlled deployment docs.

## Recommended Order

1. Fix `verdify-plan-publish.service`; it is the only actively failed Verdify unit.
2. Publish/resolve `/mnt/iris/planner`, `/mnt/iris/verdify/verdify-site`, and `/mnt/iris/verdify-vault` dirty/unpushed state.
3. Restore/verify GitHub Actions and branch protection.
4. Clean branches/worktrees.
5. Clean Docker/runtime/storage with backups and retention rules.
6. Refresh architecture/runbook/folder docs.
7. Sweep scripts after the runtime/source-of-truth picture is stable.

## Completion Definition For The Cleanup Sprint

The sprint is complete when:

- Platform, planner, vault, and Quartz repos are clean or each has a documented intentional dirty state with owner/blocker.
- `systemctl --failed` has no Verdify units.
- Docker has no unintended exited Verdify containers and no empty Verdify networks.
- Root filesystem has a documented space-recovery result.
- GitHub branch/Actions state matches the intended process.
- `make lint`, `make test`, and relevant live health checks pass.
- The inventory docs match the live deployment closely enough that a new agent can locate code, data, services, credentials references, logs, and generated artifacts without chat history.
