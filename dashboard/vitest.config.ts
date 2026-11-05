// SPDX-License-Identifier: MIT
import { availableParallelism } from "node:os";
import { defineConfig } from "vitest/config";
// One worker per spare CPU, at most four: the in-cluster step's pod runs the files side by side, and a laptop keeps a
// CPU free (`--maxWorkers` on the command line overrides it). Side by side on a busy node the longest test (Water's line
// controls, 11 minutes of fake time polled every 5 s) has taken from 35 s to past 60 s, so a test gets 120 s.
const maxWorkers = Math.min(4, Math.max(1, availableParallelism() - 1));
export default defineConfig({ test: { testTimeout: 120_000, hookTimeout: 60_000, maxWorkers, include: ["app/**/*.test.{ts,tsx}", "bff/**/*.test.mjs"] } });
