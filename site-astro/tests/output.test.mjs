import assert from "node:assert/strict";
import { access, readFile, readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST = path.join(ROOT, "dist");
const read = (relative) => readFile(path.join(DIST, ...relative.split("/")), "utf8");

test("fixture build preserves leaf, folder, planner, and alias route shapes", async () => {
  for (const relative of [
    "index.html",
    "start/about.html",
    "data/plans/index.html",
    "plans/2026-07-12.html",
    "about.html",
    "story.html",
    "plans/latest.html",
    "press/index.html",
  ]) {
    await access(path.join(DIST, ...relative.split("/")));
  }
});

test("prose, table, download, media, and Grafana occurrence semantics survive", async () => {
  const html = await read("index.html");
  assert.match(html, /deterministic control on the ESP32/);
  assert.match(html, /<table>/);
  assert.match(html, /greenhouse-evidence\.csv/);
  assert.match(html, /static\/graphs\/overview\.svg/);
  assert.match(html, /src="\/static\/graphs\/overview\.svg"[^>]+width="640"[^>]+height="180"/);
  assert.match(html, /sizes="\(max-width: 620px\)/);
  assert.match(html, /class="grafana-evidence"/);
  assert.match(html, /data-iframe-src="https:\/\/graphs\.verdify\.ai\/d-solo\//);
  assert.match(html, /data-image-src="https:\/\/graphs\.verdify\.ai\/render\/d-solo\//);
  assert.doesNotMatch(html, /<iframe[^>]+graphs\.verdify\.ai/i);
  assert.match(html, /class="media-lightbox"/);
  assert.match(html, /data-lightbox-previous/);
  assert.match(html, /data-lab-navigation-toggle/);
  assert.match(html, /class="lab-page lab-page--home"/);
});

test("stage output stays noindex, blocker-labelled, searchable, and auditable", async () => {
  const html = await read("start/about.html");
  assert.match(html, /name="robots" content="noindex,follow"/);
  assert.match(html, /rel="icon" href="\/assets\/verdify-lab-lockup\.svg" type="image\/svg\+xml"/);
  assert.match(html, /known frozen-baseline integrity blockers remain open/);
  assert.match(html, /src="\/assets\/verdify-lab-lockup\.svg"/);
  assert.match(html, /href="https:\/\/verdify\.ai\/services\//);
  const css = await Promise.all(
    (await readdir(path.join(DIST, "_astro")))
      .filter((name) => name.endsWith(".css"))
      .map((name) => readFile(path.join(DIST, "_astro", name), "utf8")),
  );
  assert.match(css.join("\n"), /IBM Plex/);
  assert.match(await read("robots.txt"), /Disallow: \//);

  const routeManifest = JSON.parse(await read("route-manifest.json"));
  assert.equal(routeManifest.build.sourceCount, 4);
  assert.equal(routeManifest.build.snapshotMarkdownCount, 4);
  assert.equal(routeManifest.build.aliasCount, 4);
  assert.equal(routeManifest.build.rollingPlanCompatibility.suppressedDeclarationCount, 1);
  assert.equal(routeManifest.build.grafanaOccurrenceCount, 1);
  assert.equal(routeManifest.build.approvalEligible, false);
  assert.equal(routeManifest.build.siteShell.contractVersion, "1.0.0");
  assert.equal(routeManifest.build.siteShell.releaseDigest, "sha256:897f872a6ab8de39f2c55e0d7833d723c00b1c9533673df6309472552956b42c");
  assert.equal(routeManifest.build.sanitization.fixtureOnly, true);
  assert.equal(routeManifest.routes.filter((record) => record.source.endsWith(".md")).length, 7);
  await access(path.join(DIST, "pagefind", "pagefind.js"));
  for (const unused of ["pagefind-component-ui.js", "pagefind-ui.js"]) {
    await assert.rejects(access(path.join(DIST, "pagefind", unused)), { code: "ENOENT" });
  }
  await access(path.join(DIST, "assets", "verdify-lab-lockup.svg"));
});

test("alias pages retain exact refresh, canonical, and noindex semantics", async () => {
  const alias = await read("about.html");
  assert.match(alias, /http-equiv="refresh" content="0; URL='\/start\/about\/?'"/);
  assert.match(alias, /rel="canonical" href="https:\/\/lab-stage\.verdify\.ai\/start\/about\/??"/);
  assert.match(alias, /name="robots" content="noindex,follow"/);
});
