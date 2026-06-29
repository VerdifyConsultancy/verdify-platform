#!/usr/bin/env bash
# Create/update the repo-local Python tooling environment.
#
# Dependencies are read from pyproject.toml so this script does not become a
# second requirements source. BOOTSTRAP_EXTRAS is a comma-separated list of
# optional dependency groups to include; defaults live in the Makefile.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

venv="${VENV:-.venv}"
extras="${BOOTSTRAP_EXTRAS:-dev,api,planner}"
requested_python="${BOOTSTRAP_PYTHON:-}"

choose_python() {
  if [[ -n "$requested_python" ]]; then
    printf '%s\n' "$requested_python"
    return
  fi
  for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  return 1
}

python_bin="$(choose_python)" || {
  echo "No Python interpreter found. Install Python 3.12+ or set BOOTSTRAP_PYTHON=/path/to/python." >&2
  exit 127
}

"$python_bin" - <<'PY'
import sys

if sys.version_info < (3, 12):
    raise SystemExit(
        "Verdify tooling requires Python 3.12+ per pyproject.toml. "
        f"Found {sys.version.split()[0]}. Install python3.12/python3.13 or set BOOTSTRAP_PYTHON."
    )
PY

if [[ ! -x "$venv/bin/python" ]]; then
  "$python_bin" -m venv "$venv"
fi

"$venv/bin/python" -m pip install --upgrade pip setuptools wheel

req_file="$(mktemp)"
trap 'rm -f "$req_file"' EXIT

BOOTSTRAP_EXTRAS="$extras" "$venv/bin/python" - <<'PY' > "$req_file"
import os
import tomllib
from pathlib import Path

pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
project = pyproject["project"]
requirements = list(project.get("dependencies", []))
optional = project.get("optional-dependencies", {})

for extra in [item.strip() for item in os.environ.get("BOOTSTRAP_EXTRAS", "").split(",") if item.strip()]:
    if extra not in optional:
        raise SystemExit(f"Unknown optional dependency group {extra!r}; available: {', '.join(sorted(optional))}")
    requirements.extend(optional[extra])

for requirement in requirements:
    print(requirement)
PY

"$venv/bin/python" -m pip install -r "$req_file"

if command -v pre-commit >/dev/null 2>&1 || [[ -x "$venv/bin/pre-commit" ]]; then
  "$venv/bin/pre-commit" install
fi

echo "✓ Verdify tooling venv ready at $venv"
