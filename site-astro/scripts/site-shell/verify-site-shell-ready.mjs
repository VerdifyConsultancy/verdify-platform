import path from "node:path";
import { verifySiteShellInstallReady } from "./lib/site-shell-install.mjs";

const parseArgs = (args) => {
  const values = {};
  for (let index = 0; index < args.length; index += 2) {
    const flag = args[index];
    const value = args[index + 1];
    if (
      ![
        "--archive-sha256",
        "--contract-version",
        "--destination",
        "--installed-file-count",
        "--manifest-sha256",
        "--source-tree-digest",
      ].includes(flag)
      || !value
    ) {
      throw new Error("Usage: node scripts/verify-site-shell-ready.mjs --destination DIRECTORY --archive-sha256 sha256:<hex> --source-tree-digest sha256:<hex> --manifest-sha256 sha256:<hex> --contract-version VERSION --installed-file-count COUNT");
    }
    if (values[flag]) throw new Error(`Duplicate option: ${flag}`);
    values[flag] = value;
  }
  for (const required of [
    "--archive-sha256",
    "--contract-version",
    "--destination",
    "--installed-file-count",
    "--manifest-sha256",
    "--source-tree-digest",
  ]) {
    if (!values[required]) throw new Error(`Missing required option: ${required}`);
  }
  if (!/^(?:0|[1-9][0-9]*)$/.test(values["--installed-file-count"])) {
    throw new Error("--installed-file-count must be a canonical non-negative integer.");
  }
  const installedFileCount = Number(values["--installed-file-count"]);
  if (!Number.isSafeInteger(installedFileCount)) throw new Error("--installed-file-count is outside the safe integer range.");
  return { ...values, installedFileCount };
};

const args = parseArgs(process.argv.slice(2));
const destination = path.resolve(process.cwd(), args["--destination"]);
const record = await verifySiteShellInstallReady({
  destination,
  expectedArchiveDigest: args["--archive-sha256"],
  expectedSourceTreeDigest: args["--source-tree-digest"],
  expectedContractVersion: args["--contract-version"],
  expectedInstalledFileCount: args.installedFileCount,
  expectedManifestDigest: args["--manifest-sha256"],
});

console.log(`Verified ready Verdify site shell ${record.contractVersion} at ${destination}`);
console.log(`archiveSha256=${record.archiveDigest}`);
console.log(`sourceTreeDigest=${record.sourceTreeDigest}`);
console.log(`manifestSha256=${record.manifestDigest}`);
console.log(`files=${record.installedFileCount}`);
