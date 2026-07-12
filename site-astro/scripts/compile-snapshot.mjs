import { createHash } from "node:crypto";
import { cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
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
const SITE_ORIGIN = normalizeOrigin(process.env.SITE_ORIGIN ?? "https://lab-stage.verdify.ai");
const STAGE_GLOBAL_NOINDEX = process.env.STAGE_GLOBAL_NOINDEX !== "false";

function normalizeOrigin(value) {
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("SITE_ORIGIN must be one credential-free HTTPS origin");
  }
  parsed.pathname = "";
  return parsed.toString().replace(/\/$/, "");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
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

function rehypeGrafanaEvidence(occurrences) {
  return () => (tree) => {
    visit(tree, "element", (node, index, parent) => {
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
      const renderUrl = grafanaRenderUrl(liveUrl);
      const title = typeof node.properties?.title === "string" ? node.properties.title : "Greenhouse evidence graph";
      occurrences.push({ liveUrl, renderUrl, title });
      parent.children[index] = {
        type: "element",
        tagName: "figure",
        properties: {
          className: ["grafana-evidence"],
          "data-iframe-src": liveUrl,
          "data-live-src": liveUrl,
          "data-image-src": renderUrl,
          "data-title": title,
        },
        children: [
          {
            type: "element",
            tagName: "div",
            properties: { className: ["grafana-evidence__status"] },
            children: [{ type: "text", value: "Verified local graph fallback is pending for this stage snapshot." }],
          },
          {
            type: "element",
            tagName: "a",
            properties: { href: liveUrl, rel: ["noopener", "noreferrer"], target: "_blank" },
            children: [{ type: "text", value: "Open interactive graph" }],
          },
        ],
      };
    });
  };
}

async function renderMarkdown(markdown, source, linkIndex, routeBySource, assetPaths, imageMetadata, snapshotFiles) {
  const occurrences = [];
  const cameras = [];
  const processor = unified()
    .use(remarkParse)
    .use(remarkGfm, { singleTilde: false })
    .use(remarkMath)
    .use(remarkRewriteLocalLinks(source, routeBySource))
    .use(remarkRehype, { allowDangerousHtml: true })
    .use(rehypeRaw)
    .use(rehypeRewriteRelativeReferences(source, routeBySource, assetPaths))
    .use(rehypeSlug)
    .use(rehypeKatex)
    .use(rehypeCameraSnapshots(snapshotFiles, cameras))
    .use(rehypeImageMetadata(imageMetadata))
    .use(rehypeGrafanaEvidence(occurrences));
  const tree = processor.parse(expandWikilinks(markdown, source, linkIndex));
  const result = await processor.run(tree);
  return { html: toHtml(result, { allowDangerousHtml: true }), grafana: occurrences, cameras };
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
        date: record.date,
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
      date: latest.date,
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
      const slug = titleKey(tag).replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      if (!slug) continue;
      const entries = tags.get(slug) ?? { label: tag, records: [] };
      entries.records.push(record);
      tags.set(slug, entries);
    }
  }
  const records = [];
  for (const [slug, group] of [...tags].sort()) {
    const route = `/tags/${slug}`;
    if (occupied.has(route)) throw new Error(`generated tag route collides: ${route}`);
    occupied.add(route);
    const links = group.records
      .sort((left, right) => left.title.localeCompare(right.title))
      .map((record) => `<li><a href="${escapeHtml(record.canonicalPath)}">${escapeHtml(record.title)}</a></li>`)
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
      html: `<h1>Tag: ${escapeHtml(group.label)}</h1><ul>${links}</ul>`,
      aliases: [],
      tags: [],
      cssclasses: [],
      noindex: false,
      target: "",
      grafana: [],
      cameras: [],
      date: "",
    });
  }
  records.push({
    route: "/tags",
    canonicalPath: "/tags/",
    canonicalUrl: `${SITE_ORIGIN}/tags/`,
    physicalPath: "tags/index.html",
    kind: "folder",
    source: "generated:tags",
    title: "Verdify Lab tags",
    description: "Topics in the Verdify public greenhouse evidence notebook.",
    html: `<h1>Topics</h1><ul>${[...tags].sort().map(([slug, group]) => `<li><a href="/tags/${slug}">${escapeHtml(group.label)}</a></li>`).join("")}</ul>`,
    aliases: [],
    tags: [],
    cssclasses: [],
    noindex: false,
    target: "",
    grafana: [],
    cameras: [],
    date: "",
  });
  return records;
}

function xmlEscape(value) {
  return escapeHtml(value);
}

async function writeIndexes(records, build) {
  const canonical = records.filter((record) => record.kind !== "alias" && !record.noindex);
  const sitemap = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${canonical
    .map((record) => `<url><loc>${xmlEscape(record.canonicalUrl)}</loc></url>`)
    .join("")}</urlset>\n`;
  const dated = records
    .filter((record) => record.kind !== "alias" && !record.noindex && record.date)
    .sort((left, right) => String(right.date).localeCompare(String(left.date)))
    .slice(0, 10);
  const rss = `<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>Verdify Lab</title><link>${SITE_ORIGIN}/</link><description>Public greenhouse evidence</description>${dated
    .map((record) => `<item><title>${xmlEscape(record.title)}</title><link>${xmlEscape(record.canonicalUrl)}</link><guid>${xmlEscape(record.canonicalUrl)}</guid><pubDate>${new Date(record.date).toUTCString()}</pubDate></item>`)
    .join("")}</channel></rss>\n`;
  await writeFile(path.join(PUBLIC_ROOT, "sitemap.xml"), sitemap);
  await writeFile(path.join(PUBLIC_ROOT, "rss.xml"), rss);
  await writeFile(
    path.join(PUBLIC_ROOT, "robots.txt"),
    `User-agent: *\n${STAGE_GLOBAL_NOINDEX ? "Disallow: /" : "Allow: /"}\nSitemap: ${SITE_ORIGIN}/sitemap.xml\n`,
  );
  await writeFile(path.join(PUBLIC_ROOT, "static-build.json"), `${JSON.stringify(build, null, 2)}\n`);
}

async function main() {
  const snapshotRoot = process.env.LAB_SNAPSHOT;
  if (!snapshotRoot) throw new Error("LAB_SNAPSHOT must name a local snapshot root");
  const snapshot = await verifySnapshot(snapshotRoot, {
    allowSyntheticFixture: process.env.ALLOW_SYNTHETIC_FIXTURE === "true",
  });
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
      description: String(source.frontmatter.description ?? ""),
      html: rendered.html,
      aliases,
      tags,
      cssclasses: stringList(source.frontmatter.cssclasses),
      noindex: source.frontmatter.noindex === true,
      target: "",
      grafana: rendered.grafana,
      cameras: rendered.cameras,
      date: source.frontmatter.date ? String(source.frontmatter.date) : "",
    };
    record.canonicalPath = canonicalPath(record);
    record.canonicalUrl = `${SITE_ORIGIN}${record.canonicalPath}`;
    records.push(record);
  }

  const aliasResult = aliasRecords(records);
  const aliases = aliasResult.aliases;
  const tags = tagRecords(records, new Set([...occupied, ...aliases.map((record) => record.route)]));
  const allRecords = [...records, ...aliases, ...tags].sort((left, right) => left.route.localeCompare(right.route));
  const routeDigest = createHash("sha256")
    .update(JSON.stringify(allRecords.map(({ route, physicalPath, kind, source }) => ({ route, physicalPath, kind, source }))))
    .digest("hex");
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
    grafanaOccurrenceCount: records.reduce((count, record) => count + record.grafana.length, 0),
    cameraOccurrenceCount: records.reduce((count, record) => count + record.cameras.length, 0),
    cameraLocalFallbackCount: records.reduce(
      (count, record) => count + record.cameras.filter((camera) => camera.available).length,
      0,
    ),
    routeDigest: `sha256:${routeDigest}`,
    snapshotAssetCount: [...snapshot.files.keys()].filter((relative) => !relative.endsWith(".md")).length,
    copiedSnapshotAssetCount: assetRecords.length,
    generatedResponsiveImageCount: responsiveAssets.length,
    policyReplacedAssets: snapshot.files.has("robots.txt") ? ["robots.txt"] : [],
    preservedMediaCount: assetRecords.filter((record) => record.relative.startsWith("static/video/")).length,
    siteShell: {
      contractVersion: "1.0.0",
      wwwCommit: "c9c0d56f654d6b9198352f16c620717dbee71612",
      archiveDigest: "sha256:6600525856f7a32b2fe7b30b4043fc29cdb26346f5b4689b20343cdff4efce61",
      releaseDigest: "sha256:897f872a6ab8de39f2c55e0d7833d723c00b1c9533673df6309472552956b42c",
      manifestDigest: "sha256:43ca0600f9a6db8af2a54e93da06d4d2994991018c2344a6c854bc6297ab9458",
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
  imageDimensions,
  main,
  normalizeRoute,
  renderMarkdown,
  routeFromSource,
  splitFrontmatter,
};
