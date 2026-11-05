// SPDX-License-Identifier: MIT
/**
 * Names each failure the moment its test ends: the project, file:line and title, the status and time, then the first
 * error's message and where it was thrown, and the first line of any error after it (a timeout's second error says what
 * the test was waiting for). The list reporter prints its details only in its closing summary, which a run the dashboard
 * step's deadline kills never reaches, so this keeps them in the log; the list and JSON reporters stay as they are.
 */
import { relative } from "node:path";
import type { Location, Reporter, TestCase, TestError, TestResult } from "@playwright/test/reporter";

/** The colour codes expect's messages carry. */
const ANSI = new RegExp(`${String.fromCharCode(27)}\\[[0-9;]*m`, "g");
/** A message's first lines, enough for a locator, its expected and received values and the call log's start. */
const MESSAGE_LINES = 40;

const where = (location: Location | undefined) => (location ? `${relative(process.cwd(), location.file)}:${location.line}:${location.column}` : null);
const words = (error: TestError) => (error.message ?? error.value ?? "(no message)").replace(ANSI, "").trimEnd();

/** One failure's lines, each indented under its head. */
export function failureLines(test: Pick<TestCase, "title" | "location"> & { project: string }, result: Pick<TestResult, "status" | "duration" | "errors">): string[] {
  const head = `✘ FAILED [${test.project}] ${relative(process.cwd(), test.location.file)}:${test.location.line} › ${test.title} (${result.status}, ${(result.duration / 1000).toFixed(1)}s)`;
  const [first, ...rest] = result.errors;
  if (!first) return [head, "    (no error recorded)"];
  const message = words(first).split("\n");
  const shown = message.slice(0, MESSAGE_LINES);
  if (message.length > MESSAGE_LINES) shown.push(`… ${message.length - MESSAGE_LINES} more lines`);
  const at = where(first.location);
  return [
    head,
    ...shown.map((line) => `    ${line}`),
    ...(at ? [`    at ${at}`] : []),
    ...rest.map((error) => `    then: ${words(error).split("\n")[0]}${where(error.location) ? ` (${where(error.location)})` : ""}`),
  ];
}

export default class FailureReporter implements Reporter {
  printsToStdio() {
    return true;
  }

  onTestEnd(test: TestCase, result: TestResult) {
    if (result.status === test.expectedStatus || result.status === "skipped") return;
    const project = test.parent.project()?.name ?? "";
    process.stdout.write(`${failureLines({ title: test.title, location: test.location, project }, result).join("\n")}\n`);
  }

  onError(error: TestError) {
    process.stdout.write(`✘ ERROR outside a test: ${words(error).split("\n").slice(0, MESSAGE_LINES).join("\n    ")}\n`);
  }
}
