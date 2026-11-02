// SPDX-License-Identifier: MIT
/**
 * The fixture registry (VD-02). `fixture(name)` returns the response set per operationId: what each operation
 * answers under that fixture, for its example request (`EXAMPLES`). A list of names applies in order over `in-band`.
 * Vitest imports it directly; Playwright selects the same fixtures through the fixture API's `POST /__control`.
 */
import { EXAMPLES, OPERATION_IDS, answer } from "./operations.mjs";
import { FIXTURES, buildWorld } from "./world.mjs";

/** Every fixture name, `in-band` first. */
export const FIXTURE_NAMES = Object.keys(FIXTURES);

/**
 * The response body each operationId answers under `names` (a name or a list). Options: `now` (epoch ms or an ISO
 * time, default the current time; every time in the set is relative to it), `confirm` (false: a write under
 * `requested-to-confirmed` reads not_confirmed) and `fail` ({operationId: status}, as `/__control` takes it).
 * @param {string | string[]} [names]
 * @param {{ now?: number | string, confirm?: boolean, fail?: Record<string, number> }} [options]
 * @returns {Record<string, any>}
 */
export function fixture(names = "in-band", options = {}) {
  const world = buildWorld(names, options);
  return Object.fromEntries(OPERATION_IDS.map((operationId) => [operationId, answer(world, EXAMPLES[operationId](world)).body]));
}

export { EXAMPLES, OPERATION_IDS, ROUTES, answer, routeOf } from "./operations.mjs";
export { buildWorld } from "./world.mjs";
