// SPDX-License-Identifier: MIT
/**
 * WP4's named fixtures (Control, redesign §10.6). Each is a patch over the base `in-band` world, written with world.mjs's
 * exported helpers (`clock`, `lineOf`, `itemOf`, `settingOf`, `runLine`, `openAlert`, …) the way the fixtures in
 * world.mjs are, and it may apply a base fixture first (`import { FIXTURES as BASE } from "./world.mjs"`, then
 * `BASE["l01-running"](world)`). index.mjs registers this map
 * once, through `registerFixtures`; a name already registered (a base fixture's or another package's) fails the import,
 * so no package shadows another. Screen packages add fixtures here only, never in world.mjs or operations.mjs.
 *
 * @type {Readonly<Record<string, (world: any) => void>>}
 */
export const FIXTURES = {
  // Equipment's Controller card (redesign §5.12): free heap under the 32 kB floor the firmware restarts under (#429), so
  // the card's rule and the heap meter turn fault, in words.
  "controller-heap-low": (world) => {
    world.controller = { ...world.controller, heap_free_kb: 28.4, heap_largest_block_kb: 9.6 };
  },
};
