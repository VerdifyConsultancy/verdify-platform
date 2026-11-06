// SPDX-License-Identifier: MIT
/**
 * The fixture registry (VD-02). `fixture(name)` returns the response set per operationId: what each operation
 * answers under that fixture, for its example request (`EXAMPLES`). A list of names applies in order over `in-band`.
 * Vitest imports it directly; Playwright selects the same fixtures through the fixture API's `POST /__control`. The
 * base API-09 response is `settings.json`, rendered at test time from the tunable registry by `pnpm fixtures:settings`
 * (VD-12), which `pnpm test` runs first.
 */
import { FIXTURES as CONTROL } from "./control.mjs";
import { FIXTURES as CROPS } from "./crops.mjs";
import { FIXTURES as GRAPHS } from "./graphs.mjs";
import { FIXTURES as NOW_ACTIVITY } from "./now-activity.mjs";
import { EXAMPLES, OPERATION_IDS, answer } from "./operations.mjs";
import { FIXTURES as WATER } from "./water.mjs";
import { FIXTURES, buildWorld, registerFixtures } from "./world.mjs";

/**
 * The screen packages' own fixtures (redesign §10.1), registered once here, in package order, after the base ones in
 * world.mjs: every user of the registry imports it through this file, so `fixture()`, the fixture API and the contract
 * checks see them all.
 */
export const PACKAGE_FIXTURES = { "now-activity.mjs": NOW_ACTIVITY, "graphs.mjs": GRAPHS, "water.mjs": WATER, "control.mjs": CONTROL, "crops.mjs": CROPS };
for (const [owner, fixtures] of Object.entries(PACKAGE_FIXTURES)) registerFixtures(owner, fixtures);

/** Every fixture name, `in-band` first. */
export const FIXTURE_NAMES = Object.keys(FIXTURES);

/**
 * The writes a fixture describes the reads after. `fixture()` answers that fixture's reads once its writes have
 * settled: `requested-to-confirmed` after its API-10 write (confirmed, or not_confirmed with `confirm: false`), and
 * `flow-after-stop` and `stop-unconfirmed` after a Stop. The fixture API starts before them, so a browser test sends
 * them itself and watches the reads change (a requested setting settles on the third API-09 read).
 */
export const AFTER = { "requested-to-confirmed": ["API-10"], "flow-after-stop": ["API-IRR-07"], "stop-unconfirmed": ["API-IRR-07"] };
/** As the reads see a write once it has settled. */
const SETTLED = { readsAfter: () => Number.POSITIVE_INFINITY };

/**
 * The response body each operationId answers under `names` (a name or a list). Options: `now` (epoch ms or an ISO
 * time, default the current time; every time in the set is relative to it), `confirm` (false: the write under
 * `requested-to-confirmed` settles not_confirmed) and `fail` ({operationId: status}, as `/__control` takes it). A
 * fixture's `AFTER` writes answer as they did when sent, and every other operation answers after them.
 * @param {string | string[]} [names]
 * @param {{ now?: number | string, confirm?: boolean, fail?: Record<string, number> }} [options]
 * @returns {Record<string, any>}
 */
export function fixture(names = "in-band", options = {}) {
  const now = options.now ?? Date.now();
  const before = buildWorld(names, { ...options, now });
  const after = buildWorld(names, { ...options, now });
  const scripted = [names].flat().flatMap((name) => AFTER[name] ?? []);
  for (const operationId of scripted) answer(after, EXAMPLES[operationId](after), SETTLED).effect?.(after, SETTLED);
  return Object.fromEntries(OPERATION_IDS.map((operationId) => {
    const world = scripted.includes(operationId) ? before : after;
    return [operationId, answer(world, EXAMPLES[operationId](world)).body];
  }));
}

export { EXAMPLES, OPERATION_IDS, ROUTES, answer, routeOf } from "./operations.mjs";
export { buildWorld, registerFixtures } from "./world.mjs";
