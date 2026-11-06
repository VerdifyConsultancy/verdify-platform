#!/usr/bin/env bash
# The dashboard job's checks, run inside its job image by ci/gha/dashboard.sh.
. "$(dirname "$0")/lib.sh"
# Fail on a toolchain that is not the dashboard architecture's §3 pins.
node_version="$(node --version)"
pnpm_version="$(pnpm --version)"
playwright_version="$(node -p "require('/opt/weather-browser-tools/node_modules/@playwright/test/package.json').version")"
echo "dashboard toolchain: node ${node_version}, pnpm ${pnpm_version}, @playwright/test ${playwright_version}"
[ "$node_version" = v24.22.1 ] || { echo "dashboard CI image: node ${node_version}, expected v24.22.1" >&2; exit 1; }
[ "$pnpm_version" = 12.15.0 ] || { echo "dashboard CI image: pnpm ${pnpm_version}, expected 12.15.0" >&2; exit 1; }
[ "$playwright_version" = 1.64.1 ] || { echo "dashboard CI image: @playwright/test ${playwright_version}, expected 1.64.1" >&2; exit 1; }
cd dashboard
pnpm install --frozen-lockfile
pnpm lint
pnpm typecheck
pnpm test
pnpm run --if-present contract:check
pnpm run --if-present test:browser
