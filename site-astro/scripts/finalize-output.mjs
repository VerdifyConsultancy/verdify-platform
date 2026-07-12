import { access, mkdir, readFile, rename, rmdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PROJECT_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DIST_ROOT = path.join(PROJECT_ROOT, "dist");
const records = JSON.parse(await readFile(path.join(PROJECT_ROOT, ".generated", "content-records.json"), "utf8"));
const build = JSON.parse(await readFile(path.join(PROJECT_ROOT, ".generated", "build.json"), "utf8"));

const physicalPaths = new Set();
for (const record of records) {
  if (physicalPaths.has(record.physicalPath)) throw new Error(`duplicate physical output: ${record.physicalPath}`);
  physicalPaths.add(record.physicalPath);
  if (record.route === "/" || record.kind === "folder") continue;
  const routePath = record.route.slice(1);
  const emitted = path.join(DIST_ROOT, ...routePath.split("/"), "index.html");
  const destination = path.join(DIST_ROOT, ...record.physicalPath.split("/"));
  await mkdir(path.dirname(destination), { recursive: true });
  await rename(emitted, destination);
  try {
    await rmdir(path.dirname(emitted));
  } catch (error) {
    if (error.code !== "ENOTEMPTY") throw error;
  }
}

for (const record of records) {
  await access(path.join(DIST_ROOT, ...record.physicalPath.split("/")));
}

await writeFile(
  path.join(DIST_ROOT, "route-manifest.json"),
  `${JSON.stringify(
    {
      contract: "verdify.lab-astro-route-manifest",
      schemaVersion: 1,
      build,
      routes: records.map(({ route, canonicalPath, physicalPath, kind, source, noindex, grafana, cameras, unavailable }) => ({
        route,
        canonicalPath,
        physicalPath,
        kind,
        source,
        noindex,
        grafana,
        cameras,
        unavailable: unavailable ?? [],
      })),
    },
    null,
    2,
  )}\n`,
);

process.stdout.write(`finalized ${records.length} route outputs\n`);
