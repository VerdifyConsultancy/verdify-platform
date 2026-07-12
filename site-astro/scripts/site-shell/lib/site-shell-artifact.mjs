import { createHash } from "node:crypto";
import { constants as fsConstants } from "node:fs";
import { lstat, open, realpath } from "node:fs/promises";
import path from "node:path";

export const DEFAULT_CONTRACT_PATH = "contracts/site-shell/v1/contract.json";
export const MAX_ARCHIVE_BYTES = 16 * 1024 * 1024;
export const MAX_ARCHIVE_FILE_BYTES = MAX_ARCHIVE_BYTES + 4096;
export const MAX_INPUT_BYTES = MAX_ARCHIVE_BYTES;
export const NORMALIZATION = Object.freeze({
  entryOrder: "utf8-bytewise-ascending",
  directoryMode: 0o755,
  fileMode: 0o644,
  uid: 0,
  gid: 0,
  mtime: 0,
  userName: "",
  groupName: "",
  gzipHeaderMtime: 0,
  gzipOperatingSystem: 255,
});

const CONTRACT_KEYS = [
  "$schema",
  "artifactRoot",
  "componentApi",
  "contractVersion",
  "dependencies",
  "installation",
  "manifestPath",
  "name",
  "payload",
  "schemaVersion",
];
const PAYLOAD_KEYS = ["kind", "mode", "path", "source", "transform"];
const PAYLOAD_KINDS = new Set([
  "asset",
  "component",
  "contract",
  "data",
  "documentation",
  "font",
  "license",
  "schema",
  "styles",
]);
const TRANSFORMS = new Set(["identity", "vendored-css-v1"]);
const MANIFEST_KEYS = ["artifact", "contract", "dependencies", "files", "schemaVersion"];
const MANIFEST_CONTRACT_KEYS = ["name", "sourceTreeDigest", "version"];
const MANIFEST_ARTIFACT_KEYS = ["createdAt", "format", "manifestPath", "normalization", "root"];
const DEPENDENCY_KEYS = ["name", "source", "version"];
const FILE_KEYS = ["kind", "mode", "path", "sha256", "size", "source", "transform"];
const NORMALIZATION_KEYS = Object.keys(NORMALIZATION).sort();
const COMPONENT_API_KEYS = ["components", "schemaVersion", "sharedProps", "types"];
const INSTALLATION_KEYS = ["directoryMode", "fileMode", "requiresPinnedArchiveDigest", "requiresPinnedSourceTreeDigest"];
const COMPONENT_TYPE_KEYS = ["BreadcrumbItem"];
const OBJECT_TYPE_DESCRIPTOR_KEYS = ["additionalProperties", "properties", "required", "type"];
const BREADCRUMB_ITEM_KEYS = ["href", "name", "targetSite"];
const SHARED_PROP_KEYS = ["brand", "corporateOrigin", "currentSite", "homeHref", "labOrigin"];
const COMPONENT_KEYS = ["Breadcrumbs", "Footer", "Header"];
const COMPONENT_DESCRIPTOR_KEYS = ["props", "requiredProps"];
const PROP_DESCRIPTOR_KEYS = ["default", "enum", "required", "semantics", "type"];
const CONTRACT_VERSION_PATTERN = /^[0-9]+\.[0-9]+\.[0-9]+$/;
const DEPENDENCY_VERSION_PATTERN = /^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$/;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const SOURCE_TREE_DIGEST_PATTERN = /^sha256:[a-f0-9]{64}$/;
const SAFE_PATH_PATTERN = /^[A-Za-z0-9@._/-]+$/;

const fail = (message) => {
  throw new Error(message);
};

const isPlainObject = (value) => (
  value !== null
  && typeof value === "object"
  && !Array.isArray(value)
  && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null)
);

const compareUtf8 = (left, right) => Buffer.compare(Buffer.from(left, "utf8"), Buffer.from(right, "utf8"));

const sortedKeys = (value) => Object.keys(value).sort();

const assertPlainObject = (value, label) => {
  if (!isPlainObject(value)) fail(`${label} must be an object.`);
};

const assertExactKeys = (value, expectedKeys, label) => {
  assertPlainObject(value, label);
  const actual = sortedKeys(value);
  const expected = [...expectedKeys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    fail(`${label} has unexpected keys. Expected ${expected.join(", ")}; received ${actual.join(", ")}.`);
  }
};

export const validateSafeRelativePath = (value, label = "path") => {
  if (typeof value !== "string" || !value || !SAFE_PATH_PATTERN.test(value)) {
    fail(`${label} must be a non-empty portable relative POSIX path.`);
  }
  if (
    value.startsWith("/")
    || value.endsWith("/")
    || value.includes("\\")
    || value.includes("\0")
    || value.split("/").some((segment) => segment === "" || segment === "." || segment === "..")
    || path.posix.normalize(value) !== value
  ) {
    fail(`${label} is unsafe or non-canonical: ${value}`);
  }
  return value;
};

const validateArtifactRoot = (value) => {
  validateSafeRelativePath(value, "artifactRoot");
  if (value.includes("/")) fail("artifactRoot must be one path segment.");
  return value;
};

export const sha256 = (value) => createHash("sha256").update(value).digest("hex");

const sameFileIdentity = (left, right) => (
  left.dev === right.dev
  && left.ino === right.ino
  && left.mode === right.mode
  && left.nlink === right.nlink
);

const sameStableFileState = (left, right) => (
  sameFileIdentity(left, right)
  && left.size === right.size
  && left.mtimeNs === right.mtimeNs
  && left.ctimeNs === right.ctimeNs
);

const isContainedPath = (root, candidate) => {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
};

const collectSourceChain = async (realRoot, safePath) => {
  const records = [];
  let current = realRoot;
  const rootStat = await lstat(realRoot, { bigint: true });
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) fail(`Source root must be a real directory: ${realRoot}`);
  records.push({ path: realRoot, stat: rootStat });
  const segments = safePath.split("/");
  for (let index = 0; index < segments.length; index += 1) {
    current = path.join(current, segments[index]);
    let stat;
    try {
      stat = await lstat(current, { bigint: true });
    } catch (error) {
      if (error?.code === "ENOENT") fail(`Required source is missing: ${safePath}`);
      throw error;
    }
    if (stat.isSymbolicLink()) fail(`Symlink sources are forbidden: ${safePath}`);
    if (index < segments.length - 1 && !stat.isDirectory()) fail(`Source parent is not a directory: ${safePath}`);
    if (index === segments.length - 1 && (!stat.isFile() || stat.nlink !== 1n)) {
      fail(`Source must be a single-link regular file: ${safePath}`);
    }
    records.push({ path: current, stat });
  }
  return records;
};

export const readDescriptorBounded = async (handle, maxBytes, label) => {
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 0 || maxBytes === Number.MAX_SAFE_INTEGER) {
    fail(`${label} byte limit is invalid.`);
  }
  const chunks = [];
  let offset = 0;
  while (true) {
    if (offset > maxBytes) fail(`${label} exceeds ${maxBytes} bytes.`);
    const remaining = maxBytes + 1 - offset;
    const buffer = Buffer.alloc(Math.min(1024 * 1024, remaining));
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, offset);
    if (bytesRead === 0) break;
    chunks.push(buffer.subarray(0, bytesRead));
    offset += bytesRead;
  }
  if (offset > maxBytes) fail(`${label} exceeds ${maxBytes} bytes.`);
  return Buffer.concat(chunks, offset);
};

const collectAbsoluteInputChain = async (absolutePath, label) => {
  const parsed = path.parse(absolutePath);
  let current = parsed.root;
  const records = [];
  for (const segment of absolutePath.slice(parsed.root.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, segment);
    let stat;
    try {
      stat = await lstat(current, { bigint: true });
    } catch (error) {
      if (error?.code === "ENOENT") fail(`${label} is missing.`);
      throw error;
    }
    if (stat.isSymbolicLink()) fail(`${label} path must not contain symlinks.`);
    const final = current === absolutePath;
    if (!final && !stat.isDirectory()) fail(`${label} parent is not a directory.`);
    if (final && (!stat.isFile() || stat.nlink !== 1n)) fail(`${label} must be a single-link regular file.`);
    records.push({ path: current, stat });
  }
  if (records.length === 0) fail(`${label} must name a file.`);
  return records;
};

/** Read an external source/archive input without ever blocking on a swapped special node. */
export const readStableRegularInput = async (inputPath, {
  label = "Input",
  maxBytes = MAX_INPUT_BYTES,
  hooks = {},
} = {}) => {
  if (typeof inputPath !== "string" || !inputPath) fail(`${label} path is required.`);
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 0) fail(`${label} maxBytes is invalid.`);
  const absolutePath = path.resolve(inputPath);
  const beforeChain = await collectAbsoluteInputChain(absolutePath, label);
  const expected = beforeChain.at(-1).stat;
  if (expected.size > BigInt(maxBytes)) fail(`${label} exceeds ${maxBytes} bytes.`);
  await hooks.afterPreflight?.({ absolutePath });

  let handle;
  try {
    handle = await open(
      absolutePath,
      fsConstants.O_RDONLY | (fsConstants.O_NONBLOCK ?? 0) | (fsConstants.O_NOFOLLOW ?? 0),
    );
  } catch (error) {
    if (["ELOOP", "EMLINK", "ENXIO", "ENODEV", "EOPNOTSUPP"].includes(error?.code)) {
      fail(`${label} changed or is not a regular file.`);
    }
    throw error;
  }
  try {
    const before = await handle.stat({ bigint: true });
    if (!before.isFile() || before.nlink !== 1n || !sameStableFileState(before, expected)) {
      fail(`${label} changed or is not a single-link regular file.`);
    }
    const openedPath = process.platform === "linux"
      ? await realpath(`/proc/self/fd/${handle.fd}`).catch(() => null)
      : await realpath(absolutePath).catch(() => null);
    if (openedPath !== absolutePath) fail(`${label} descriptor has an unexpected path.`);
    await hooks.afterOpen?.({ absolutePath, descriptor: handle.fd });
    const bytes = await readDescriptorBounded(handle, maxBytes, label);
    const after = await handle.stat({ bigint: true });
    if (!sameStableFileState(before, after)) fail(`${label} mutated during descriptor read.`);
    const afterChain = await collectAbsoluteInputChain(absolutePath, label);
    assertUnchangedSourceChain(beforeChain, afterChain, absolutePath);
    if (!sameStableFileState(after, afterChain.at(-1).stat)) fail(`${label} descriptor no longer matches its path.`);
    return bytes;
  } finally {
    await handle.close();
  }
};

/** Read a canonical site-shell gzip at its explicit compressed-file ceiling. */
export const readStableSiteShellArchive = async (inputPath, {
  label = "Artifact input",
  hooks = {},
} = {}) => readStableRegularInput(inputPath, {
  label,
  maxBytes: MAX_ARCHIVE_FILE_BYTES,
  hooks,
});

const assertUnchangedSourceChain = (before, after, safePath) => {
  if (before.length !== after.length) fail(`Source path changed during read: ${safePath}`);
  before.forEach((record, index) => {
    const unchanged = index === before.length - 1
      ? sameStableFileState(record.stat, after[index].stat)
      : sameFileIdentity(record.stat, after[index].stat);
    if (record.path !== after[index].path || !unchanged) {
      fail(`Source path changed during read: ${safePath}`);
    }
  });
};

export const readRegularSource = async (repoRoot, relativePath, hooks = {}) => {
  const safePath = validateSafeRelativePath(relativePath, "source");
  const realRoot = await realpath(path.resolve(repoRoot));
  const absolutePath = path.resolve(realRoot, ...safePath.split("/"));
  if (!isContainedPath(realRoot, absolutePath) || absolutePath === realRoot) {
    fail(`Source escapes repository root: ${safePath}`);
  }
  const beforeChain = await collectSourceChain(realRoot, safePath);
  await hooks.afterPreflight?.({ absolutePath, realRoot });

  let handle;
  try {
    handle = await open(
      absolutePath,
      fsConstants.O_RDONLY | (fsConstants.O_NONBLOCK ?? 0) | (fsConstants.O_NOFOLLOW ?? 0),
    );
  } catch (error) {
    if (["ELOOP", "EMLINK"].includes(error?.code)) fail(`Symlink source exchange was rejected: ${safePath}`);
    throw error;
  }
  try {
    const descriptorBefore = await handle.stat({ bigint: true });
    if (!descriptorBefore.isFile() || descriptorBefore.nlink !== 1n) {
      fail(`Opened source is not a single-link regular file: ${safePath}`);
    }
    const expectedFile = beforeChain.at(-1).stat;
    if (!sameStableFileState(descriptorBefore, expectedFile)) fail(`Source file changed before descriptor open: ${safePath}`);

    let openedPath;
    try {
      openedPath = process.platform === "linux"
        ? await realpath(`/proc/self/fd/${handle.fd}`)
        : await realpath(absolutePath);
    } catch {
      fail(`Could not prove the actual opened source path: ${safePath}`);
    }
    if (!isContainedPath(realRoot, openedPath) || openedPath !== absolutePath) {
      fail(`Opened source escaped or changed its repository path: ${safePath}`);
    }

    await hooks.afterOpen?.({ absolutePath, descriptor: handle.fd, realRoot });
    const bytes = await readDescriptorBounded(handle, MAX_INPUT_BYTES, `Source ${safePath}`);
    const descriptorAfter = await handle.stat({ bigint: true });
    if (!sameStableFileState(descriptorBefore, descriptorAfter)) fail(`Source file mutated during descriptor read: ${safePath}`);

    const afterChain = await collectSourceChain(realRoot, safePath);
    assertUnchangedSourceChain(beforeChain, afterChain, safePath);
    if (!sameStableFileState(descriptorAfter, afterChain.at(-1).stat)) fail(`Source descriptor no longer matches its path: ${safePath}`);
    const finalPath = await realpath(absolutePath);
    if (finalPath !== absolutePath || !isContainedPath(realRoot, finalPath)) fail(`Source path escaped after read: ${safePath}`);
    return bytes;
  } finally {
    await handle.close();
  }
};

const parseJson = (bytes, label) => {
  let value;
  try {
    value = JSON.parse(bytes.toString("utf8"));
  } catch (error) {
    fail(`${label} is not valid JSON: ${error.message}`);
  }
  return value;
};

export const validateContract = (contract, contractPath = DEFAULT_CONTRACT_PATH) => {
  assertExactKeys(contract, CONTRACT_KEYS, "Site-shell contract");
  if (contract.$schema !== "./contract.schema.json") fail("Contract $schema must be ./contract.schema.json.");
  if (contract.schemaVersion !== 1) fail("Contract schemaVersion must be 1.");
  if (contract.name !== "@verdify/site-shell") fail("Unexpected contract name.");
  if (!CONTRACT_VERSION_PATTERN.test(contract.contractVersion)) fail("contractVersion must be exact numeric version x.y.z.");
  validateArtifactRoot(contract.artifactRoot);
  if (contract.artifactRoot !== `verdify-site-shell-${contract.contractVersion}`) {
    fail("artifactRoot must include the exact contractVersion.");
  }
  if (contract.manifestPath !== "MANIFEST.json") fail("manifestPath must be MANIFEST.json.");

  assertExactKeys(contract.installation, INSTALLATION_KEYS, "installation");
  if (contract.installation.directoryMode !== NORMALIZATION.directoryMode) fail(`installation.directoryMode must be ${NORMALIZATION.directoryMode}.`);
  if (contract.installation.fileMode !== NORMALIZATION.fileMode) fail(`installation.fileMode must be ${NORMALIZATION.fileMode}.`);
  if (contract.installation.requiresPinnedArchiveDigest !== true) fail("installation.requiresPinnedArchiveDigest must be true.");
  if (contract.installation.requiresPinnedSourceTreeDigest !== true) fail("installation.requiresPinnedSourceTreeDigest must be true.");

  if (!Array.isArray(contract.dependencies) || contract.dependencies.length === 0) {
    fail("Contract dependencies must be a non-empty array.");
  }
  const dependencyNames = new Set();
  contract.dependencies.forEach((name, index) => {
    if (typeof name !== "string" || !name || name.startsWith("/") || name.includes("\\")) {
      fail(`Contract dependency ${index} is invalid.`);
    }
    if (dependencyNames.has(name)) fail(`Duplicate contract dependency: ${name}`);
    dependencyNames.add(name);
  });
  const sortedDependencies = [...contract.dependencies].sort(compareUtf8);
  if (contract.dependencies.some((name, index) => name !== sortedDependencies[index])) {
    fail("Contract dependencies must be UTF-8 bytewise sorted.");
  }

  if (!Array.isArray(contract.payload) || contract.payload.length === 0) {
    fail("Contract payload must be a non-empty array.");
  }
  const sources = new Set();
  const destinations = new Set();
  contract.payload.forEach((entry, index) => {
    assertExactKeys(entry, PAYLOAD_KEYS, `Contract payload ${index}`);
    validateSafeRelativePath(entry.source, `Contract payload ${index} source`);
    validateSafeRelativePath(entry.path, `Contract payload ${index} path`);
    if (sources.has(entry.source)) fail(`Duplicate contract source: ${entry.source}`);
    if (destinations.has(entry.path)) fail(`Duplicate artifact path: ${entry.path}`);
    sources.add(entry.source);
    destinations.add(entry.path);
    if (!PAYLOAD_KINDS.has(entry.kind)) fail(`Unsupported payload kind: ${entry.kind}`);
    if (!TRANSFORMS.has(entry.transform)) fail(`Unsupported payload transform: ${entry.transform}`);
    if (entry.mode !== NORMALIZATION.fileMode) fail(`Payload mode must be ${NORMALIZATION.fileMode}.`);
  });
  if (!sources.has(contractPath)) fail(`Contract payload must include itself: ${contractPath}`);
  const validateDescriptor = (descriptor, label, expected) => {
    assertExactKeys(descriptor, PROP_DESCRIPTOR_KEYS, label);
    if (descriptor.type !== expected.type) fail(`${label}.type must be ${expected.type}.`);
    if (descriptor.required !== expected.required) fail(`${label}.required must be ${expected.required}.`);
    if (descriptor.default !== expected.default) fail(`${label}.default is invalid.`);
    if (typeof descriptor.semantics !== "string" || !descriptor.semantics.trim()) fail(`${label}.semantics must be non-empty.`);
    if (expected.enum === null) {
      if (descriptor.enum !== null) fail(`${label}.enum must be null.`);
    } else if (
      !Array.isArray(descriptor.enum)
      || descriptor.enum.length !== expected.enum.length
      || descriptor.enum.some((value, index) => value !== expected.enum[index])
    ) {
      fail(`${label}.enum is invalid.`);
    }
  };
  assertExactKeys(contract.componentApi, COMPONENT_API_KEYS, "componentApi");
  if (contract.componentApi.schemaVersion !== 1) fail("componentApi.schemaVersion must be 1.");
  assertExactKeys(contract.componentApi.sharedProps, SHARED_PROP_KEYS, "componentApi.sharedProps");
  validateDescriptor(contract.componentApi.sharedProps.currentSite, "componentApi.sharedProps.currentSite", {
    type: "site-shell-site", required: false, default: "corporate", enum: ["corporate", "lab"],
  });
  validateDescriptor(contract.componentApi.sharedProps.corporateOrigin, "componentApi.sharedProps.corporateOrigin", {
    type: "absolute-http-origin", required: false, default: "https://verdify.ai", enum: null,
  });
  validateDescriptor(contract.componentApi.sharedProps.labOrigin, "componentApi.sharedProps.labOrigin", {
    type: "absolute-http-origin", required: false, default: "https://lab.verdify.ai", enum: null,
  });
  validateDescriptor(contract.componentApi.sharedProps.brand, "componentApi.sharedProps.brand", {
    type: "site-shell-brand", required: false, default: "$currentSite", enum: ["corporate", "lab"],
  });
  validateDescriptor(contract.componentApi.sharedProps.homeHref, "componentApi.sharedProps.homeHref", {
    type: "local-root-relative-href", required: false, default: "/", enum: null,
  });

  assertExactKeys(contract.componentApi.types, COMPONENT_TYPE_KEYS, "componentApi.types");
  const breadcrumbItem = contract.componentApi.types.BreadcrumbItem;
  assertExactKeys(breadcrumbItem, OBJECT_TYPE_DESCRIPTOR_KEYS, "componentApi.types.BreadcrumbItem");
  if (breadcrumbItem.type !== "object") fail("componentApi.types.BreadcrumbItem.type must be object.");
  if (breadcrumbItem.additionalProperties !== false) fail("componentApi.types.BreadcrumbItem.additionalProperties must be false.");
  if (
    !Array.isArray(breadcrumbItem.required)
    || breadcrumbItem.required.length !== 2
    || breadcrumbItem.required[0] !== "name"
    || breadcrumbItem.required[1] !== "href"
  ) {
    fail("componentApi.types.BreadcrumbItem.required must be [name, href].");
  }
  assertExactKeys(breadcrumbItem.properties, BREADCRUMB_ITEM_KEYS, "componentApi.types.BreadcrumbItem.properties");
  validateDescriptor(breadcrumbItem.properties.name, "componentApi.types.BreadcrumbItem.properties.name", {
    type: "string", required: true, default: null, enum: null,
  });
  validateDescriptor(breadcrumbItem.properties.href, "componentApi.types.BreadcrumbItem.properties.href", {
    type: "local-root-relative-href", required: true, default: null, enum: null,
  });
  validateDescriptor(breadcrumbItem.properties.targetSite, "componentApi.types.BreadcrumbItem.properties.targetSite", {
    type: "breadcrumb-target", required: false, default: "$defaultItemTarget", enum: ["corporate", "lab", "current"],
  });

  assertExactKeys(contract.componentApi.components, COMPONENT_KEYS, "componentApi.components");
  const validateComponent = (name, expectedRequired, expectedPropKeys) => {
    const component = contract.componentApi.components[name];
    assertExactKeys(component, COMPONENT_DESCRIPTOR_KEYS, `componentApi.components.${name}`);
    if (
      !Array.isArray(component.requiredProps)
      || component.requiredProps.length !== expectedRequired.length
      || component.requiredProps.some((value, index) => value !== expectedRequired[index])
    ) {
      fail(`componentApi.components.${name}.requiredProps is invalid.`);
    }
    assertExactKeys(component.props, expectedPropKeys, `componentApi.components.${name}.props`);
  };
  validateComponent("Header", [], []);
  validateComponent("Footer", [], ["description"]);
  validateDescriptor(contract.componentApi.components.Footer.props.description, "componentApi.components.Footer.props.description", {
    type: "string", required: false, default: "$currentSite.description", enum: null,
  });
  validateComponent("Breadcrumbs", ["items"], ["defaultItemTarget", "items"]);
  validateDescriptor(contract.componentApi.components.Breadcrumbs.props.items, "componentApi.components.Breadcrumbs.props.items", {
    type: "breadcrumb-item-array", required: true, default: null, enum: null,
  });
  validateDescriptor(contract.componentApi.components.Breadcrumbs.props.defaultItemTarget, "componentApi.components.Breadcrumbs.props.defaultItemTarget", {
    type: "breadcrumb-target", required: false, default: "current", enum: ["corporate", "lab", "current"],
  });
  return contract;
};

export const loadContract = async (repoRoot, contractPath = DEFAULT_CONTRACT_PATH) => {
  const bytes = await readRegularSource(repoRoot, contractPath);
  return validateContract(parseJson(bytes, contractPath), contractPath);
};

const replaceExactlyOnce = (value, search, replacement, label) => {
  const first = value.indexOf(search);
  if (first === -1 || value.indexOf(search, first + search.length) !== -1) {
    fail(`${label} expected exactly one occurrence of ${JSON.stringify(search)}.`);
  }
  return `${value.slice(0, first)}${replacement}${value.slice(first + search.length)}`;
};

export const applyPayloadTransform = (sourceBytes, transform, source) => {
  if (transform === "identity") return Buffer.from(sourceBytes);
  if (transform !== "vendored-css-v1") fail(`Unknown transform: ${transform}`);

  let css = sourceBytes.toString("utf8");
  if (!Buffer.from(css, "utf8").equals(sourceBytes)) fail(`${source} must be valid UTF-8.`);
  css = replaceExactlyOnce(
    css,
    '@import "tailwindcss";',
    '@import "tailwindcss";\n@source "../components";',
    source,
  );
  css = replaceExactlyOnce(
    css,
    'url("@fontsource-variable/ibm-plex-sans/files/ibm-plex-sans-latin-wght-normal.woff2")',
    'url("/assets/verdify-site-shell/fonts/ibm-plex-sans-latin-wght-normal.woff2")',
    source,
  );
  css = replaceExactlyOnce(
    css,
    'url("@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2")',
    'url("/assets/verdify-site-shell/fonts/ibm-plex-mono-latin-400-normal.woff2")',
    source,
  );
  if (/(?:@import\s+(?:url\()?|url\()["']?https?:\/\//i.test(css)) {
    fail(`${source} contains a CDN-backed CSS import or asset URL.`);
  }
  return Buffer.from(css, "utf8");
};

const resolveDependencies = async (repoRoot, names) => {
  const lockBytes = await readRegularSource(repoRoot, "package-lock.json");
  const lock = parseJson(lockBytes, "package-lock.json");
  assertPlainObject(lock.packages, "package-lock.json packages");

  return Promise.all(names.map(async (name) => {
    const packageEntry = lock.packages[`node_modules/${name}`];
    if (!isPlainObject(packageEntry) || typeof packageEntry.version !== "string" || !DEPENDENCY_VERSION_PATTERN.test(packageEntry.version)) {
      fail(`package-lock.json does not pin an exact supported version for ${name}.`);
    }
    const installedPackagePath = `node_modules/${name}/package.json`;
    const installedPackage = parseJson(await readRegularSource(repoRoot, installedPackagePath), installedPackagePath);
    if (installedPackage.version !== packageEntry.version) {
      fail(`${name} installed version ${String(installedPackage.version)} does not match lockfile ${packageEntry.version}.`);
    }
    return {
      name,
      version: packageEntry.version,
      source: "package-lock.json",
    };
  }));
};

const computeSourceTreeDigest = (sourceRecords, dependencies) => {
  const hash = createHash("sha256");
  hash.update("verdify-site-shell-source-tree-v1\0", "utf8");
  [...sourceRecords]
    .sort((left, right) => compareUtf8(left.source, right.source) || compareUtf8(left.path, right.path))
    .forEach((record) => {
      hash.update(record.source, "utf8");
      hash.update("\0", "utf8");
      hash.update(record.path, "utf8");
      hash.update("\0", "utf8");
      hash.update(record.kind, "utf8");
      hash.update("\0", "utf8");
      hash.update(record.transform, "utf8");
      hash.update("\0", "utf8");
      hash.update(String(record.mode), "utf8");
      hash.update("\0", "utf8");
      hash.update(record.sourceSha256, "utf8");
      hash.update("\0", "utf8");
    });
  [...dependencies].sort((left, right) => compareUtf8(left.name, right.name)).forEach((dependency) => {
    hash.update(dependency.name, "utf8");
    hash.update("\0", "utf8");
    hash.update(dependency.version, "utf8");
    hash.update("\0", "utf8");
    hash.update(dependency.source, "utf8");
    hash.update("\0", "utf8");
  });
  return `sha256:${hash.digest("hex")}`;
};

const stableJson = (value) => Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");

const splitUstarPath = (entryPath) => {
  const pathBytes = Buffer.byteLength(entryPath, "utf8");
  if (pathBytes <= 100) return { name: entryPath, prefix: "" };
  const slashIndexes = [...entryPath].flatMap((character, index) => character === "/" ? [index] : []);
  for (let index = slashIndexes.length - 1; index >= 0; index -= 1) {
    const splitAt = slashIndexes[index];
    const prefix = entryPath.slice(0, splitAt);
    const name = entryPath.slice(splitAt + 1);
    if (Buffer.byteLength(prefix, "utf8") <= 155 && Buffer.byteLength(name, "utf8") <= 100) {
      return { name, prefix };
    }
  }
  fail(`Archive path is too long for USTAR: ${entryPath}`);
};

const writeAscii = (buffer, offset, length, value, label) => {
  const bytes = Buffer.from(value, "ascii");
  if (bytes.length > length) fail(`${label} exceeds USTAR field width.`);
  bytes.copy(buffer, offset);
};

const octalField = (value, length, label) => {
  if (!Number.isSafeInteger(value) || value < 0) fail(`${label} must be a non-negative safe integer.`);
  const octal = value.toString(8);
  if (octal.length > length - 1) fail(`${label} exceeds USTAR octal field width.`);
  return `${octal.padStart(length - 1, "0")}\0`;
};

const createUstarHeader = (entry) => {
  const header = Buffer.alloc(512);
  const { name, prefix } = splitUstarPath(entry.path);
  writeAscii(header, 0, 100, name, "name");
  writeAscii(header, 100, 8, octalField(entry.mode, 8, "mode"), "mode");
  writeAscii(header, 108, 8, octalField(NORMALIZATION.uid, 8, "uid"), "uid");
  writeAscii(header, 116, 8, octalField(NORMALIZATION.gid, 8, "gid"), "gid");
  writeAscii(header, 124, 12, octalField(entry.content.length, 12, "size"), "size");
  writeAscii(header, 136, 12, octalField(NORMALIZATION.mtime, 12, "mtime"), "mtime");
  header.fill(0x20, 148, 156);
  header[156] = 0x30;
  writeAscii(header, 257, 6, "ustar\0", "magic");
  writeAscii(header, 263, 2, "00", "version");
  writeAscii(header, 329, 8, octalField(0, 8, "device major"), "device major");
  writeAscii(header, 337, 8, octalField(0, 8, "device minor"), "device minor");
  writeAscii(header, 345, 155, prefix, "prefix");
  const checksum = header.reduce((sum, byte) => sum + byte, 0);
  writeAscii(header, 148, 8, `${checksum.toString(8).padStart(6, "0")}\0 `, "checksum");
  return header;
};

export const createDeterministicTar = (entries) => {
  if (!Array.isArray(entries) || entries.length === 0) fail("Archive entries must be a non-empty array.");
  const sorted = [...entries].sort((left, right) => compareUtf8(left.path, right.path));
  const seen = new Set();
  const parts = [];
  for (const entry of sorted) {
    validateSafeRelativePath(entry.path, "Archive entry path");
    if (seen.has(entry.path)) fail(`Duplicate archive entry: ${entry.path}`);
    seen.add(entry.path);
    if (!Buffer.isBuffer(entry.content)) fail(`Archive entry content must be a Buffer: ${entry.path}`);
    if (entry.mode !== NORMALIZATION.fileMode) fail(`Archive entry mode must be ${NORMALIZATION.fileMode}: ${entry.path}`);
    parts.push(createUstarHeader(entry), entry.content);
    const padding = (512 - (entry.content.length % 512)) % 512;
    if (padding) parts.push(Buffer.alloc(padding));
  }
  parts.push(Buffer.alloc(1024));
  const tar = Buffer.concat(parts);
  if (tar.length > MAX_ARCHIVE_BYTES) fail(`Archive exceeds ${MAX_ARCHIVE_BYTES} bytes.`);
  return tar;
};

const CRC32_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
      value = (value & 1) ? (0xedb88320 ^ (value >>> 1)) : (value >>> 1);
    }
    table[index] = value >>> 0;
  }
  return table;
})();

const crc32 = (buffer) => {
  let value = 0xffffffff;
  for (const byte of buffer) value = CRC32_TABLE[(value ^ byte) & 0xff] ^ (value >>> 8);
  return (value ^ 0xffffffff) >>> 0;
};

export const createDeterministicGzip = (content) => {
  if (!Buffer.isBuffer(content)) fail("Gzip content must be a Buffer.");
  if (content.length > MAX_ARCHIVE_BYTES) fail(`Gzip input exceeds ${MAX_ARCHIVE_BYTES} bytes.`);
  const parts = [Buffer.from([0x1f, 0x8b, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff])];
  let offset = 0;
  while (offset < content.length) {
    const length = Math.min(0xffff, content.length - offset);
    const final = offset + length === content.length;
    const header = Buffer.alloc(5);
    header[0] = final ? 0x01 : 0x00;
    header.writeUInt16LE(length, 1);
    header.writeUInt16LE((~length) & 0xffff, 3);
    parts.push(header, content.subarray(offset, offset + length));
    offset += length;
  }
  if (content.length === 0) parts.push(Buffer.from([0x01, 0x00, 0x00, 0xff, 0xff]));
  const trailer = Buffer.alloc(8);
  trailer.writeUInt32LE(crc32(content), 0);
  trailer.writeUInt32LE(content.length >>> 0, 4);
  parts.push(trailer);
  return Buffer.concat(parts);
};

export const decodeDeterministicGzip = (archive) => {
  if (!Buffer.isBuffer(archive)) fail("Archive must be a Buffer.");
  if (archive.length < 23 || archive.length > MAX_ARCHIVE_FILE_BYTES) fail("Archive gzip size is invalid.");
  const expectedHeader = Buffer.from([0x1f, 0x8b, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xff]);
  if (!archive.subarray(0, 10).equals(expectedHeader)) fail("Archive gzip header is not normalized deterministic v1.");
  const trailerOffset = archive.length - 8;
  const parts = [];
  let outputLength = 0;
  let offset = 10;
  let final = false;
  while (!final) {
    if (offset + 5 > trailerOffset) fail("Truncated stored DEFLATE block.");
    const blockHeader = archive[offset];
    if (blockHeader !== 0x00 && blockHeader !== 0x01) fail("Gzip must contain only byte-aligned stored DEFLATE blocks.");
    final = blockHeader === 0x01;
    const length = archive.readUInt16LE(offset + 1);
    const complement = archive.readUInt16LE(offset + 3);
    if ((((~length) & 0xffff) !== complement)) fail("Stored DEFLATE length check failed.");
    if (!final && length !== 0xffff) fail("Non-final stored DEFLATE blocks must use the canonical 65535-byte segmentation.");
    if (final && outputLength > 0 && length === 0) fail("Final stored DEFLATE block must be non-empty for non-empty content.");
    offset += 5;
    if (offset + length > trailerOffset) fail("Stored DEFLATE block exceeds gzip payload.");
    outputLength += length;
    if (outputLength > MAX_ARCHIVE_BYTES) fail("Expanded archive exceeds safety limit.");
    parts.push(archive.subarray(offset, offset + length));
    offset += length;
  }
  if (offset !== trailerOffset) fail("Unexpected data after final stored DEFLATE block.");
  const output = Buffer.concat(parts, outputLength);
  if (archive.readUInt32LE(trailerOffset) !== crc32(output)) fail("Gzip CRC32 mismatch.");
  if (archive.readUInt32LE(trailerOffset + 4) !== (output.length >>> 0)) fail("Gzip ISIZE mismatch.");
  return output;
};

const readNullTerminatedAscii = (buffer, start, length, label) => {
  const field = buffer.subarray(start, start + length);
  const nul = field.indexOf(0);
  const end = nul === -1 ? field.length : nul;
  if (nul !== -1 && field.subarray(nul).some((byte) => byte !== 0)) fail(`${label} has non-zero bytes after NUL.`);
  if (field.subarray(0, end).some((byte) => byte > 0x7f)) fail(`${label} must be ASCII.`);
  return field.subarray(0, end).toString("ascii");
};

const parseOctalField = (buffer, start, length, label) => {
  const field = buffer.subarray(start, start + length).toString("ascii");
  if (!/^[0-7]+\0$/.test(field)) fail(`${label} is not canonical NUL-terminated octal.`);
  const value = Number.parseInt(field.slice(0, -1), 8);
  if (!Number.isSafeInteger(value)) fail(`${label} is outside the safe integer range.`);
  return value;
};

const verifyUstarChecksum = (header) => {
  const checksumText = header.subarray(148, 156).toString("ascii");
  if (!/^[0-7]{6}\0 $/.test(checksumText)) fail("USTAR checksum field is not canonical.");
  const expected = Number.parseInt(checksumText.slice(0, 6), 8);
  const copy = Buffer.from(header);
  copy.fill(0x20, 148, 156);
  const actual = copy.reduce((sum, byte) => sum + byte, 0);
  if (actual !== expected) fail("USTAR checksum mismatch.");
};

export const parseDeterministicTar = (tar) => {
  if (!Buffer.isBuffer(tar) || tar.length < 1536 || tar.length % 512 !== 0 || tar.length > MAX_ARCHIVE_BYTES) {
    fail("USTAR payload has an invalid size.");
  }
  const entries = [];
  const seen = new Set();
  let previousPath = null;
  let offset = 0;
  while (offset < tar.length) {
    const header = tar.subarray(offset, offset + 512);
    if (header.every((byte) => byte === 0)) {
      if (offset + 1024 !== tar.length || !tar.subarray(offset).every((byte) => byte === 0)) {
        fail("USTAR must end with exactly two zero blocks.");
      }
      if (entries.length === 0) fail("USTAR contains no entries.");
      return entries;
    }
    if (entries.length >= 256) fail("USTAR entry count exceeds safety limit.");
    verifyUstarChecksum(header);
    const name = readNullTerminatedAscii(header, 0, 100, "USTAR name");
    const prefix = readNullTerminatedAscii(header, 345, 155, "USTAR prefix");
    const entryPath = prefix ? `${prefix}/${name}` : name;
    validateSafeRelativePath(entryPath, "USTAR entry path");
    const canonicalPathFields = splitUstarPath(entryPath);
    if (name !== canonicalPathFields.name || prefix !== canonicalPathFields.prefix) {
      fail(`USTAR name/prefix split is not canonical: ${entryPath}`);
    }
    if (seen.has(entryPath)) fail(`Duplicate USTAR entry: ${entryPath}`);
    if (previousPath !== null && compareUtf8(previousPath, entryPath) >= 0) fail("USTAR entries are not strictly UTF-8 bytewise sorted.");
    seen.add(entryPath);
    previousPath = entryPath;

    if (parseOctalField(header, 100, 8, "USTAR mode") !== NORMALIZATION.fileMode) fail(`Unexpected mode for ${entryPath}.`);
    if (parseOctalField(header, 108, 8, "USTAR uid") !== 0) fail(`Unexpected uid for ${entryPath}.`);
    if (parseOctalField(header, 116, 8, "USTAR gid") !== 0) fail(`Unexpected gid for ${entryPath}.`);
    const size = parseOctalField(header, 124, 12, "USTAR size");
    if (parseOctalField(header, 136, 12, "USTAR mtime") !== 0) fail(`Unexpected mtime for ${entryPath}.`);
    if (header[156] !== 0x30) fail(`Only regular-file USTAR entries are allowed: ${entryPath}`);
    if (header.subarray(157, 257).some((byte) => byte !== 0)) fail(`USTAR link field must be empty: ${entryPath}`);
    if (header.subarray(257, 263).toString("binary") !== "ustar\0" || header.subarray(263, 265).toString("ascii") !== "00") {
      fail(`USTAR magic/version mismatch: ${entryPath}`);
    }
    if (readNullTerminatedAscii(header, 265, 32, "USTAR user name") !== "") fail(`USTAR user name must be empty: ${entryPath}`);
    if (readNullTerminatedAscii(header, 297, 32, "USTAR group name") !== "") fail(`USTAR group name must be empty: ${entryPath}`);
    if (parseOctalField(header, 329, 8, "USTAR device major") !== 0) fail(`USTAR device major must be zero: ${entryPath}`);
    if (parseOctalField(header, 337, 8, "USTAR device minor") !== 0) fail(`USTAR device minor must be zero: ${entryPath}`);
    if (header.subarray(500, 512).some((byte) => byte !== 0)) fail(`USTAR reserved bytes must be zero: ${entryPath}`);

    const dataStart = offset + 512;
    const dataEnd = dataStart + size;
    const nextOffset = dataStart + Math.ceil(size / 512) * 512;
    if (dataEnd > tar.length - 1024 || nextOffset > tar.length - 1024) fail(`USTAR entry exceeds payload: ${entryPath}`);
    if (tar.subarray(dataEnd, nextOffset).some((byte) => byte !== 0)) fail(`USTAR padding must be zero: ${entryPath}`);
    entries.push({ path: entryPath, mode: NORMALIZATION.fileMode, content: Buffer.from(tar.subarray(dataStart, dataEnd)) });
    offset = nextOffset;
  }
  fail("USTAR is missing its two-block terminator.");
};

export const validateManifest = (manifest) => {
  assertExactKeys(manifest, MANIFEST_KEYS, "Manifest");
  if (manifest.schemaVersion !== 1) fail("Manifest schemaVersion must be 1.");
  assertExactKeys(manifest.contract, MANIFEST_CONTRACT_KEYS, "Manifest contract");
  if (manifest.contract.name !== "@verdify/site-shell") fail("Manifest contract name is invalid.");
  if (!CONTRACT_VERSION_PATTERN.test(manifest.contract.version)) fail("Manifest contract version must be exact numeric x.y.z.");
  if (!SOURCE_TREE_DIGEST_PATTERN.test(manifest.contract.sourceTreeDigest)) fail("Manifest sourceTreeDigest is invalid.");

  assertExactKeys(manifest.artifact, MANIFEST_ARTIFACT_KEYS, "Manifest artifact");
  if (manifest.artifact.format !== "ustar+gzip") fail("Manifest artifact format is invalid.");
  validateArtifactRoot(manifest.artifact.root);
  if (manifest.artifact.manifestPath !== "MANIFEST.json") fail("Manifest path is invalid.");
  if (manifest.artifact.createdAt !== "1970-01-01T00:00:00.000Z") fail("Manifest createdAt is not normalized.");
  assertExactKeys(manifest.artifact.normalization, NORMALIZATION_KEYS, "Manifest normalization");
  for (const [key, expected] of Object.entries(NORMALIZATION)) {
    if (manifest.artifact.normalization[key] !== expected) fail(`Manifest normalization.${key} is invalid.`);
  }

  if (!Array.isArray(manifest.dependencies) || manifest.dependencies.length === 0) fail("Manifest dependencies must be non-empty.");
  const dependencyNames = new Set();
  let previousDependency = null;
  manifest.dependencies.forEach((dependency, index) => {
    assertExactKeys(dependency, DEPENDENCY_KEYS, `Manifest dependency ${index}`);
    if (typeof dependency.name !== "string" || !dependency.name) fail(`Manifest dependency ${index} name is invalid.`);
    if (!DEPENDENCY_VERSION_PATTERN.test(dependency.version)) fail(`Manifest dependency ${dependency.name} version is invalid.`);
    if (dependency.source !== "package-lock.json") fail(`Manifest dependency ${dependency.name} source is invalid.`);
    if (dependencyNames.has(dependency.name)) fail(`Duplicate manifest dependency: ${dependency.name}`);
    if (previousDependency !== null && compareUtf8(previousDependency, dependency.name) >= 0) fail("Manifest dependencies are not sorted.");
    dependencyNames.add(dependency.name);
    previousDependency = dependency.name;
  });

  if (!Array.isArray(manifest.files) || manifest.files.length === 0) fail("Manifest files must be non-empty.");
  const filePaths = new Set();
  let previousFile = null;
  manifest.files.forEach((file, index) => {
    assertExactKeys(file, FILE_KEYS, `Manifest file ${index}`);
    validateSafeRelativePath(file.path, `Manifest file ${index} path`);
    validateSafeRelativePath(file.source, `Manifest file ${index} source`);
    if (!PAYLOAD_KINDS.has(file.kind)) fail(`Manifest file ${file.path} kind is invalid.`);
    if (!TRANSFORMS.has(file.transform)) fail(`Manifest file ${file.path} transform is invalid.`);
    if (file.mode !== NORMALIZATION.fileMode) fail(`Manifest file ${file.path} mode is invalid.`);
    if (!Number.isSafeInteger(file.size) || file.size < 0 || file.size > MAX_ARCHIVE_BYTES) fail(`Manifest file ${file.path} size is invalid.`);
    if (!SHA256_PATTERN.test(file.sha256)) fail(`Manifest file ${file.path} sha256 is invalid.`);
    if (filePaths.has(file.path)) fail(`Duplicate manifest file path: ${file.path}`);
    if (previousFile !== null && compareUtf8(previousFile, file.path) >= 0) fail("Manifest files are not sorted.");
    filePaths.add(file.path);
    previousFile = file.path;
  });
  return manifest;
};

const manifestsEqual = (left, right) => stableJson(left).equals(stableJson(right));

export const verifyArtifactBuffer = (archive, { expectedManifest, forbiddenHostPath } = {}) => {
  if (forbiddenHostPath && archive.includes(Buffer.from(forbiddenHostPath, "utf8"))) {
    fail("Archive contains an absolute host path.");
  }
  const tar = decodeDeterministicGzip(archive);
  const entries = parseDeterministicTar(tar);
  const entryMap = new Map(entries.map((entry) => [entry.path, entry]));
  const roots = new Set(entries.map((entry) => entry.path.split("/")[0]));
  if (roots.size !== 1) fail("Archive entries must share exactly one artifact root.");
  const [archiveRoot] = roots;
  const manifestEntryPath = `${archiveRoot}/MANIFEST.json`;
  const manifestEntry = entryMap.get(manifestEntryPath);
  if (!manifestEntry) fail("Archive is missing MANIFEST.json.");
  const manifest = validateManifest(parseJson(manifestEntry.content, "MANIFEST.json"));
  if (manifest.artifact.root !== archiveRoot) fail("Archive root does not match manifest artifact.root.");
  if (manifest.contract.version !== archiveRoot.replace(/^verdify-site-shell-/, "")) fail("Archive root version does not match manifest contract version.");

  const expectedEntryPaths = [
    manifestEntryPath,
    ...manifest.files.map((file) => `${archiveRoot}/${file.path}`),
  ].sort(compareUtf8);
  const actualEntryPaths = entries.map((entry) => entry.path);
  if (
    expectedEntryPaths.length !== actualEntryPaths.length
    || expectedEntryPaths.some((entryPath, index) => entryPath !== actualEntryPaths[index])
  ) {
    fail("Archive entries do not exactly match the manifest file list.");
  }

  for (const file of manifest.files) {
    const entry = entryMap.get(`${archiveRoot}/${file.path}`);
    if (!entry) fail(`Archive payload is missing: ${file.path}`);
    if (entry.mode !== file.mode) fail(`Archive mode mismatch: ${file.path}`);
    if (entry.content.length !== file.size) fail(`Archive size mismatch: ${file.path}`);
    if (sha256(entry.content) !== file.sha256) fail(`Archive SHA-256 mismatch: ${file.path}`);
  }

  if (expectedManifest && !manifestsEqual(manifest, expectedManifest)) {
    fail("Archive manifest does not match the expected source tree.");
  }
  return { manifest, entries };
};

export const prepareSiteShellArtifact = async ({ repoRoot, contractPath = DEFAULT_CONTRACT_PATH }) => {
  const contract = await loadContract(repoRoot, contractPath);
  const dependencies = (await resolveDependencies(repoRoot, contract.dependencies)).sort((left, right) => compareUtf8(left.name, right.name));
  const sourceRecords = [];
  const payloads = [];
  for (const entry of contract.payload) {
    const sourceBytes = await readRegularSource(repoRoot, entry.source);
    const content = applyPayloadTransform(sourceBytes, entry.transform, entry.source);
    sourceRecords.push({ ...entry, sourceSha256: sha256(sourceBytes) });
    payloads.push({ ...entry, content });
  }
  payloads.sort((left, right) => compareUtf8(left.path, right.path));

  const files = payloads.map((payload) => ({
    path: payload.path,
    source: payload.source,
    kind: payload.kind,
    transform: payload.transform,
    mode: payload.mode,
    size: payload.content.length,
    sha256: sha256(payload.content),
  }));
  const manifest = validateManifest({
    schemaVersion: 1,
    contract: {
      name: contract.name,
      version: contract.contractVersion,
      sourceTreeDigest: computeSourceTreeDigest(sourceRecords, dependencies),
    },
    artifact: {
      format: "ustar+gzip",
      root: contract.artifactRoot,
      manifestPath: contract.manifestPath,
      createdAt: "1970-01-01T00:00:00.000Z",
      normalization: { ...NORMALIZATION },
    },
    dependencies,
    files,
  });
  const entries = [
    {
      path: `${contract.artifactRoot}/${contract.manifestPath}`,
      mode: NORMALIZATION.fileMode,
      content: stableJson(manifest),
    },
    ...payloads.map((payload) => ({
      path: `${contract.artifactRoot}/${payload.path}`,
      mode: payload.mode,
      content: payload.content,
    })),
  ];
  const archive = createDeterministicGzip(createDeterministicTar(entries));
  verifyArtifactBuffer(archive, { expectedManifest: manifest, forbiddenHostPath: path.resolve(repoRoot) });
  return { archive, manifest, contract };
};

export const verifySiteShellArtifact = async ({ repoRoot, archive, contractPath = DEFAULT_CONTRACT_PATH }) => {
  const expected = await prepareSiteShellArtifact({ repoRoot, contractPath });
  return verifyArtifactBuffer(archive, {
    expectedManifest: expected.manifest,
    forbiddenHostPath: path.resolve(repoRoot),
  });
};
