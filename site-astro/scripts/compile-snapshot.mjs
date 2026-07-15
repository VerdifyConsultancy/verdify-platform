import { createHash } from "node:crypto";
import { cp, lstat, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { toHtml } from "hast-util-to-html";
import rehypeKatex from "rehype-katex";
import rehypeRaw from "rehype-raw";
import rehypeSlug from "rehype-slug";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkParse from "remark-parse";
import remarkRehype from "remark-rehype";
import { unified } from "unified";
import { SKIP, visit } from "unist-util-visit";
import YAML from "yaml";
import sharp from "sharp";

import {
  discoverCurrentMediaOccurrence,
  discoverGraphOccurrence,
  loadSelectedOccurrenceRelease,
  materializeOccurrenceBlobs,
  occurrenceStateIndex,
  staticOccurrenceManifest,
} from "./lib/occurrence-release.mjs";
import {
  occurrenceExportPolicySha256,
  readCanonicalExportDocument,
  staticOccurrenceDiscoverySha256,
  validateOccurrenceExportPolicy,
  validatePolicyManifestBinding,
} from "./lib/occurrence-export-contract.mjs";
import {
  OccurrenceReleaseStore,
  createOccurrenceReleaseStore,
  parseOccurrenceReleaseStoreLocation,
} from "./lib/occurrence-release-store.mjs";
import { verifySnapshot } from "./lib/snapshot.mjs";

const SCRIPT_ROOT = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(SCRIPT_ROOT, "..");
const GENERATED_ROOT = path.join(PROJECT_ROOT, ".generated");
const PUBLIC_ROOT = path.join(GENERATED_ROOT, "public");
const RECORDS_PATH = path.join(GENERATED_ROOT, "content-records.json");
const ASSETS_PATH = path.join(GENERATED_ROOT, "asset-records.json");
const BUILD_PATH = path.join(GENERATED_ROOT, "build.json");
const SITE_SHELL_ROOT = path.join(GENERATED_ROOT, "site-shell-root");
const SITE_SHELL_PUBLIC = path.join(SITE_SHELL_ROOT, "public");
const COMPAT_PUBLIC_ROOT = path.join(PROJECT_ROOT, "vendor", "compat-public");
const SITE_ORIGIN = normalizeOrigin(process.env.SITE_ORIGIN ?? "https://lab-stage.verdify.ai");
const STAGE_GLOBAL_NOINDEX = process.env.STAGE_GLOBAL_NOINDEX !== "false";

function sameOccurrenceStoreLocation(left, right) {
  return left?.kind === right.kind
    && (right.kind === "local"
      ? left.root === right.root
      : left.bucket === right.bucket && left.prefix === right.prefix);
}

async function createCompilerOccurrenceStore(location, occurrenceStoreFactory) {
  const expectedLocation = parseOccurrenceReleaseStoreLocation(location);
  let store;
  if (occurrenceStoreFactory === null) {
    if (expectedLocation.kind !== "local") {
      throw new Error("compiler S3 occurrence access requires an explicitly injected store adapter");
    }
    store = createOccurrenceReleaseStore(location);
  } else {
    if (typeof occurrenceStoreFactory !== "function") {
      throw new Error("compiler occurrence store factory is invalid");
    }
    store = await occurrenceStoreFactory(location);
  }
  if (!(store instanceof OccurrenceReleaseStore)) {
    throw new Error("compiler occurrence store factory did not return an OccurrenceReleaseStore");
  }
  if (!sameOccurrenceStoreLocation(store.location, expectedLocation)) {
    throw new Error("compiler occurrence store adapter does not match the requested location");
  }
  return store;
}

function normalizeOrigin(value) {
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("SITE_ORIGIN must be one credential-free HTTPS origin");
  }
  parsed.pathname = "";
  return parsed.toString().replace(/\/$/, "");
}

async function loadCompilerOccurrenceBinding({
  snapshot,
  occurrenceStore = process.env.LAB_OCCURRENCE_STORE,
  occurrencePolicy = process.env.LAB_OCCURRENCE_POLICY,
  occurrenceStoreFactory = null,
}) {
  if (!occurrenceStore) {
    return {
      release: { selection: null, current: null },
      policy: null,
      policySha256: null,
    };
  }
  if (!occurrencePolicy) {
    throw new Error("LAB_OCCURRENCE_POLICY must name the exact policy when LAB_OCCURRENCE_STORE is set");
  }

  const policyValue = await readCanonicalExportDocument(occurrencePolicy, "occurrence export policy");
  const policy = validateOccurrenceExportPolicy(policyValue.document);
  const policySha256 = occurrenceExportPolicySha256(policy);
  if (policySha256 !== policyValue.sha256) {
    throw new Error("occurrence export policy digest does not match its canonical bytes");
  }

  const snapshotMatch = /^sha256:([0-9a-f]{64})$/u.exec(snapshot.manifestDigest ?? "");
  if (
    !snapshotMatch
    || policy.sourceSnapshotManifestSha256 !== snapshotMatch[1]
  ) {
    throw new Error("occurrence export policy does not match the exact snapshot manifest");
  }
  if (policy.activation.state !== "approved") {
    throw new Error("selected occurrence release policy is not approved for compiler use");
  }

  // The policy is the authority boundary. Only after its closed shape,
  // canonical bytes, approval, and snapshot binding are proven may a lazy
  // adapter construct or invoke a client. Keep this exact store instance for
  // both selector reads and later blob materialization.
  const store = await createCompilerOccurrenceStore(occurrenceStore, occurrenceStoreFactory);
  const release = await loadSelectedOccurrenceRelease(store);
  if (release.current !== null) {
    if (
      release.current.sourceSnapshotManifestSha256 !== snapshotMatch[1]
      || release.current.sourceSnapshotManifestSha256 !== policy.sourceSnapshotManifestSha256
    ) {
      throw new Error("selected occurrence release does not match the exact snapshot manifest");
    }
    if (release.current.policyVersion !== policy.policyVersion) {
      throw new Error("selected occurrence release does not match the occurrence export policy version");
    }
    if (release.current.policySha256 !== policySha256) {
      throw new Error("selected occurrence release does not match the exact occurrence export policy bytes");
    }
  }
  return { release, policy, policySha256, store };
}

function verifyCompilerOccurrenceDiscovery(binding, discoveryManifest) {
  if (binding.release.current === null) return;
  const discoverySha256 = staticOccurrenceDiscoverySha256(discoveryManifest);
  if (binding.policy.sourceOccurrenceManifestSha256 !== discoverySha256) {
    throw new Error("selected occurrence release policy does not match the stable discovery manifest");
  }
  validatePolicyManifestBinding(binding.policy, discoveryManifest, discoverySha256);
}

function selectedOccurrenceDiscovery(kind, occurrence) {
  if (kind === "graph") {
    return {
      occurrenceId: occurrence.occurrenceId,
      route: occurrence.route,
      ordinal: occurrence.ordinal,
      semanticRole: occurrence.semanticRole,
      uid: occurrence.uid,
      panelId: occurrence.panelId,
      query: occurrence.query,
      variables: occurrence.variables,
      timeRange: occurrence.timeRange,
      liveUrl: occurrence.liveUrl,
      renderCadenceSeconds: occurrence.renderCadenceSeconds,
    };
  }
  return {
    occurrenceId: occurrence.occurrenceId,
    route: occurrence.route,
    ordinal: occurrence.ordinal,
    classification: occurrence.classification,
    semanticRole: occurrence.semanticRole,
    sourceProvenanceSha256: occurrence.sourceProvenanceSha256,
    stableTarget: occurrence.stableTarget,
    captureCadenceSeconds: occurrence.captureCadenceSeconds,
  };
}

function verifyCompleteSelectedOccurrenceEvidence(release, occurrenceManifest, policy, policySha256) {
  if (release.current === null) return;
  if (occurrenceManifest.selectedManifestSha256 !== release.selection.current.manifestSha256) {
    throw new Error("selected occurrence release identity differs from the static occurrence manifest");
  }
  for (const [kind, released, discovered, approved, bounds] of [
    ["graph", release.current.occurrences.graphs, occurrenceManifest.graphs, policy.graphs, policy.imagePolicy.graphs],
    [
      "current-media",
      release.current.occurrences.currentMedia,
      occurrenceManifest.currentMedia,
      policy.currentMedia,
      policy.imagePolicy.currentMedia,
    ],
  ]) {
    const discoveredById = new Map(discovered.map((occurrence) => [occurrence.occurrenceId, occurrence]));
    const approvedById = new Map(approved.map((occurrence) => [occurrence.occurrenceId, occurrence]));
    if (released.length !== discovered.length || approved.length !== discovered.length) {
      throw new Error(`selected occurrence release lacks complete ${kind} fallback coverage`);
    }
    for (const occurrence of released) {
      const served = discoveredById.get(occurrence.occurrenceId);
      const approval = approvedById.get(occurrence.occurrenceId);
      const selectedDiscovery = selectedOccurrenceDiscovery(kind, occurrence);
      const discoverySha256 = createHash("sha256")
        .update(`${JSON.stringify(selectedDiscovery, null, 2)}\n`)
        .digest("hex");
      if (
        !served
        || !approval
        || !occurrence.fallback
        || JSON.stringify(selectedOccurrenceDiscovery(kind, served)) !== JSON.stringify(selectedDiscovery)
        || JSON.stringify(served.selected) !== JSON.stringify(occurrence)
        || approval.occurrenceSha256 !== discoverySha256
      ) {
        throw new Error(`selected occurrence release lacks exact approved ${kind} fallback coverage`);
      }
      if (
        kind === "current-media"
        && (
          occurrence.policySha256 !== policySha256
          || occurrence.requestProvenanceSha256 !== approval.requestProvenanceSha256
        )
      ) {
        throw new Error("selected current-media request provenance differs from the approved occurrence policy");
      }
      const fallback = occurrence.fallback;
      if (
        fallback.mediaType !== policy.imagePolicy.mediaType
        || fallback.width < bounds.minWidth
        || fallback.width > bounds.maxWidth
        || fallback.height < bounds.minHeight
        || fallback.height > bounds.maxHeight
        || fallback.bytes > bounds.maxBytes
      ) {
        throw new Error(`selected ${kind} fallback violates the approved image bounds`);
      }
    }
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function inferredDescription(tree, { descriptionLength = 150, maxDescriptionLength = 300 } = {}) {
  const pending = [tree];
  const text = [];
  while (pending.length > 0) {
    const node = pending.pop();
    if (node?.type === "text" && typeof node.value === "string") text.push(node.value);
    if (Array.isArray(node?.children)) pending.push(...node.children.toReversed());
  }
  const normalized = text.join("").replace(/\s+/gu, " ").trim();
  const sentences = normalized.split(/\.\s/u);
  let description = "";
  for (const sentence of sentences) {
    if (!sentence) break;
    const current = sentence.endsWith(".") ? sentence : `${sentence}.`;
    const nextLength = description.length + current.length + (description ? 1 : 0);
    if (nextLength > descriptionLength && description) break;
    description += `${description ? " " : ""}${current}`;
  }
  return description.length > maxDescriptionLength
    ? `${description.slice(0, maxDescriptionLength)}...`
    : description;
}

async function verifyCompatAssets() {
  const manifestBytes = await readFile(path.join(COMPAT_PUBLIC_ROOT, "manifest.json"));
  let manifest;
  try {
    manifest = JSON.parse(manifestBytes.toString("utf8"));
  } catch {
    throw new Error("legacy public asset manifest is invalid JSON");
  }
  if (
    !manifest
    || Object.getPrototypeOf(manifest) !== Object.prototype
    || Object.keys(manifest).sort().join("\n") !== ["contract", "files", "schemaVersion", "sourceBaselineSha256"].join("\n")
    || manifest.contract !== "verdify.lab-legacy-public-assets"
    || manifest.schemaVersion !== 1
    || !/^[0-9a-f]{64}$/.test(manifest.sourceBaselineSha256)
    || !manifest.files
    || Object.getPrototypeOf(manifest.files) !== Object.prototype
  ) {
    throw new Error("legacy public asset manifest violates its closed contract");
  }
  const inventory = [];
  const pending = [[COMPAT_PUBLIC_ROOT, ""]];
  while (pending.length > 0) {
    const [directory, prefix] = pending.pop();
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) pending.push([absolute, relative]);
      else if (entry.isFile() && relative !== "manifest.json") inventory.push(relative);
      else if (relative !== "manifest.json") throw new Error("legacy public asset tree contains a special file");
    }
  }
  const declared = Object.keys(manifest.files).sort();
  if (inventory.sort().join("\n") !== declared.join("\n")) {
    throw new Error("legacy public asset inventory differs from its closed manifest");
  }
  const verified = [];
  for (const relative of declared) {
    if (
      !/^[A-Za-z0-9._/-]+$/.test(relative)
      || relative.startsWith("/")
      || relative.split("/").includes("..")
      || !/^[0-9a-f]{64}$/.test(manifest.files[relative])
    ) {
      throw new Error("legacy public asset manifest contains an unsafe entry");
    }
    const absolute = path.join(COMPAT_PUBLIC_ROOT, ...relative.split("/"));
    const stat = await lstat(absolute, { bigint: true });
    if (!stat.isFile() || stat.nlink !== 1n || stat.size > 10n * 1024n * 1024n) {
      throw new Error("legacy public asset is not a bounded unaliased regular file");
    }
    const bytes = await readFile(absolute);
    if (createHash("sha256").update(bytes).digest("hex") !== manifest.files[relative]) {
      throw new Error("legacy public asset digest mismatch");
    }
    verified.push({ relative, bytes });
  }
  return verified;
}

async function materializeCompatAssets() {
  for (const { relative, bytes } of await verifyCompatAssets()) {
    const destination = path.join(PUBLIC_ROOT, ...relative.split("/"));
    await mkdir(path.dirname(destination), { recursive: true });
    await writeFile(destination, bytes, { flag: "wx", mode: 0o644 });
  }
}

function normalizeRoute(value) {
  if (typeof value !== "string") throw new Error("route must be a string");
  let route = value.trim().replaceAll("\\", "/").replace(/^https?:\/\/[^/]+/i, "");
  route = `/${route}`.replace(/\/+/g, "/").replace(/\/$/, "");
  if (route === "") return "/";
  const decoded = decodeURIComponent(route);
  if (decoded.split("/").includes("..") || route.includes("?") || route.includes("#")) {
    throw new Error(`unsafe route: ${value}`);
  }
  return route;
}

function routeFromSource(relative) {
  const withoutExtension = relative.slice(0, -3);
  if (withoutExtension === "index") return { route: "/", kind: "root", physicalPath: "index.html" };
  if (withoutExtension.endsWith("/index")) {
    const route = normalizeRoute(withoutExtension.slice(0, -6));
    return { route, kind: "folder", physicalPath: `${route.slice(1)}/index.html` };
  }
  const route = normalizeRoute(withoutExtension);
  return { route, kind: "page", physicalPath: `${route.slice(1)}.html` };
}

function canonicalPath(record) {
  if (record.route === "/") return "/";
  return record.kind === "folder" ? `${record.route}/` : record.route;
}

function splitFrontmatter(source, relative) {
  if (!source.startsWith("---\n") && !source.startsWith("---\r\n")) return [{}, source];
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n/.exec(source);
  if (!match) throw new Error(`unterminated frontmatter: ${relative}`);
  let data;
  try {
    data = YAML.parse(match[1], { maxAliasCount: 0, uniqueKeys: true });
  } catch {
    throw new Error(`invalid YAML frontmatter: ${relative}`);
  }
  if (data === null) data = {};
  if (Object.getPrototypeOf(data) !== Object.prototype) throw new Error(`frontmatter must be an object: ${relative}`);
  return [data, source.slice(match[0].length)];
}

function stringList(value) {
  if (value === undefined || value === null || value === "") return [];
  const values = Array.isArray(value) ? value : [value];
  return values.map((item) => String(item).trim()).filter(Boolean);
}

function titleKey(value) {
  return String(value).trim().toLocaleLowerCase("en-US").replace(/\.md$/i, "");
}

function tagSlug(value) {
  return titleKey(value).replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function displayDate(value) {
  if (!value) return "";
  const parsed = new Date(`${String(value).slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(parsed.valueOf())) return "";
  return new Intl.DateTimeFormat("en-US", {
    day: "numeric",
    month: "long",
    timeZone: "UTC",
    year: "numeric",
  }).format(parsed);
}

function socialImagePath(value, assetPaths, source) {
  if (value === undefined || value === null || value === "") return "";
  if (typeof value !== "string") throw new Error(`socialImage must be a local path: ${source}`);
  const candidate = value.trim();
  if (
    !candidate.startsWith("/")
    || candidate.startsWith("//")
    || candidate.includes("\\")
    || /[?#\u0000-\u001f\u007f]/.test(candidate)
  ) {
    throw new Error(`socialImage must be a plain same-origin path: ${source}`);
  }
  if (!assetPaths.has(candidate.slice(1))) throw new Error(`socialImage target is absent: ${source}`);
  return candidate;
}

function buildLinkIndex(sources) {
  const candidates = new Map();
  function add(key, route) {
    const normalized = titleKey(key);
    if (!normalized) return;
    const routes = candidates.get(normalized) ?? new Set();
    routes.add(route);
    candidates.set(normalized, routes);
  }
  for (const source of sources) {
    add(source.relative, source.route);
    add(source.relative.slice(0, -3), source.route);
    add(path.posix.basename(source.relative, ".md"), source.route);
    if (path.posix.basename(source.relative) === "index.md") add(path.posix.dirname(source.relative), source.route);
    add(source.frontmatter.title ?? "", source.route);
  }
  return candidates;
}

function resolveWikiTarget(rawTarget, source, linkIndex) {
  const [targetPath, fragment = ""] = rawTarget.split("#", 2);
  const normalizedTarget = targetPath.replace(/\.md$/i, "").replace(/\/$/, "");
  const relativeCandidate = path.posix.normalize(path.posix.join(path.posix.dirname(source.relative), normalizedTarget));
  const keys = [normalizedTarget, relativeCandidate, path.posix.basename(normalizedTarget)];
  for (const key of keys) {
    const routes = linkIndex.get(titleKey(key));
    if (routes?.size === 1) {
      const route = [...routes][0];
      return `${route}${fragment ? `#${encodeURIComponent(fragment.toLocaleLowerCase("en-US").replaceAll(" ", "-"))}` : ""}`;
    }
  }
  throw new Error(`unresolved or ambiguous wikilink in ${source.relative}`);
}

function expandWikilinks(markdown, source, linkIndex) {
  return markdown.replace(/(!?)\[\[([^\]]+)\]\]/g, (_match, embed, body) => {
    const [target, label] = body.split("|", 2);
    const href = resolveWikiTarget(target.trim(), source, linkIndex);
    const text = (label ?? target).trim();
    return embed ? `![${text}](${href})` : `[${text}](${href})`;
  });
}

function remarkRewriteLocalLinks(source, routeBySource) {
  return () => (tree) => {
    visit(tree, "link", (node) => {
      if (typeof node.url !== "string" || /^(?:[a-z]+:|\/|#)/i.test(node.url)) return;
      const [pathname, suffix = ""] = node.url.split(/(?=[?#])/u, 2);
      if (!pathname.endsWith(".md")) return;
      const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(source.relative), pathname));
      const route = routeBySource.get(resolved);
      if (!route) throw new Error(`missing Markdown link target ${pathname} in ${source.relative}`);
      node.url = `${route}${suffix}`;
    });
  };
}

function rehypeRewriteRelativeReferences(source, routeBySource, assetPaths) {
  return () => (tree) => {
    visit(tree, "element", (node) => {
      const property = node.tagName === "a" ? "href" : ["img", "source", "video"].includes(node.tagName) ? "src" : null;
      if (!property) return;
      const raw = node.properties?.[property];
      if (typeof raw !== "string" || /^(?:[a-z]+:|\/|#)/i.test(raw)) return;
      const match = /^([^?#]*)([?#].*)?$/u.exec(raw);
      const pathname = match?.[1] ?? "";
      const suffix = match?.[2] ?? "";
      if (!pathname) return;
      const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(source.relative), pathname));
      const sourceCandidates = pathname.endsWith(".md")
        ? [resolved]
        : [resolved, `${resolved}.md`, `${resolved.replace(/\/$/, "")}/index.md`];
      for (const candidate of sourceCandidates) {
        const route = routeBySource.get(candidate);
        if (route) {
          node.properties[property] = `${route}${suffix}`;
          return;
        }
      }
      if (assetPaths.has(resolved)) node.properties[property] = `/${resolved}${suffix}`;
    });
  };
}

function rehypeUnavailableLocalReferences(assetPaths, availableRoutes, unavailable) {
  return () => (tree) => {
    visit(tree, "element", (node) => {
      const property = node.tagName === "a" ? "href" : node.tagName === "img" ? "src" : null;
      if (!property) return;
      const raw = node.properties?.[property];
      if (typeof raw !== "string" || raw.startsWith("#")) return;
      let parsed;
      try {
        parsed = new URL(raw, SITE_ORIGIN);
      } catch {
        return;
      }
      if (parsed.origin !== SITE_ORIGIN) return;
      const pathname = decodeURIComponent(parsed.pathname);
      const relative = pathname.replace(/^\/+/, "").replace(/\/$/, "");
      const route = normalizeRoute(pathname);
      if (availableRoutes.has(route) || assetPaths.has(relative)) return;

      const kind = node.tagName === "img" ? "image" : "link";
      unavailable.push({ kind, path: pathname });
      if (kind === "image") {
        const alt = typeof node.properties?.alt === "string" ? node.properties.alt : "Evidence image";
        node.tagName = "span";
        node.properties = {
          className: ["media-unavailable"],
          role: "img",
          ariaLabel: `${alt} — image unavailable in this publication`,
        };
        // Preserve a rendered boundary before an adjacent inline timestamp or
        // caption. Generated crop cards place <strong> immediately after the
        // image, and omitting this trailing space would merge publication and
        // date into one semantic token.
        node.children = [{ type: "text", value: `${alt} — image unavailable in this publication. ` }];
        return;
      }
      node.tagName = "span";
      node.properties = {
        className: ["unavailable-reference"],
        title: "This evidence route is unavailable in this publication.",
      };
    });
  };
}

function grafanaRenderUrl(liveUrl) {
  const parsed = new URL(liveUrl);
  parsed.pathname = parsed.pathname.replace(/^\/d-solo\//, "/render/d-solo/");
  return parsed.toString();
}

function imageDimensions(buffer, relative = "") {
  if (buffer.length >= 24 && buffer.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) {
    return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
  }
  if (buffer.length >= 4 && buffer[0] === 0xff && buffer[1] === 0xd8) {
    const startOfFrame = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
    let offset = 2;
    while (offset + 8 < buffer.length) {
      while (offset < buffer.length && buffer[offset] !== 0xff) offset += 1;
      while (offset < buffer.length && buffer[offset] === 0xff) offset += 1;
      if (offset >= buffer.length) break;
      const marker = buffer[offset];
      offset += 1;
      if (marker === 0xd8 || marker === 0x01) continue;
      if (marker === 0xd9 || marker === 0xda || offset + 2 > buffer.length) break;
      const length = buffer.readUInt16BE(offset);
      if (length < 2 || offset + length > buffer.length) break;
      if (startOfFrame.has(marker) && length >= 7) {
        return { width: buffer.readUInt16BE(offset + 5), height: buffer.readUInt16BE(offset + 3) };
      }
      offset += length;
    }
  }
  if (/\.svg$/i.test(relative)) {
    const source = buffer.toString("utf8", 0, Math.min(buffer.length, 128 * 1024));
    const open = /<svg\b[^>]*>/i.exec(source)?.[0] ?? "";
    const width = /\bwidth=["']([0-9.]+)(?:px)?["']/i.exec(open)?.[1];
    const height = /\bheight=["']([0-9.]+)(?:px)?["']/i.exec(open)?.[1];
    if (width && height) return { width: Math.round(Number(width)), height: Math.round(Number(height)) };
    const viewBox = /\bviewBox=["']\s*[-0-9.]+\s+[-0-9.]+\s+([0-9.]+)\s+([0-9.]+)\s*["']/i.exec(open);
    if (viewBox) return { width: Math.round(Number(viewBox[1])), height: Math.round(Number(viewBox[2])) };
  }
  return null;
}

async function buildResponsiveImage(entry, relative, sourceDigest) {
  if (!/\.(?:jpe?g|png)$/i.test(relative)) return null;
  const metadata = await sharp(entry.absolute, { failOn: "warning", limitInputPixels: 80_000_000 }).metadata();
  if (!metadata.width || !metadata.height) return null;
  const rotated = [5, 6, 7, 8].includes(metadata.orientation ?? 1);
  const width = rotated ? metadata.height : metadata.width;
  const height = rotated ? metadata.width : metadata.height;
  const variants = [];
  for (const targetWidth of [480, 960, 1440]) {
    if (targetWidth >= width) continue;
    const relativeOutput = `_media/${sourceDigest.slice(0, 24)}-${targetWidth}.webp`;
    const destination = path.join(PUBLIC_ROOT, ...relativeOutput.split("/"));
    await mkdir(path.dirname(destination), { recursive: true });
    await sharp(entry.absolute, { failOn: "warning", limitInputPixels: 80_000_000 })
      .rotate()
      .resize({ width: targetWidth, withoutEnlargement: true })
      .webp({ quality: 82, effort: 5 })
      .toFile(destination);
    const output = await readFile(destination);
    variants.push({
      path: relativeOutput,
      width: targetWidth,
      height: Math.max(1, Math.round((height / width) * targetWidth)),
      bytes: output.length,
      sha256: createHash("sha256").update(output).digest("hex"),
      generatedFrom: relative,
    });
  }
  return { width, height, variants };
}

function rehypeImageMetadata(metadata) {
  return () => (tree) => {
    visit(tree, "element", (node) => {
      if (node.tagName !== "img") return;
      const rawSource = node.properties?.src;
      if (typeof rawSource !== "string") return;
      let pathname;
      try {
        const parsed = new URL(rawSource, SITE_ORIGIN);
        if (parsed.origin !== SITE_ORIGIN) {
          if ((node.properties?.className ?? []).includes("camera-snapshot")) {
            node.properties.width ??= 704;
            node.properties.height ??= 480;
          }
          return;
        }
        pathname = decodeURIComponent(parsed.pathname).replace(/^\//, "");
      } catch {
        return;
      }
      const dimensions = metadata.get(pathname);
      if (!dimensions) return;
      node.properties.width ??= dimensions.width;
      node.properties.height ??= dimensions.height;
      node.properties.decoding ??= "async";
      node.properties.loading ??= "lazy";
      node.properties.sizes ??= "(max-width: 620px) calc(100vw - 2rem), (max-width: 860px) 50vw, 806px";
      const candidates = [
        ...(dimensions.variants ?? []).map((variant) => ({ source: `/${variant.path}`, width: variant.width })),
        { source: `/${pathname}`, width: dimensions.width },
      ];
      if (pathname.startsWith("static/photos/") && !pathname.startsWith("static/photos/full/")) {
        const fullPath = `static/photos/full/${path.posix.basename(pathname)}`;
        const fullDimensions = metadata.get(fullPath);
        if (fullDimensions && fullDimensions.width > dimensions.width) {
          candidates.push(
            ...(fullDimensions.variants ?? []).map((variant) => ({ source: `/${variant.path}`, width: variant.width })),
            { source: `/${fullPath}`, width: fullDimensions.width },
          );
        }
      }
      const byWidth = new Map();
      for (const candidate of candidates.sort((left, right) => left.width - right.width)) {
        byWidth.set(candidate.width, candidate);
      }
      if (byWidth.size > 1) {
        node.properties.srcSet = [...byWidth.values()].map((candidate) => `${candidate.source} ${candidate.width}w`).join(", ");
      }
    });
  };
}

function rehypeMediaLoadingPolicy() {
  return () => (tree) => {
    visit(tree, "element", (node) => {
      if (node.tagName !== "video" || node.properties?.autoPlay) return;
      node.properties ??= {};
      node.properties.preload = "none";
    });
  };
}

function cameraSnapshotAsset(rawSource, snapshotFiles) {
  if (typeof rawSource !== "string") return null;
  let parsed;
  try {
    parsed = new URL(rawSource);
  } catch {
    return null;
  }
  if (
    parsed.protocol !== "https:"
    || parsed.hostname !== "api.verdify.ai"
    || parsed.username
    || parsed.password
  ) return null;
  const match = /^\/api\/v1\/public\/cameras\/([a-z0-9_-]+)\/latest\.jpg$/.exec(parsed.pathname);
  if (!match) return null;
  const relative = `static/cameras/${match[1]}/latest.jpg`;
  return {
    sourceUrl: parsed.toString(),
    relative,
    publicPath: `/${relative}`,
    available: snapshotFiles.has(relative),
  };
}

function rehypeCameraSnapshots(snapshotFiles, occurrences) {
  return () => (tree) => {
    visit(tree, "element", (node, index, parent) => {
      if (!parent || index === undefined) return;
      if (node.tagName === "script" && node.properties?.src === "/static/camera-refresh.js") {
        parent.children.splice(index, 1);
        return [SKIP, index];
      }
      if (node.tagName !== "img") return;
      const candidate = cameraSnapshotAsset(node.properties?.dataCameraSrc ?? node.properties?.src, snapshotFiles);
      if (!candidate) return;
      occurrences.push(candidate);
      const alt = typeof node.properties?.alt === "string" ? node.properties.alt : "Greenhouse camera snapshot";
      if (candidate.available) {
        node.properties.src = candidate.publicPath;
        node.properties.dataCameraLocalSrc = candidate.publicPath;
        node.properties.dataCameraState = "last-known-good";
        delete node.properties.dataCameraSrc;
        if (parent.tagName === "a") parent.properties.href = candidate.publicPath;
        return;
      }
      parent.children[index] = {
        type: "element",
        tagName: "div",
        properties: {
          className: ["camera-snapshot", "camera-snapshot--unavailable"],
          role: "img",
          ariaLabel: alt,
          dataCameraState: "unavailable",
        },
        children: [{
          type: "text",
          value: "A verified same-origin camera snapshot was not included in this publication.",
        }],
      };
      if (parent.tagName === "a") {
        parent.properties.target = "_blank";
        parent.properties.rel = ["noopener", "noreferrer"];
        parent.properties.ariaLabel = "Open the latest camera snapshot at the public API";
      }
    });
  };
}

function rehypeSpecialistEvidence({ route, grafanaOccurrences, currentMediaOccurrences, selectedOccurrenceState }) {
  return (tree) => {
    let graphOrdinal = 0;
    let mediaOrdinal = 0;
    visit(tree, "element", (node, index, parent) => {
      if (node.tagName === "script" && node.properties?.src === "/static/camera-refresh.js" && parent && index !== undefined) {
        parent.children.splice(index, 1);
        return [SKIP, index];
      }
      if (node.tagName === "img") {
        const rawSource = node.properties?.src;
        if (typeof rawSource !== "string") return;
        const discovered = discoverCurrentMediaOccurrence({
          route,
          ordinal: mediaOrdinal,
          sourceUrl: rawSource,
          semanticRole: typeof node.properties?.alt === "string" ? node.properties.alt : "Current greenhouse camera",
        });
        mediaOrdinal += 1;
        if (discovered === null) return;
        currentMediaOccurrences.push(discovered);
        const selectedCandidate = selectedOccurrenceState.currentMedia.get(discovered.occurrenceId);
        const selected = selectedCandidate?.sourceProvenanceSha256 === discovered.sourceProvenanceSha256
          ? selectedCandidate
          : null;
        if (selected?.fallback) {
          node.properties.src = selected.fallback.publicPath;
          node.properties.width = selected.fallback.width;
          node.properties.height = selected.fallback.height;
          node.properties.loading = "eager";
          node.properties.decoding = "async";
          node.properties["data-occurrence-id"] = discovered.occurrenceId;
          node.properties["data-current-media-target"] = discovered.stableTarget;
          node.properties["data-fallback-verified-at"] = selected.fallback.verifiedAt;
          if (parent?.tagName === "a") parent.properties.href = selected.fallback.publicPath;
        } else {
          if (!parent || index === undefined) throw new Error("current media occurrence has no render parent");
          if (parent.tagName === "a") {
            parent.tagName = "div";
            parent.properties = { className: ["current-media-evidence__wrapper"] };
          }
          parent.children[index] = {
            type: "element",
            tagName: "figure",
            properties: {
              className: ["current-media-evidence", "current-media-evidence--pending"],
              "data-occurrence-id": discovered.occurrenceId,
              "data-current-media-target": discovered.stableTarget,
            },
            children: [
              {
                type: "element",
                tagName: "figcaption",
                properties: { className: ["current-media-evidence__status"] },
                children: [{ type: "text", value: "Verified local camera fallback is pending." }],
              },
            ],
          };
        }
        return;
      }
      if (node.tagName !== "iframe" || !parent || index === undefined) return;
      const rawSource = node.properties?.src;
      if (typeof rawSource !== "string") return;
      let parsed;
      try {
        parsed = new URL(rawSource);
      } catch {
        return;
      }
      if (parsed.protocol !== "https:" || parsed.hostname !== "graphs.verdify.ai") return;
      if (!/^\/(?:d-solo|d)\//.test(parsed.pathname) || parsed.username || parsed.password) {
        throw new Error("unsupported Grafana occurrence URL");
      }
      const liveUrl = parsed.toString();
      const title = typeof node.properties?.title === "string" ? node.properties.title : "Greenhouse evidence graph";
      const discovered = discoverGraphOccurrence({ route, ordinal: graphOrdinal, liveUrl, title });
      graphOrdinal += 1;
      const selected = selectedOccurrenceState.graphs.get(discovered.occurrenceId);
      grafanaOccurrences.push(discovered);
      const fallback = selected?.fallback;
      const status = fallback
        ? `${selected.state === "retained-last-known-good" ? "Last-known-good" : "Verified"} graph fallback · ${fallback.verifiedAt}`
        : "Verified local graph fallback is pending for this stage snapshot.";
      const children = [];
      if (fallback) {
        children.push({
          type: "element",
          tagName: "img",
          properties: {
            className: ["grafana-evidence__image"],
            src: fallback.publicPath,
            alt: title,
            width: fallback.width,
            height: fallback.height,
            // Selected evidence is already same-origin, immutable, and bounded.
            // Load it automatically in every browser instead of delegating
            // visibility to browser-specific native lazy-load heuristics.
            loading: "eager",
            decoding: "async",
          },
          children: [],
        });
      }
      children.push(
        {
          type: "element",
          tagName: "figcaption",
          properties: { className: ["grafana-evidence__status"] },
          children: [{ type: "text", value: status }],
        },
        {
          type: "element",
          tagName: "a",
          properties: { href: selected?.liveUrl ?? liveUrl, rel: ["noopener", "noreferrer"], target: "_blank" },
          children: [{ type: "text", value: "Open interactive graph" }],
        },
      );
      parent.children[index] = {
        type: "element",
        tagName: "figure",
        properties: {
          className: ["grafana-evidence"],
          "data-occurrence-id": discovered.occurrenceId,
          "data-iframe-src": liveUrl,
          "data-live-src": liveUrl,
          ...(fallback ? { "data-image-src": fallback.publicPath, "data-image-sha256": fallback.sha256 } : {}),
          "data-title": title,
        },
        children,
      };
    });
  };
}

async function renderMarkdown(
  markdown,
  source,
  linkIndex,
  routeBySource,
  assetPaths,
  imageMetadata,
  snapshotFiles,
  availableRoutes = new Set(routeBySource.values()),
  selectedOccurrenceState = { graphs: new Map(), currentMedia: new Map() },
) {
  const grafanaOccurrences = [];
  const currentMediaOccurrences = [];
  const unavailable = [];
  const processor = unified()
    .use(remarkParse)
    .use(remarkGfm, { singleTilde: false })
    .use(remarkMath)
    .use(remarkRewriteLocalLinks(source, routeBySource))
    .use(remarkRehype, { allowDangerousHtml: true })
    .use(rehypeRaw)
    .use(rehypeMediaLoadingPolicy())
    .use(rehypeRewriteRelativeReferences(source, routeBySource, assetPaths))
    .use(rehypeUnavailableLocalReferences(assetPaths, availableRoutes, unavailable))
    .use(rehypeSlug)
    .use(rehypeKatex)
    .use(rehypeSpecialistEvidence, {
      route: source.route,
      grafanaOccurrences,
      currentMediaOccurrences,
      selectedOccurrenceState,
    })
    .use(rehypeImageMetadata(imageMetadata));
  const tree = processor.parse(expandWikilinks(markdown, source, linkIndex));
  const result = await processor.run(tree);
  return {
    html: toHtml(result, { allowDangerousHtml: true }),
    description: inferredDescription(result),
    grafana: grafanaOccurrences,
    currentMedia: currentMediaOccurrences,
    unavailable,
  };
}

function aliasRecords(sourceRecords) {
  const sourceRoutes = new Set(sourceRecords.map((record) => record.route));
  const aliases = new Map();
  const rollingPlanDate = (record) => /^\/plans\/(\d{4}-\d{2}-\d{2})$/.exec(record.route)?.[1] ?? null;
  const suppressedLatest = [];
  for (const record of sourceRecords) {
    for (const value of record.aliases) {
      const route = normalizeRoute(value);
      if (route === record.route) throw new Error(`self alias on ${record.route}`);
      if (sourceRoutes.has(route)) throw new Error(`alias collides with a source route: ${route}`);
      if (route === "/plans/latest") {
        if (!rollingPlanDate(record)) throw new Error("rolling-plan alias must be declared only by a valid dated plan");
        suppressedLatest.push(record);
        continue;
      }
      const candidate = {
        route,
        canonicalPath: route,
        canonicalUrl: `${SITE_ORIGIN}${route}`,
        physicalPath: `${route.slice(1)}.html`,
        kind: "alias",
        source: record.source,
        title: `Moved: ${record.title}`,
        description: `This route moved to ${record.canonicalPath}.`,
        html: "",
        aliases: [],
        tags: [],
        cssclasses: [],
        noindex: true,
        target: record.canonicalPath,
        grafana: [],
        cameras: [],
        currentMedia: [],
        unavailable: [],
        date: record.date,
        socialImage: record.socialImage,
      };
      if (aliases.has(route)) throw new Error(`duplicate alias is not an approved rolling-plan alias: ${route}`);
      aliases.set(route, candidate);
    }
  }
  const datedPlans = sourceRecords
    .filter((record) => rollingPlanDate(record))
    .sort((left, right) => rollingPlanDate(right).localeCompare(rollingPlanDate(left)));
  const latest = datedPlans[0];
  if (latest) {
    aliases.set("/plans/latest", {
      route: "/plans/latest",
      canonicalPath: "/plans/latest",
      canonicalUrl: `${SITE_ORIGIN}/plans/latest`,
      physicalPath: "plans/latest.html",
      kind: "alias",
      source: "generated:rolling-plan-latest",
      title: `Latest plan: ${latest.title}`,
      description: `This route resolves to ${latest.canonicalPath}.`,
      html: "",
      aliases: [],
      tags: [],
      cssclasses: [],
      noindex: true,
      target: latest.canonicalPath,
      grafana: [],
      cameras: [],
      currentMedia: [],
      unavailable: [],
      date: latest.date,
      socialImage: latest.socialImage,
    });
  }
  return {
    aliases: [...aliases.values()],
    compatibility: {
      contract: "verdify.rolling-plan-latest/v1",
      route: "/plans/latest",
      target: latest?.canonicalPath ?? null,
      selectedSource: latest?.source ?? null,
      suppressedDeclarationCount: suppressedLatest.length,
      suppressedSources: suppressedLatest.map((record) => record.source).sort(),
    },
  };
}

function tagRecords(sourceRecords, occupied) {
  const tags = new Map();
  for (const record of sourceRecords) {
    for (const tag of record.tags) {
      const slug = tagSlug(tag);
      if (!slug) continue;
      const entries = tags.get(slug) ?? { label: tag, records: [] };
      entries.records.push(record);
      tags.set(slug, entries);
    }
  }
  const card = (record, includeTopics) => {
    const date = displayDate(record.date);
    const topics = includeTopics
      ? record.tags.map((tag) => `<a href="/tags/${tagSlug(tag)}">${escapeHtml(tag)}</a>`).join(" ")
      : "";
    return `<li>${date ? `<time datetime="${escapeHtml(record.date)}">${escapeHtml(date)}</time>` : ""}<h3><a href="${escapeHtml(record.canonicalPath)}">${escapeHtml(record.title)}</a></h3>${topics ? `<p class="tag-card__topics">${topics}</p>` : ""}</li>`;
  };
  const sortedGroups = [...tags].sort();
  const records = [];
  for (const [slug, group] of sortedGroups) {
    const route = `/tags/${slug}`;
    if (occupied.has(route)) throw new Error(`generated tag route collides: ${route}`);
    occupied.add(route);
    const cards = group.records
      .sort((left, right) => String(right.date).localeCompare(String(left.date)) || left.title.localeCompare(right.title))
      .map((record) => card(record, true))
      .join("");
    records.push({
      route,
      canonicalPath: route,
      canonicalUrl: `${SITE_ORIGIN}${route}`,
      physicalPath: `tags/${slug}.html`,
      kind: "tag",
      source: "generated:tags",
      title: `Tag: ${group.label}`,
      description: `Verdify Lab pages tagged ${group.label}.`,
      html: `<h1>Tag: ${escapeHtml(group.label)}</h1><p>${group.records.length} ${group.records.length === 1 ? "item" : "items"} with this tag.</p><ul class="tag-card-list">${cards}</ul>`,
      aliases: [],
      tags: [],
      cssclasses: [],
      noindex: true,
      target: "",
      grafana: [],
      cameras: [],
      currentMedia: [],
      unavailable: [],
      date: "",
      socialImage: "",
    });
  }
  const groups = sortedGroups
    .map(([slug, group]) => {
      const cards = group.records
        .sort((left, right) => String(right.date).localeCompare(String(left.date)) || left.title.localeCompare(right.title))
        .map((record) => card(record, false))
        .join("");
      return `<section class="tag-group"><h2><a href="/tags/${slug}">${escapeHtml(group.label)}</a></h2><ul class="tag-card-list">${cards}</ul></section>`;
    })
    .join("");
  records.push({
    route: "/tags",
    canonicalPath: "/tags/",
    canonicalUrl: `${SITE_ORIGIN}/tags/`,
    physicalPath: "tags/index.html",
    kind: "folder",
    source: "generated:tags",
    title: "Tag Index",
    description: "Topics in the Verdify public greenhouse evidence notebook.",
    html: `<h1>Tag Index</h1>${groups}`,
    aliases: [],
    tags: [],
    cssclasses: [],
    noindex: true,
    target: "",
    grafana: [],
    cameras: [],
    currentMedia: [],
    unavailable: [],
    date: "",
    socialImage: "",
  });
  return records;
}

function folderRecords(sourceRecords, occupied) {
  const groups = new Map();
  for (const record of sourceRecords) {
    const segments = record.route.split("/").filter(Boolean);
    if (segments.length < 2) continue;
    const route = `/${segments[0]}`;
    if (occupied.has(route)) continue;
    const directChild = segments.length === 2;
    if (!directChild) continue;
    const entries = groups.get(route) ?? [];
    entries.push(record);
    groups.set(route, entries);
  }

  const generated = [];
  for (const [route, entries] of [...groups].sort()) {
    if (occupied.has(route)) continue;
    occupied.add(route);
    const label = route.slice(1);
    const cards = entries
      .sort((left, right) => left.title.localeCompare(right.title))
      .map((record) => {
        const topics = record.tags
          .map((tag) => {
            const slug = tagSlug(tag);
            return slug ? `<a href="/tags/${slug}">${escapeHtml(tag)}</a>` : "";
          })
          .filter(Boolean)
          .join(" · ");
        return `<li><h3><a href="${escapeHtml(record.canonicalPath)}">${escapeHtml(record.title)}</a></h3>${topics ? `<p>${topics}</p>` : ""}</li>`;
      })
      .join("");
    generated.push({
      route,
      canonicalPath: `${route}/`,
      canonicalUrl: `${SITE_ORIGIN}${route}/`,
      physicalPath: `${label}/index.html`,
      kind: "folder",
      source: `generated:folder:${label}`,
      title: `Folder: ${label}`,
      description: `Browse Verdify Lab ${label} evidence pages.`,
      html: `<h1>Folder: ${escapeHtml(label)}</h1><ul class="folder-index">${cards}</ul>`,
      aliases: [],
      tags: [],
      cssclasses: ["folder-index-page"],
      noindex: false,
      target: "",
      grafana: [],
      cameras: [],
      currentMedia: [],
      unavailable: [],
      date: "",
      socialImage: "",
    });
  }
  return generated;
}

function xmlEscape(value) {
  return escapeHtml(value);
}

function xmlCdata(value) {
  return String(value).replaceAll("]]>", "]]]]><![CDATA[>");
}

async function writeIndexes(records, build) {
  const canonical = records.filter((record) => record.kind !== "alias" && !record.noindex);
  const sitemap = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${canonical
    .map((record) => `<url><loc>${xmlEscape(record.canonicalUrl)}</loc>${record.date ? `<lastmod>${new Date(record.date).toISOString()}</lastmod>` : ""}</url>`)
    .join("")}</urlset>\n`;
  const dated = records
    .filter((record) => record.kind !== "alias" && !record.noindex && record.date)
    .sort((left, right) => (
      String(right.date).localeCompare(String(left.date))
      || left.route.localeCompare(right.route)
    ))
    .slice(0, 10);
  const rss = `<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>Verdify Lab</title><link>${SITE_ORIGIN}</link><description>Last 10 notes on Verdify Lab</description><generator>Verdify public site</generator>${dated
    .map((record) => `<item><title>${xmlEscape(record.title)}</title><link>${xmlEscape(record.canonicalUrl)}</link><guid>${xmlEscape(record.canonicalUrl)}</guid><description><![CDATA[ ${xmlCdata(record.description)} ]]></description><pubDate>${new Date(record.date).toUTCString()}</pubDate></item>`)
    .join("")}</channel></rss>\n`;
  await writeFile(path.join(PUBLIC_ROOT, "sitemap.xml"), sitemap);
  await writeFile(path.join(PUBLIC_ROOT, "rss.xml"), rss);
  await writeFile(path.join(PUBLIC_ROOT, "index.xml"), rss);
  const robotsPolicy = STAGE_GLOBAL_NOINDEX
    ? "Disallow: /"
    : "Allow: /\nDisallow: /static/vision/\nDisallow: /greenhouse/lessons/raw";
  await writeFile(
    path.join(PUBLIC_ROOT, "robots.txt"),
    `User-agent: *\n${robotsPolicy}\n\nSitemap: ${SITE_ORIGIN}/sitemap.xml\n`,
  );
  await writeFile(path.join(PUBLIC_ROOT, "static-build.json"), `${JSON.stringify(build, null, 2)}\n`);
}

async function main({ occurrenceStoreFactory = null } = {}) {
  const snapshotRoot = process.env.LAB_SNAPSHOT;
  if (!snapshotRoot) throw new Error("LAB_SNAPSHOT must name a local snapshot root");
  const snapshot = await verifySnapshot(snapshotRoot, {
    allowSyntheticFixture: process.env.ALLOW_SYNTHETIC_FIXTURE === "true",
  });
  const occurrenceBinding = await loadCompilerOccurrenceBinding({ snapshot, occurrenceStoreFactory });
  const selectedOccurrenceRelease = occurrenceBinding.release;
  const selectedOccurrenceState = occurrenceStateIndex(selectedOccurrenceRelease.current);
  await rm(PUBLIC_ROOT, { recursive: true, force: true });
  await rm(RECORDS_PATH, { force: true });
  await rm(ASSETS_PATH, { force: true });
  await rm(BUILD_PATH, { force: true });
  await mkdir(PUBLIC_ROOT, { recursive: true });
  for (const entry of await readdir(SITE_SHELL_PUBLIC)) {
    await cp(path.join(SITE_SHELL_PUBLIC, entry), path.join(PUBLIC_ROOT, entry), {
      recursive: true,
      force: false,
      errorOnExist: true,
    });
  }
  await materializeCompatAssets();

  const markdownSources = [];
  const excludedDrafts = [];
  const assetRecords = [];
  const imageMetadata = new Map();
  const responsiveAssets = [];
  for (const [relative, entry] of snapshot.files) {
    if (relative.endsWith(".md")) {
      const source = await readFile(entry.absolute, "utf8");
      const [frontmatter, markdown] = splitFrontmatter(source, relative);
      if (frontmatter.draft === true) {
        excludedDrafts.push(relative);
        continue;
      }
      markdownSources.push({ relative, frontmatter, markdown, ...routeFromSource(relative) });
    } else if (relative !== "robots.txt") {
      const destination = path.join(PUBLIC_ROOT, ...relative.split("/"));
      await mkdir(path.dirname(destination), { recursive: true });
      await cp(entry.absolute, destination, { dereference: false, force: false });
      assetRecords.push({ relative, sha256: snapshot.manifest.files[relative], bytes: entry.size });
      if (/\.(?:jpe?g|png|svg)$/i.test(relative)) {
        const responsive = await buildResponsiveImage(entry, relative, snapshot.manifest.files[relative]);
        const dimensions = responsive ?? imageDimensions(await readFile(entry.absolute), relative);
        if (dimensions?.width > 0 && dimensions?.height > 0) {
          imageMetadata.set(relative, dimensions);
          responsiveAssets.push(...(dimensions.variants ?? []));
        }
      }
    }
  }
  markdownSources.sort((left, right) => left.relative.localeCompare(right.relative));
  const linkIndex = buildLinkIndex(markdownSources);
  const routeBySource = new Map(markdownSources.map((source) => [source.relative, source.route]));
  const assetPaths = new Set(assetRecords.map((record) => record.relative));
  const plannedRoutes = new Set(["/", "/404", ...routeBySource.values()]);
  for (const source of markdownSources) {
    for (const alias of stringList(source.frontmatter.aliases ?? source.frontmatter.alias)) {
      plannedRoutes.add(normalizeRoute(alias));
    }
    for (const tag of stringList(source.frontmatter.tags)) {
      const slug = tagSlug(tag);
      if (slug) plannedRoutes.add(`/tags/${slug}`);
    }
    const segments = source.route.split("/").filter(Boolean);
    if (segments.length >= 2) plannedRoutes.add(`/${segments[0]}`);
  }
  plannedRoutes.add("/tags");
  if (markdownSources.some((source) => /^\/plans\/\d{4}-\d{2}-\d{2}$/.test(source.route))) {
    plannedRoutes.add("/plans/latest");
  }
  const records = [];
  const occupied = new Set();
  for (const source of markdownSources) {
    if (occupied.has(source.route)) throw new Error(`duplicate source route: ${source.route}`);
    occupied.add(source.route);
    const rendered = await renderMarkdown(
      source.markdown,
      source,
      linkIndex,
      routeBySource,
      assetPaths,
      imageMetadata,
      snapshot.files,
      plannedRoutes,
      selectedOccurrenceState,
    );
    const aliases = stringList(source.frontmatter.aliases ?? source.frontmatter.alias);
    const tags = stringList(source.frontmatter.tags);
    const title = String(source.frontmatter.title ?? path.posix.basename(source.relative, ".md"));
    const record = {
      route: source.route,
      canonicalPath: "",
      canonicalUrl: "",
      physicalPath: source.physicalPath,
      kind: source.kind,
      source: source.relative,
      title,
      description: String(source.frontmatter.description ?? "").trim() || rendered.description,
      html: rendered.html,
      aliases,
      tags,
      cssclasses: stringList(source.frontmatter.cssclasses),
      noindex: source.frontmatter.noindex === true,
      target: "",
      grafana: rendered.grafana,
      cameras: rendered.currentMedia,
      currentMedia: rendered.currentMedia,
      unavailable: rendered.unavailable,
      date: source.frontmatter.date ? String(source.frontmatter.date) : "",
      socialImage: socialImagePath(source.frontmatter.socialImage, assetPaths, source.relative),
    };
    record.canonicalPath = canonicalPath(record);
    record.canonicalUrl = `${SITE_ORIGIN}${record.canonicalPath}`;
    records.push(record);
  }

  const aliasResult = aliasRecords(records);
  const aliases = aliasResult.aliases;
  const generatedOccupied = new Set([...occupied, ...aliases.map((record) => record.route)]);
  const folders = folderRecords(records, generatedOccupied);
  const tags = tagRecords(records, generatedOccupied);
  const allRecords = [...records, ...aliases, ...folders, ...tags].sort((left, right) => left.route.localeCompare(right.route));
  const routeDigest = createHash("sha256")
    .update(JSON.stringify(allRecords.map(({ route, physicalPath, kind, source }) => ({ route, physicalPath, kind, source }))))
    .digest("hex");
  const discoveredGraphs = records.flatMap((record) => record.grafana);
  const discoveredCurrentMedia = records.flatMap((record) => record.currentMedia);
  const discoveryOccurrenceManifest = staticOccurrenceManifest({
    snapshotId: snapshot.snapshotId,
    discoveredGraphs,
    discoveredCurrentMedia,
  });
  verifyCompilerOccurrenceDiscovery(occurrenceBinding, discoveryOccurrenceManifest);
  const occurrenceManifest = staticOccurrenceManifest({
    snapshotId: snapshot.snapshotId,
    selectedManifestSha256: selectedOccurrenceRelease.selection?.current.manifestSha256 ?? null,
    discoveredGraphs,
    discoveredCurrentMedia,
    selectedManifest: selectedOccurrenceRelease.current,
  });
  verifyCompleteSelectedOccurrenceEvidence(
    selectedOccurrenceRelease,
    occurrenceManifest,
    occurrenceBinding.policy,
    occurrenceBinding.policySha256,
  );
  const discoveredGraphIds = new Set(discoveredGraphs.map((occurrence) => occurrence.occurrenceId));
  const discoveredMediaIds = new Set(discoveredCurrentMedia.map((occurrence) => occurrence.occurrenceId));
  const selectedBuildOccurrences = selectedOccurrenceRelease.current
    ? {
        ...selectedOccurrenceRelease.current,
        occurrences: {
          graphs: selectedOccurrenceRelease.current.occurrences.graphs.filter((occurrence) => discoveredGraphIds.has(occurrence.occurrenceId)),
          currentMedia: selectedOccurrenceRelease.current.occurrences.currentMedia.filter((occurrence) => discoveredMediaIds.has(occurrence.occurrenceId)),
        },
      }
    : null;
  const materializedOccurrenceBlobCount = selectedBuildOccurrences
    ? await materializeOccurrenceBlobs(occurrenceBinding.store, selectedBuildOccurrences, PUBLIC_ROOT)
    : 0;
  const occurrenceManifestBytes = `${JSON.stringify(occurrenceManifest, null, 2)}\n`;
  const occurrenceManifestDigest = createHash("sha256").update(occurrenceManifestBytes).digest("hex");
  await writeFile(path.join(PUBLIC_ROOT, "occurrence-manifest.json"), occurrenceManifestBytes);
  const build = {
    contract: "verdify.lab-astro-stage-build",
    schemaVersion: 1,
    siteOrigin: SITE_ORIGIN,
    stageGlobalNoindex: STAGE_GLOBAL_NOINDEX,
    snapshotId: snapshot.snapshotId,
    snapshotManifestDigest: snapshot.manifestDigest,
    sanitization: snapshot.sanitization,
    localEvidenceStatus: snapshot.evidenceStatus,
    approvalEligible: snapshot.approvalEligible,
    mandatoryApprovalBoundary: snapshot.mandatoryApprovalBoundary,
    sourceCount: records.length,
    snapshotMarkdownCount: markdownSources.length + excludedDrafts.length,
    excludedDrafts,
    aliasCount: aliases.length,
    rollingPlanCompatibility: aliasResult.compatibility,
    tagRouteCount: tags.length,
    folderRouteCount: folders.length,
    grafanaOccurrenceCount: records.reduce((count, record) => count + record.grafana.length, 0),
    cameraOccurrenceCount: discoveredCurrentMedia.length,
    cameraLocalFallbackCount: discoveredCurrentMedia.filter(
      (occurrence) => selectedOccurrenceState.currentMedia.get(occurrence.occurrenceId)?.fallback,
    ).length,
    unavailableReferenceCount: records.reduce((count, record) => count + record.unavailable.length, 0),
    currentMediaOccurrenceCount: discoveredCurrentMedia.length,
    selectedOccurrenceManifestSha256: selectedOccurrenceRelease.selection?.current.manifestSha256
      ? `sha256:${selectedOccurrenceRelease.selection.current.manifestSha256}`
      : null,
    occurrenceManifestDigest: `sha256:${occurrenceManifestDigest}`,
    materializedOccurrenceBlobCount,
    routeDigest: `sha256:${routeDigest}`,
    snapshotAssetCount: [...snapshot.files.keys()].filter((relative) => !relative.endsWith(".md")).length,
    copiedSnapshotAssetCount: assetRecords.length,
    generatedResponsiveImageCount: responsiveAssets.length,
    policyReplacedAssets: snapshot.files.has("robots.txt") ? ["robots.txt"] : [],
    preservedMediaCount: assetRecords.filter((record) => record.relative.startsWith("static/video/")).length,
    siteShell: {
      contractVersion: "1.1.0",
      wwwCommit: "7febbc479c6ed7d22f829e9c1e7109bc9bc7c6c0",
      archiveDigest: "sha256:0645773ab3a952727251840e28dc73929a3e42b904450bcc9e7d25d8b03b1c91",
      releaseDigest: "sha256:779620f2eda4d62677a2d9d61c65e2a1014e34de8cb2cec5008928caeef46a6d",
      manifestDigest: "sha256:2864debbe67b23cd20ef5aa5fb57e86803ed1a1a9393b993c0acdd9182a6f585",
    },
  };
  await writeFile(RECORDS_PATH, `${JSON.stringify(allRecords)}\n`);
  await writeFile(ASSETS_PATH, `${JSON.stringify([...assetRecords, ...responsiveAssets])}\n`);
  await writeFile(BUILD_PATH, `${JSON.stringify(build, null, 2)}\n`);
  await writeIndexes(allRecords, build);
  process.stdout.write(
    `compiled ${records.length} sources, ${aliases.length} aliases, ${tags.length} tag routes, ${build.grafanaOccurrenceCount} Grafana occurrences\n`,
  );
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`compile-snapshot: ${error.message}\n`);
    process.exitCode = 1;
  });
}

export {
  aliasRecords,
  cameraSnapshotAsset,
  folderRecords,
  imageDimensions,
  inferredDescription,
  loadCompilerOccurrenceBinding,
  main,
  normalizeRoute,
  renderMarkdown,
  routeFromSource,
  socialImagePath,
  splitFrontmatter,
  tagRecords,
  verifyCompilerOccurrenceDiscovery,
  verifyCompleteSelectedOccurrenceEvidence,
  verifyCompatAssets,
};
