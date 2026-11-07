#!/usr/bin/env bash
# GHA validate: the native `validate` step (jvallery/agents WorkflowTemplate
# verdify-platform-ci, template validate) on the exact checkout. Same image
# (python:3.13-bookworm, the job image of the verdify-platform-ci set), same
# dependency bootstrap, same gate: scripts/ci-local.sh with CI_BASE_REF set, so
# the diff gates (rules 6 and 7, firmware replay diff) run. VALIDATE_PART (set
# per matrix job in .github/workflows/ci.yml) selects one parallel part (#1459).
. "$(dirname "$0")/lib.sh"

# Assert instead of install: ci-local.sh skips the g++ gates when the compiler
# is absent, so a missing toolchain must fail here rather than drop a gate.
for tool in g++ git curl tar make; do
  command -v "$tool" >/dev/null || { echo "CI image is missing $tool" >&2; exit 1; }
done

ci_require_runner_tools

ci_fetch_history
impact="$(mktemp -d)"
if [ "${GITHUB_EVENT_NAME:-}" = push ]; then
  ci_classify "$impact" --push >/dev/null
else
  ci_classify "$impact" >/dev/null
fi
# A digest-only pin push to main skips validation, as natively.
if [ "$(cat "$impact/mode")" = skip ]; then
  echo "path impact: digest-only pin diff; validation not required"
  exit 0
fi
base="$(ci_base)"
if [ -n "$base" ]; then
  export CI_BASE_REF="$base"
else
  unset CI_BASE_REF
fi

# No editable install (pyproject has no build-system); dependency lists come
# straight from pyproject, as in the native step.
python3 - <<'PY' > "$impact/reqs.txt"
import tomllib
d = tomllib.load(open("pyproject.toml", "rb"))
reqs = list(d["project"]["dependencies"])
opt = d["project"].get("optional-dependencies", {})
for grp in ("dev", "planner", "api"):
    reqs += opt.get(grp, [])
print("\n".join(reqs))
PY
python3 -m venv .venv
.venv/bin/pip -q install -r "$impact/reqs.txt" paho-mqtt 'mcp[cli]' pydantic-settings pytest pytest-asyncio
bash scripts/ci-local.sh
