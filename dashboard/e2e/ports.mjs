// SPDX-License-Identifier: MIT
/**
 * The browser suite's ports: the fixture ingress (the browser's base URL), the fixture API, the BFF, and the closed port
 * OIDC_ISSUER names, so sign-in reads unavailable. `pnpm test:browser` runs the suite as shards at once
 * (`e2e/run-shards.mjs`), each with its own three servers PW_PORT_OFFSET above these, because one fixture API holds one
 * state: a test's control call resets the requests, writes and keys every other test on that API would read.
 */
export const BASE_PORTS = { ingress: 4173, api: 4174, web: 4175, oidc: 4179 };
/** Each shard's ports sit this far above the one before's, past the four above. */
export const SHARD_PORT_STEP = 10;

/**
 * The ports for this process, from PW_PORT_OFFSET (0 when unset).
 * @param {Record<string, string | undefined>} [env]
 * @returns {{ ingress: number, api: number, web: number, oidc: number }}
 */
export function suitePorts(env = process.env) {
  const raw = env.PW_PORT_OFFSET ?? "0";
  const offset = /^\d{1,4}$/.test(raw) ? Number(raw) : Number.NaN;
  if (!(offset <= 1000)) throw new Error(`PW_PORT_OFFSET must be a whole number from 0 to 1000, not ${JSON.stringify(raw)}`);
  return {
    ingress: BASE_PORTS.ingress + offset, api: BASE_PORTS.api + offset, web: BASE_PORTS.web + offset, oidc: BASE_PORTS.oidc + offset,
  };
}
