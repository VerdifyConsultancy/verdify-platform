// SPDX-License-Identifier: MIT
import { defineConfig } from "vitest/config";
export default defineConfig({ test: { testTimeout: 60_000, hookTimeout: 60_000, maxWorkers: 1, include: ["app/**/*.test.{ts,tsx}", "bff/**/*.test.mjs"] } });
