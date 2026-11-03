#!/usr/bin/env python3
"""planner-dry — render every planner event-type prompt locally, without any
API calls, and assert that invariants the audit cares about still hold. Run
via `make planner-dry`. Fails loud (exit 1) if an invariant breaks so prompt
regressions can't land silently.

Checks:
  1. iris_planner imports (fails on Python syntax, missing dependency, or
     missing playbook at PLANNER_PLAYBOOK_PATH).
  2. `_STANDING_DIRECTIVES` still says "33 production tools" — Hermes tool-inventory guard.
  3. `_PLANNER_CORE` still carries the "Structured hypothesis" section —
     G7 write-side guard (CORE must include it so both opus and local see it).
  4. Sprint-3 split invariants: `_PLANNER_CORE` has the Tier 1 table;
     `_PLANNER_EXTENDED` has the validated-lessons list and NOT the Tier 1
     table (prevents duplication).
  5. Every event-type prompt builder renders a non-empty string for BOTH
     instances (opus, local). Assertions:
      - opus and local audit labels render the same Hermes/GPT-5.6 Luna prompt.
      - preamble ≤ ~52000 Claude tokens (≈ 208000 chars at 4:1; we
         approximate with char-length since the anthropic tokenizer hits
         the network and this script must stay offline).
      - "33 production tools" preamble appears in both.
  6. The context each builder gets holds section 11 (`render_context()` over
     tests/fixtures/irrigation_context.json), within its 60-line budget, and the
     MANUAL SETTINGS section, rendered from its block in scripts/gather-plan-context.sh
     under a fake `psql` that returns two fixture rows; both reach every prompt.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HERMES_URL", "http://127.0.0.1:8642")
os.environ.setdefault("HERMES_IRIS_API_KEY", "x")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ingestor"))

import iris_planner as p  # noqa: E402

# Keep the static Hermes preamble bounded. Claude tokenizer is the proxy;
# target ≤52k tokens = ~208k chars (4:1 approximation).
PREAMBLE_MAX_CHARS = 52_000 * 4

# Invariants that must hold regardless of instance.
assert "33 production tools" in p._STANDING_DIRECTIVES, "tool-count guard: '33 production tools' missing"
assert "`query` tool" in p._STANDING_DIRECTIVES, "Hermes prompt must document that raw SQL is denied"
assert "operator `plan_run` tool" in p._STANDING_DIRECTIVES, "Hermes prompt must document that plan_run is denied"
assert p.PLANNER_PLAYBOOK_PATH.exists(), f"playbook missing at {p.PLANNER_PLAYBOOK_PATH}"

# Sprint-3 split + Phase-1d prompt slimming: CORE must have the hypothesis
# format AND the tactical Tier 1 dictionary with read-only crop bands. The full
# registry lives in verdify_schemas.tunable_registry / docs/tunable-cascade.md;
# keeping every tunable literal in CORE caused prompt bloat and stale guidance.
assert "Structured hypothesis" in p._PLANNER_CORE, (
    "structured-hypothesis guidance (G7) missing from _PLANNER_CORE — must ship to both instances"
)
assert "Tunable Dictionary — Tactical Tier 1" in p._PLANNER_CORE, "Tier 1 dictionary header missing from _PLANNER_CORE"
assert "docs/tunable-cascade.md" in p._PLANNER_CORE, "non-policy reference to full cascade doc missing"
# Drift guard: every Tier 1 planner-pushable name must appear literally in CORE.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from verdify_schemas.tunable_registry import REGISTRY  # noqa: E402

_tier1 = {name for name, definition in REGISTRY.items() if definition.planner_pushable and definition.tier == 1}
_missing_tunables = sorted(t for t in _tier1 if t not in p._PLANNER_CORE)
assert not _missing_tunables, (
    f"CORE Tier-1 dictionary missing {len(_missing_tunables)} params: {_missing_tunables[:10]}"
)
assert "Validated Lessons" in p._PLANNER_EXTENDED, "full lessons list belongs in EXTENDED"
assert "Tunable Dictionary" not in p._PLANNER_EXTENDED, (
    "Dictionary should not be duplicated in EXTENDED — CORE is the single source"
)

# Preamble sanity for both instances.
opus_preamble = p._compose_preamble("opus")
local_preamble = p._compose_preamble("local")
assert opus_preamble == local_preamble, "Hermes uses one GPT-5.6 Luna prompt for both audit labels"
assert len(local_preamble) <= PREAMBLE_MAX_CHARS, (
    f"preamble {len(local_preamble)} chars > {PREAMBLE_MAX_CHARS} char budget (≈ 52k Claude tokens)"
)
print(f"  preamble opus  = {len(opus_preamble):>6} chars (≈ {len(opus_preamble) // 4:>5} Claude-tokens)")
print(f"  preamble local = {len(local_preamble):>6} chars (≈ {len(local_preamble) // 4:>5} Claude-tokens)")
print(f"  delta          = {len(opus_preamble) - len(local_preamble):>6} chars")

# The context step: the gather script's MANUAL SETTINGS block, from its marker to the next `# ──` marker, run with
# no database. Two fixture rows stand in for v_desired_state_current, as `psql -t -A` prints them.
MANUAL_SETTINGS_MARKER = "# ── 1b. MANUAL SETTINGS"
MANUAL_SETTINGS_HEADER = "--- MANUAL SETTINGS: held by a person; your plan values for these are ignored ---"
FAKE_PSQL = """#!/bin/bash
echo "mister_engage_kpa|1.55|jason (dashboard)|2026-11-02 06:47"
echo "vpd_hysteresis|0.3|jason (dashboard)|2026-11-01 18:12"
"""


def manual_settings_section() -> str:
    script = (Path(__file__).resolve().parent / "gather-plan-context.sh").read_text()
    start = script.index(MANUAL_SETTINGS_MARKER)
    block = script[start : script.index("\n# ──", start)]
    with tempfile.TemporaryDirectory() as bin_dir:
        fake = Path(bin_dir) / "psql"
        fake.write_text(FAKE_PSQL)
        fake.chmod(0o755)
        return subprocess.run(
            ["bash", "-c", f"set -euo pipefail\nDB=(psql -t -A)\nGREENHOUSE_ID=vallery\n{block}\n"],
            env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
            capture_output=True,
            text=True,
            check=True,
        ).stdout


manual_settings = manual_settings_section()
assert MANUAL_SETTINGS_HEADER in manual_settings, "gather script's MANUAL SETTINGS block lost its header"
assert "mister_engage_kpa|1.55|jason (dashboard)|2026-11-02 06:47" in manual_settings, "fixture row not rendered"
assert "(none)" not in manual_settings, "the block printed (none) over two rows"
print(f"  MANUAL SETTINGS section = {len(manual_settings)} chars with two rows")

# The context step: section 11 rendered over its fixture, appended to the context every builder gets.
from tasks.irrigation import CONTEXT_MAX_LINES, IrrigationContext, render_context  # noqa: E402

IRRIGATION_HEADER = "--- IRRIGATION (lines, next 24 h, manual items) ---"
_fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "irrigation_context.json"
section_11 = render_context(IrrigationContext.model_validate_json(_fixture.read_text()))
_section_lines = len(section_11.splitlines())
assert section_11.startswith(IRRIGATION_HEADER), "section 11 lost its header"
assert _section_lines <= CONTEXT_MAX_LINES, f"section 11 is {_section_lines} lines, over {CONTEXT_MAX_LINES}"
print(f"  section 11     = {len(section_11):>6} chars ({_section_lines} lines)")
context = f"<context stub>\n{manual_settings.rstrip()}\n{section_11}"

# Every event-type builder renders cleanly for both instances.
for event in p._PROMPT_BUILDERS:
    for instance in ("opus", "local"):
        message = p._PROMPT_BUILDERS[event](context, "<label stub>", instance)
        assert isinstance(message, str) and len(message) > 1000, (
            f"{event}/{instance} prompt suspiciously short: {len(message)} chars"
        )
        assert IRRIGATION_HEADER in message, f"{event}/{instance} prompt missing section 11"
        assert MANUAL_SETTINGS_HEADER in message, f"{event}/{instance} prompt missing the MANUAL SETTINGS section"
        assert "33 production tools" in message, f"{event}/{instance} prompt missing _STANDING_DIRECTIVES preamble"
        print(f"  {event:18s} {instance:5s} {len(message):>6} chars")

print("planner-dry: all prompts render; Hermes invariants hold")
