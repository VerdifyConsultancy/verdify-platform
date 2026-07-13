import assert from "node:assert/strict";
import { cp, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { verifyProductionOutput } from "../scripts/verify-production-output.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST = path.join(ROOT, "dist");
const PRODUCTION_ORIGIN = "https://lab.verdify.ai";
const read = (relative) => readFile(path.join(DIST, ...relative.split("/")), "utf8");

async function mutatedDist(relative, transform) {
  const root = await mkdtemp(path.join(os.tmpdir(), "verdify-lab-production-output-"));
  const candidate = path.join(root, "dist");
  await cp(DIST, candidate, { recursive: true });
  const target = path.join(candidate, ...relative.split("/"));
  await writeFile(target, transform(await readFile(target, "utf8")));
  return { candidate, root };
}

test("production fixture proves per-page SEO without satisfying the release gate", async () => {
  await verifyProductionOutput({ dist: DIST, allowFixture: true });
  await assert.rejects(
    verifyProductionOutput({ dist: DIST }),
    /synthetic fixtures can never satisfy the production release verifier/,
  );

  const root = await read("index.html");
  assert.match(root, /<meta name="robots" content="index,follow">/);
  assert.match(root, new RegExp(`<link rel="canonical" href="${PRODUCTION_ORIGIN}/">`));
  assert.doesNotMatch(root, /Stage preview|globally noindex|not approval evidence|lab-stage\.verdify\.ai/);

  const notFound = await read("404.html");
  assert.match(notFound, /<meta name="robots" content="noindex,follow">/);
  assert.match(notFound, new RegExp(`<link rel="canonical" href="${PRODUCTION_ORIGIN}/404">`));
  assert.match(notFound, /<title>Not Found — Verdify Lab<\/title>/);
  assert.match(notFound, /<h1>404<\/h1>/);
  assert.match(notFound, /Either this page is private or doesn't exist\./);
  assert.match(notFound, /<a href="\/">Return to Homepage<\/a>/);

  const alias = await read("about.html");
  assert.match(alias, /<meta name="robots" content="noindex,follow">/);
  assert.match(alias, new RegExp(`<link rel="canonical" href="${PRODUCTION_ORIGIN}/start/about">`));
  assert.equal(
    await read("robots.txt"),
    `User-agent: *\nAllow: /\nDisallow: /static/vision/\nDisallow: /greenhouse/lessons/raw\n\nSitemap: ${PRODUCTION_ORIGIN}/sitemap.xml\n`,
  );
});

test("production verifier rejects a global-noindex regression", async () => {
  const mutation = await mutatedDist("index.html", (source) => source.replace("index,follow", "noindex,follow"));
  try {
    await assert.rejects(
      verifyProductionOutput({ dist: mutation.candidate, allowFixture: true }),
      /wrong per-page robots policy/,
    );
  } finally {
    await rm(mutation.root, { recursive: true, force: true });
  }
});

test("production verifier rejects an alias canonical that leaves the production origin", async () => {
  const mutation = await mutatedDist("about.html", (source) => source.replaceAll(
    `${PRODUCTION_ORIGIN}/start/about`,
    "https://lab-stage.verdify.ai/start/about",
  ));
  try {
    await assert.rejects(
      verifyProductionOutput({ dist: mutation.candidate, allowFixture: true }),
      /wrong canonical URL/,
    );
  } finally {
    await rm(mutation.root, { recursive: true, force: true });
  }
});

test("production Docker runtime fixes origin and indexing policy without build arguments", async () => {
  const dockerfile = await readFile(path.join(ROOT, "Dockerfile.production"), "utf8");
  assert.match(dockerfile, /LAB_BUILD_TARGET=production/);
  assert.match(dockerfile, /SITE_ORIGIN=https:\/\/lab\.verdify\.ai/);
  assert.match(dockerfile, /STAGE_GLOBAL_NOINDEX=false/);
  assert.match(dockerfile, /RUN test -f \.snapshot\/attestation\.json && npm run build:production/);
  assert.match(dockerfile, /COPY nginx\/security-headers-production\.inc \/etc\/nginx\/conf\.d\/security-headers\.inc/);
  assert.doesNotMatch(dockerfile, /ARG (?:SITE_ORIGIN|STAGE_GLOBAL_NOINDEX|LAB_BUILD_TARGET)/);

  const headers = await readFile(path.join(ROOT, "nginx/security-headers-production.inc"), "utf8");
  assert.doesNotMatch(headers, /X-Robots-Tag|noindex|nofollow|noarchive/i);
});
