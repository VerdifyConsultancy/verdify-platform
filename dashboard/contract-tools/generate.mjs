// SPDX-License-Identifier: MIT
/**
 * Render the dashboard's operator API client from the hand-maintained OpenAPI file (R1). Generation reads only local
 * files and refuses remote schemas. `--write` writes the client, `--check` renders it in memory and exits 1 naming
 * the stale file, and no flag prints it.
 */
import { readFile, writeFile } from "node:fs/promises";
import openapiTS, { astToString } from "openapi-typescript";
import { parse } from "yaml";

const SOURCE = "docs/design/dashboard/openapi/dashboard-api.v1.yaml";
const TARGET = "app/generated/operator-api.ts";
const source = new URL(`../../${SOURCE}`, import.meta.url);
const target = new URL(`../${TARGET}`, import.meta.url);
const HEADER = `// SPDX-License-Identifier: MIT\n// Generated from ${SOURCE}; run pnpm contract:write.\n`;

const text = await readFile(source, "utf8");
if (Buffer.byteLength(text) > 2 * 1024 * 1024) throw new Error("Contract exceeds generation budget");
const document = parse(text, { prettyErrors: true, uniqueKeys: true });
function localReferences(value) {
  if (!value || typeof value !== "object") return;
  if ("$ref" in value && (typeof value.$ref !== "string" || !value.$ref.startsWith("#/"))) throw new Error("Remote contract references are forbidden");
  for (const child of Object.values(value)) localReferences(child);
}
localReferences(document);
// Optionality follows each schema's `required`: a field with a default stays optional in the request bodies that
// reference it (a schedule edit sends only what changes), and a stored-JSON object reads as Record<string, unknown>.
const ast = await openapiTS(document, { alphabetize: true, defaultNonNullable: false, emptyObjectsUnknown: true, immutable: true, silent: true });
const rendered = HEADER + astToString(ast);

const mode = process.argv[2];
if (mode === "--write") {
  await writeFile(target, rendered);
  process.stdout.write(`Wrote ${TARGET} from ${SOURCE}\n`);
} else if (mode === "--check") {
  const committed = await readFile(target, "utf8").catch(() => null);
  if (committed !== rendered) {
    process.stderr.write(`${TARGET} is stale against ${SOURCE}; run pnpm contract:write\n`);
    process.exit(1);
  }
  process.stdout.write(`${TARGET} matches ${SOURCE}\n`);
} else if (mode === undefined) {
  process.stdout.write(rendered);
} else {
  throw new Error(`Unknown option ${mode}; use --write or --check`);
}
