// SPDX-License-Identifier: MIT
// How `pnpm test:browser` runs the browser suite (#1308): the shards' own ports, the runner that fails when any shard
// fails, and the reporter that names a failure as it happens. The suite itself runs only in the in-cluster CI image; a
// stand-in script plays Playwright's CLI here.
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Writable } from "node:stream";
import { afterAll, expect, test } from "vitest";
import { failureLines } from "../../e2e/failure-reporter";
import { BASE_PORTS, SHARD_PORT_STEP, suitePorts } from "../../e2e/ports.mjs";
import { SHARDS, runShards, shardJob } from "../../e2e/run-shards.mjs";

const dir = mkdtempSync(join(tmpdir(), "verdify-shards-"));
afterAll(() => rmSync(dir, { recursive: true, force: true }));

/** A writable that keeps what it is sent. */
function sink() {
  const lines: string[] = [];
  const stream = new Writable({ write(chunk, _encoding, done) { lines.push(...String(chunk).split("\n").filter(Boolean)); done(); } });
  return { stream, lines };
}

test("each shard has its own three servers' ports, PW_PORT_OFFSET above the base; a bad offset refuses", () => {
  expect(suitePorts({})).toEqual(BASE_PORTS);
  expect(suitePorts({ PW_PORT_OFFSET: "20" })).toEqual({ ingress: 4193, api: 4194, web: 4195, oidc: 4199 });
  for (const bad of ["-10", "1.5", "x", "1001", ""]) expect(() => suitePorts({ PW_PORT_OFFSET: bad }), bad).toThrow("PW_PORT_OFFSET");
  // No shard's ports, the closed sign-in port included, meet another's.
  const all = Array.from({ length: SHARDS }, (_, index) => Object.values(suitePorts(shardJob(index + 1, SHARDS, [], {}).env)));
  expect(new Set(all.flat()).size).toBe(SHARDS * 4);
  expect(SHARD_PORT_STEP).toBeGreaterThan(BASE_PORTS.oidc - BASE_PORTS.ingress);
});

test("shard i of N runs Playwright's --shard=i/N with its own PW_SHARD and ports, and the arguments given", () => {
  expect(shardJob(2, 3, ["--grep", "@webkit"], { KEEP: "1" })).toEqual({
    args: ["test", "--shard=2/3", "--grep", "@webkit"],
    env: { KEEP: "1", PW_SHARD: "2", PW_PORT_OFFSET: String(SHARD_PORT_STEP) },
  });
});

test("the shards run at once, every line carries its shard, and each exit is kept: a failed or killed shard is non-zero", async () => {
  const cli = join(dir, "cli.mjs");
  // Each line is out before the shard ends: a pipe's writes are asynchronous off Linux.
  writeFileSync(cli, [
    "process.stderr.write('warning\\n', () => process.stdout.write(`ran ${process.argv.slice(2).join(' ')} on ${process.env.PW_PORT_OFFSET}\\n`, () => {",
    "  if (process.env.PW_SHARD === '2') process.exit(1);",
    "  if (process.env.PW_SHARD === '3') process.kill(process.pid, 'SIGKILL');",
    "}));",
  ].join("\n"));
  const out = sink(), err = sink();
  const results = await runShards({ cli, total: 3, args: ["--list"], env: { PATH: process.env.PATH }, out: out.stream, err: err.stream });
  expect(results).toEqual([{ index: 1, code: 0 }, { index: 2, code: 1 }, { index: 3, code: 1 }]);
  expect(out.lines.sort()).toEqual(["[1/3] ran test --shard=1/3 --list on 0", "[2/3] ran test --shard=2/3 --list on 10", "[3/3] ran test --shard=3/3 --list on 20"]);
  expect(err.lines).toContain("[2/3] warning");
  // A missing CLI fails its shard rather than passing it.
  expect(await runShards({ cli: join(dir, "missing.mjs"), total: 1, env: { PATH: process.env.PATH }, out: sink().stream, err: sink().stream })).toEqual([{ index: 1, code: 1 }]);
});

test("a failure names its project, file:line, title and status, then its first error without colour codes and where it was thrown", () => {
  const file = join(process.cwd(), "e2e/now.spec.ts");
  const red = `${String.fromCharCode(27)}[31m`, plain = `${String.fromCharCode(27)}[39m`;
  const lines = failureLines({ project: "chromium", title: "in band at 360 × 740", location: { file, line: 21, column: 1 } }, {
    status: "timedOut", duration: 30_150,
    errors: [
      { message: `Test timeout of 30000ms exceeded.` },
      { message: `Error: ${red}page.waitForResponse${plain}: Test timeout\nCall log: …`, location: { file, line: 155, column: 7 } },
    ],
  });
  expect(lines).toEqual([
    "✘ FAILED [chromium] e2e/now.spec.ts:21 › in band at 360 × 740 (timedOut, 30.1s)",
    "    Test timeout of 30000ms exceeded.",
    "    then: Error: page.waitForResponse: Test timeout (e2e/now.spec.ts:155:7)",
  ]);
  const failed = failureLines({ project: "webkit", title: "t", location: { file, line: 3, column: 1 } }, {
    status: "failed", duration: 1200, errors: [{ message: `expect(${red}locator${plain}).toHaveText(expected)\nExpected: "All OK"`, location: { file, line: 25, column: 9 } }],
  });
  expect(failed).toEqual(["✘ FAILED [webkit] e2e/now.spec.ts:3 › t (failed, 1.2s)", "    expect(locator).toHaveText(expected)", "    Expected: \"All OK\"", "    at e2e/now.spec.ts:25:9"]);
});
