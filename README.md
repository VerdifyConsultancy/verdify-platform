# Verdify

**A public AI-assisted greenhouse control loop.**

367 sq ft. Longmont, Colorado. 5,090 feet. 15% humidity. 95°F solar peaks. Mixed crops. One deterministic controller.

About 172 ESPHome entities feed an 8-state firmware controller that evaluates conditions every 5 seconds. Crop profiles define what each zone needs at each hour. Iris runs through Hermes with OpenAI GPT-5.5 high-reasoning as the sole planner gateway. It reads telemetry, forecasts, prior plans, scorecards, site context, and validated lessons through typed MCP tools before writing bounded tactical plans. The system measures outcomes, scores plans, and feeds validated lessons into future planning.

**[verdify.ai](https://verdify.ai)**

## Architecture

```
ESP32 Controller (8-state, greenhouse_logic.h, 5s loop)
  ├── aioesphomeapi ──→ Ingestor ──→ TimescaleDB (2.5M+ rows)
  ├── MQTT ──→ Mosquitto (state publishing + occupancy)
  └── HTTPS ──→ API (band-driven setpoints every 5 min)

TimescaleDB (telemetry, views, scorecards, lessons)
  ├── Grafana (public and private dashboards)
  ├── FastAPI (crop catalog + ESP32 setpoints)
  ├── MCP Server (typed tools for Iris)
  └── Quartz (static site with embedded panels)

Iris Planner (Hermes + GPT-5.5, MCP-only tool surface)
  └── Event-driven: sunrise, transitions, sunset, forecast, deviation
      → MCP tools → set_tunable() → Slack #greenhouse
```

The greenhouse control core runs on a single VM. Local GPU inference handles routine Iris reasoning; external APIs provide weather, optional heavyweight reasoning, and public delivery support. Real-time relay control stays local on the ESP32.

## Components

| Directory | What |
|-----------|------|
| `ingestor/` | Python async service — ESP32 data capture, 15 periodic tasks, entity routing |
| `api/` | FastAPI crop catalog + compatibility `/setpoints` export |
| `firmware/` | ESPHome YAML + C++ headers — 8-state climate controller (greenhouse_logic.h) |
| `mcp/` | FastMCP server — typed tools for Iris agent (climate, scorecard, set_tunable, etc.) |
| `scripts/` | Operational scripts — planner, vision analysis, forecast sync, monitoring |
| `provisioning/` | Grafana dashboard JSON + datasource config |
| `db/` | Schema, analytical views, functions, and migrations |
| `templates/` | Jinja2 planner prompt + reference docs |
| `config/` | AI model config, zone definitions |
| `tests/` | Smoke, drift, and integration tests |
| `site/` | Quartz static site source |

## Development

```bash
# Prerequisites: Docker, Python 3.13+, shared venv at /srv/greenhouse/.venv

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

**Tooling:** ruff (lint + format), pytest, pre-commit hooks, GitHub Actions CI.
**Config:** `pyproject.toml` is the single source of truth for deps, lint rules, and test config.

### Codex quickstart

Codex sessions should start with `AGENTS.md` (symlinked to `CLAUDE.md`) and
`README.md`. Historical handoffs, repo-cleanup inventories, retired backlogs,
and reusable prompt/context files live outside this repo in the local Orbit
vault at `/Users/jason/Orbit/context_dump/verdify-platform/`.

A safe orientation pass is: read `AGENTS.md`, `README.md`,
`docs/runbooks/laptop-operator.md`, `Makefile`, `pyproject.toml`,
`.github/workflows/ci.yml`, and the Orbit dump manifest if available; then
report branch/worktree state, access assumptions, current goal, safety gates,
and verification plan before editing.

## The Greenhouse

The control system has three layers:

1. **Crop target band** — diurnal VPD/temperature profiles for active crops, computed from `fn_band_setpoints()` every 5 minutes
2. **AI planner** — Iris reads live context (scorecard, forecast, lessons, sensors), plan memory, and static greenhouse documentation, then writes 72h tactical setpoint plans with performance targets
3. **ESP32 state machine** — 8 priority-ordered states evaluated every 5 seconds, enforcing the band with fans, heaters, misters, and fog

The crops set the targets. The AI tunes the tactics. The controller enforces them. The telemetry proves what happened.

## KPIs

**Planner Score (0–100):** 80% band compliance + 20% cost efficiency.
4 independent stress states tracked: heat, cold, VPD-high, VPD-low.
Dew point margin monitored for condensation risk.
Planner self-scores at every cycle and sets falsifiable performance targets.

## License

MIT
