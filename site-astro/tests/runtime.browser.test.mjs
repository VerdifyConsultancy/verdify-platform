import { expect, test } from "@playwright/test";
import { createServer } from "node:http";
import { access, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST = path.join(ROOT, "dist");
const securityHeaders = await readFile(path.join(ROOT, "nginx", "security-headers.inc"), "utf8");
const CSP = securityHeaders.match(/Content-Security-Policy "([^"]+)"/)?.[1];
if (!CSP) throw new Error("runtime CSP is missing");

const contentTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".js", "application/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".pagefind", "application/octet-stream"],
  [".pf_fragment", "application/octet-stream"],
  [".pf_index", "application/octet-stream"],
  [".pf_meta", "application/octet-stream"],
  [".svg", "image/svg+xml"],
  [".wasm", "application/wasm"],
  [".woff", "font/woff"],
  [".woff2", "font/woff2"],
]);

async function resolveRequest(pathname) {
  const decoded = decodeURIComponent(pathname);
  if (decoded.includes("\0") || decoded.split("/").includes("..")) return null;
  const relative = decoded.replace(/^\/+/, "");
  const candidates = path.extname(relative)
    ? [relative]
    : relative
      ? [`${relative}.html`, `${relative}/index.html`]
      : ["index.html"];
  for (const candidate of candidates) {
    const absolute = path.resolve(DIST, candidate);
    if (!absolute.startsWith(`${DIST}${path.sep}`) && absolute !== DIST) continue;
    try {
      await access(absolute);
      return absolute;
    } catch {
      // Try the next canonical static route shape.
    }
  }
  return null;
}

let server;
let origin;

test.beforeAll(async () => {
  server = createServer(async (request, response) => {
    const target = await resolveRequest(new URL(request.url, "http://localhost").pathname);
    response.setHeader("Content-Security-Policy", CSP);
    response.setHeader("X-Content-Type-Options", "nosniff");
    if (!target) {
      response.statusCode = 404;
      response.end("not found");
      return;
    }
    response.setHeader("Content-Type", contentTypes.get(path.extname(target)) ?? "application/octet-stream");
    response.end(await readFile(target));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  origin = `http://127.0.0.1:${address.port}`;
});

test.afterAll(async () => {
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
});

function collectBrowserFailures(page) {
  const failures = [];
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => failures.push(`pageerror: ${error.message}`));
  return failures;
}

test("Pagefind searches under CSP and KaTeX fonts remain same-origin", async ({ page }) => {
  const failures = collectBrowserFailures(page);
  const response = await page.goto(`${origin}/`);
  expect(response.headers()["content-security-policy"]).toContain("'wasm-unsafe-eval'");
  await page.locator("#lab-search").fill("greenhouse");
  await expect(page.locator("#lab-search-results li").first()).toBeVisible();
  await expect(page.locator("#lab-search-results")).not.toContainText("Search index unavailable");
  await page.evaluate(() => document.fonts.ready);
  expect(await page.locator('link[href*="katex.min"]').count()).toBe(1);
  expect(failures).toEqual([]);
});

test("captured contact markup is visible, keyboard-focusable, and tokenized", async ({ page }) => {
  const failures = collectBrowserFailures(page);
  await page.goto(`${origin}/`);
  await page.locator(".lab-article > div").evaluate((container) => {
    container.innerHTML = `
      <h1>Contact</h1>
      <form class="verdify-contact-form">
        <div class="verdify-contact-grid">
          <label><span>Name</span><input name="name" required></label>
          <label><span>Reply email</span><input name="email" type="email" required></label>
        </div>
        <label><span>Topic</span><select name="topic"><option>Build notes</option></select></label>
        <label><span>Message</span><textarea name="message" required></textarea></label>
        <div class="verdify-contact-actions"><button type="submit">Send message</button><p role="status"></p></div>
      </form>
      <style>
        .verdify-contact-form input, .verdify-contact-form select, .verdify-contact-form textarea {
          border: 1px solid var(--lightgray); background: var(--light); color: var(--dark);
        }
        .verdify-contact-actions button { background: var(--tertiary); color: var(--light); }
      </style>`;
  });

  const name = page.getByRole("textbox", { name: "Name" });
  const message = page.getByRole("textbox", { name: "Message" });
  const submit = page.getByRole("button", { name: "Send message" });
  await expect(name).toBeVisible();
  await expect(message).toBeVisible();
  await expect(submit).toBeVisible();

  const styles = await name.evaluate((element) => {
    const computed = getComputedStyle(element);
    return { background: computed.backgroundColor, border: computed.borderTopWidth, borderStyle: computed.borderTopStyle };
  });
  expect(styles.background).not.toBe("rgba(0, 0, 0, 0)");
  expect(styles.border).not.toBe("0px");
  expect(styles.borderStyle).toBe("solid");
  expect((await submit.boundingBox()).height).toBeGreaterThanOrEqual(44);

  await name.focus();
  expect(await name.evaluate((element) => getComputedStyle(element).boxShadow)).not.toBe("none");
  expect(failures).toEqual([]);
});
