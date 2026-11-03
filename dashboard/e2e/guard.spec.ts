// SPDX-License-Identifier: MIT
// The guard's negative control, after weather's public.spec.ts: a clean run of the other specs means something only if
// the guard really sees an inline style, a page error and a request off localhost, on both engines.
import { WEBKIT, expect, test } from "./support";

const CSP = "default-src 'self'; script-src 'self'; style-src 'self'";

test("the guard catches an inline style, a page error and a request off localhost", WEBKIT, async ({ page, guard }) => {
  const seen = (found: string) => expect.poll(() => guard.problems.some((problem) => problem.includes(found))).toBe(true);
  await page.route("**/__csp-control/inline-style", (route) => route.fulfill({
    contentType: "text/html", headers: { "Content-Security-Policy": CSP },
    body: "<!doctype html><html lang=\"en\"><head><title>CSP control</title></head><body><p style=\"color:red\">Forbidden application style</p></body></html>",
  }));
  await page.route("**/__csp-control/no-policy", (route) => route.fulfill({ contentType: "text/html", body: "<!doctype html><html lang=\"en\"><head><title>Control</title></head><body></body></html>" }));
  await page.route("https://off-localhost.invalid/**", (route) => route.abort());
  await page.goto("/__csp-control/inline-style");
  await seen("CSP violation: style-src-attr");
  await page.goto("/__csp-control/no-policy");
  await page.evaluate(() => { setTimeout(() => { throw new Error("guard control"); }, 0); });
  await seen("page error: guard control");
  await page.evaluate(() => { void fetch("https://off-localhost.invalid/control").catch(() => undefined); });
  await seen("request off localhost: https://off-localhost.invalid/control");
  // Handled here, so the teardown check passes.
  guard.problems.length = 0;
});
