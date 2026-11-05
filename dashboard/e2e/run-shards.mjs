// SPDX-License-Identifier: MIT
/**
 * The browser suite as shards at once, the last part of `pnpm test:browser` (after the guard, the settings fixture and
 * the one build): `playwright test --shard=i/N` for each shard, each with its own fixture API, BFF and ingress
 * PW_PORT_OFFSET ports apart (`e2e/ports.mjs`), because one fixture API holds one state, and its own artifacts
 * (PW_SHARD). One worker each, three at once, fits the dashboard step: about a third of the suite's twenty-odd minutes on
 * one worker, on the step's 6 CPUs after vitest's 13 minutes. Every line a shard prints carries `[i/N]`, and the run
 * fails when any shard fails or dies. Arguments after the script go to every shard (`-- --grep @webkit`).
 */
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { createInterface } from "node:readline";
import { SHARD_PORT_STEP } from "./ports.mjs";

export const SHARDS = 3;

/**
 * Shard `index` (from 1) of the run: its environment and its arguments for Playwright's CLI.
 * @param {number} index
 * @param {number} total
 * @param {readonly string[]} args
 * @param {Record<string, string | undefined>} env
 */
export function shardJob(index, total, args, env) {
  return {
    args: ["test", `--shard=${index}/${total}`, ...args],
    env: { ...env, PW_SHARD: String(index), PW_PORT_OFFSET: String((index - 1) * SHARD_PORT_STEP) },
  };
}

/**
 * Runs the shards at once and resolves with each one's exit: its code, or 1 when a signal or a failed start ended it.
 * @param {{ cli: string, total?: number, args?: readonly string[], env?: Record<string, string | undefined>, out?: NodeJS.WritableStream, err?: NodeJS.WritableStream }} options
 * @returns {Promise<{ index: number, code: number }[]>}
 */
export async function runShards({ cli, total = SHARDS, args = [], env = process.env, out = process.stdout, err = process.stderr }) {
  const children = [];
  const stop = (signal) => { for (const child of children) child.kill(signal); };
  process.once("SIGTERM", stop).once("SIGINT", stop);
  try {
    return await Promise.all(Array.from({ length: total }, (_, offset) => new Promise((resolve) => {
      const index = offset + 1;
      const job = shardJob(index, total, args, env);
      const child = spawn(process.execPath, [cli, ...job.args], { env: job.env, stdio: ["ignore", "pipe", "pipe"] });
      children.push(child);
      const prefix = `[${index}/${total}] `;
      const lines = [[child.stdout, out], [child.stderr, err]].map(([input, sink]) => new Promise((done) => {
        createInterface({ input, crlfDelay: Infinity }).on("line", (line) => sink.write(`${prefix}${line}\n`)).on("close", done);
      }));
      let started = true;
      child.on("error", (error) => {
        started = false;
        err.write(`${prefix}could not start: ${error.message}\n`);
        resolve({ index, code: 1 });
      });
      child.on("close", (code, signal) => {
        void Promise.all(lines).then(() => resolve({ index, code: started && signal === null && code !== null ? code : 1 }));
      });
    })));
  } finally {
    process.off("SIGTERM", stop).off("SIGINT", stop);
  }
}

if (import.meta.main) {
  const cli = createRequire(import.meta.url).resolve("@playwright/test/cli");
  const results = await runShards({ cli, args: process.argv.slice(2) });
  const failed = results.filter((result) => result.code !== 0);
  process.stdout.write(`browser shards: ${results.length - failed.length} of ${results.length} passed${failed.length ? `; failed: ${failed.map((result) => `${result.index}/${results.length} (exit ${result.code})`).join(", ")}` : ""}\n`);
  process.exitCode = failed.length ? 1 : 0;
}
