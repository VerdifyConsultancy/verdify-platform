# Verdify

**A public AI-assisted greenhouse control loop.**

367 sq ft. Longmont, Colorado. 5,090 feet. 15% humidity. 95°F solar peaks. Mixed crops. One deterministic controller.

About 172 ESPHome entities feed a firmware controller that evaluates conditions
about once a second (a `dt_ms`-based loop invariant to tick rate — it was 5 s
historically). The current controller replan is firmware-first,
deterministic, crop-agnostic at the firmware layer, and AI-bounded: firmware
owns sensors, relays, local target curves, safety rails, disconnected behavior,
and climate/lighting/irrigation control; AI may tune bounded parameters over a
72-hour horizon but must not own hard safety limits, emergency behavior, or core
target calculation.

**[verdify.ai](https://verdify.ai)**

## Architecture

```
ESP32 Controller (greenhouse_logic.h, ~1s dt_ms-based loop)
  ├── aioesphomeapi ──→ Ingestor ──→ TimescaleDB (2.5M+ rows)
  ├── MQTT ──→ Mosquitto (state publishing + occupancy)
  └── local deterministic control surfaces (climate, lighting, irrigation)

TimescaleDB (telemetry, views, scorecards, lessons)
  ├── Grafana (public and private dashboards)
  ├── FastAPI (catalog/status + compatibility setpoint surfaces)
  ├── MCP Server (typed tools for Iris)
  └── Quartz (static site with embedded panels)

Planner (repo-selected pending profile: Hermes hermes-iris → GPT-5.6 Sol xhigh → MCP; live activation separately gated)
  └── Event-driven + scheduled (incl. weekly deep review) 72h horizon
      → MCP/tools → bounded tunables + decision ledger
```

The greenhouse control core runs in the single prod k3s environment, with
real-time relay control staying local on the ESP32. External APIs provide
weather, optional heavyweight reasoning, and public delivery support.

## Components

| Directory | What |
|-----------|------|
| `ingestor/` | Python async service — ESP32 data capture, 15 periodic tasks, entity routing |
| `api/` | FastAPI catalog/status surfaces + compatibility `/setpoints` export |
| `firmware/` | ESPHome YAML + C++ headers — 8-state climate controller (greenhouse_logic.h) |
| `mcp/` | FastMCP server — typed tools for Iris agent (climate, scorecard, set_tunable, etc.) |
| `scripts/` | Operational scripts — planner, vision analysis, forecast sync, monitoring |
| `provisioning/` | Grafana dashboard JSON + datasource config |
| `db/` | Schema, analytical views, functions, and migrations |
| `templates/` | Jinja2 planner prompt + reference docs |
| `config/` | AI model config, zone definitions |
| `tests/` | Smoke, drift, and integration tests |
| `site/` | Quartz static site source (serves prod lab.verdify.ai) |
| `site-astro/` | Astro lab-site replacement — staged at lab-stage.verdify.ai; prod cutover tracked in `docs/plans/lab-astro-migration.md` |

## Development

```bash
# Prerequisites: Docker, Python 3.12+ (3.13 preferred for parity with CI/runtime)

# Create/update repo-local tooling environment (.venv)
make setup

# Run all checks (lint + test + firmware compile)
make check

# Individual commands
make lint              # Ruff linter (0 errors)
make format            # Auto-format Python
make test              # 324 Python tests (~65s)
make firmware-check    # Compile ESP32 firmware
make planner-dry       # Render planner prompt (no API call)
make help              # List all targets
```

**Tooling:** ruff (lint + format), pytest, pre-commit hooks, and in-cluster
Argo Workflows/Kaniko CI. GitHub Actions publishing was removed; application
images publish to Zot and are promoted by validated digest pins.
**Config:** `pyproject.toml` is the single source of truth for deps, lint rules, and test config. `make setup` reads it directly; there is no checked-in duplicate requirements file for local tooling.

### Codex quickstart

Codex sessions should start with `AGENTS.md` (symlinked to `CLAUDE.md`) and
`README.md`. Historical handoffs, repo-cleanup inventories, retired backlogs,
and reusable prompt/context files live outside this repo in the local Orbit
vault at `/Users/jason/Orbit/context_dump/verdify-platform/`.

A safe orientation pass is: read `AGENTS.md`, `README.md`,
the root lane docs (`AGENT_LANE.md`, `PROJECT_BOARD.md`, `EPICS.md`,
`MILESTONES.md`, `SPRINTS.md`, `HISTORY.md`, `ARGOCD.md`,
`ACCESS_MATRIX.md`, GitHub issues),
`docs/runbooks/laptop-operator.md`, `Makefile`, `pyproject.toml`,
`.github/workflows/ci.yml`, and the Orbit dump manifest if available; then
report branch/worktree state, access assumptions, current goal, safety gates,
and verification plan before editing.

## The Greenhouse

The control system has three layers:

1. **Deterministic target band** — diurnal VPD/temperature curves tied to
   location, solar phase, season, configured min/max values, hysteresis, and
   mechanical limits.
2. **AI planner** — bounded 72-hour tactical tuning over firmware-supported
   parameters, with no authority over safety rails, emergency behavior, or core
   target calculation.
3. **ESP32 state machine** — local five-second control of climate, lighting,
   and irrigation under hard safety rails and disconnected fallback behavior.

Firmware enforces the deterministic local contract. AI tunes bounded tactics.
Telemetry, readbacks, dashboards, and the lab notebook prove what happened.

## KPIs

**Planner Score (0–100):** 80% band compliance + 20% cost efficiency.
4 independent stress states tracked: heat, cold, VPD-high, VPD-low.
Dew point margin monitored for condensation risk.
Planner self-scores at every cycle and sets falsifiable performance targets.

## License

MIT
