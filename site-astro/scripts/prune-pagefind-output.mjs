import { lstat, readdir, readFile, unlink } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST = path.join(ROOT, "dist");
const UNUSED_BUNDLES = Object.freeze([
  "pagefind/pagefind-component-ui.js",
  "pagefind/pagefind-ui.js",
]);
const SEARCHABLE_SUFFIXES = new Set([".css", ".html", ".js", ".json", ".mjs"]);
const MAX_SEARCHABLE_BYTES = 8 * 1024 * 1024;

async function* files(directory, prefix = "") {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    const absolute = path.join(directory, entry.name);
    if (entry.isDirectory()) yield* files(absolute, relative);
    else if (entry.isFile()) yield relative;
    else throw new Error(`unexpected non-regular build entry: ${relative}`);
  }
}

for (const relative of UNUSED_BUNDLES) {
  const selected = await lstat(path.join(DIST, ...relative.split("/")));
  if (!selected.isFile() || selected.isSymbolicLink() || selected.nlink !== 1) {
    throw new Error(`unused Pagefind bundle is not a single-link regular file: ${relative}`);
  }
}

for await (const relative of files(DIST)) {
  if (UNUSED_BUNDLES.includes(relative) || !SEARCHABLE_SUFFIXES.has(path.extname(relative).toLowerCase())) continue;
  const absolute = path.join(DIST, ...relative.split("/"));
  const selected = await lstat(absolute);
  if (selected.size > MAX_SEARCHABLE_BYTES) throw new Error(`searchable build asset exceeds pruning bound: ${relative}`);
  const source = await readFile(absolute, "utf8");
  for (const target of UNUSED_BUNDLES) {
    if (source.includes(path.posix.basename(target))) {
      throw new Error(`unused Pagefind bundle is referenced by build output: ${target}`);
    }
  }
}

for (const relative of UNUSED_BUNDLES) await unlink(path.join(DIST, ...relative.split("/")));
process.stdout.write(`pruned ${UNUSED_BUNDLES.length} unreferenced Pagefind UI bundles\n`);
