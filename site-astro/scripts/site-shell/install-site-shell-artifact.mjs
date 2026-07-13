import path from "node:path";
import { readStableSiteShellArchive } from "./lib/site-shell-artifact.mjs";
import { installSiteShellArtifact } from "./lib/site-shell-install.mjs";

const parseArgs = (args) => {
  const values = {};
  for (let index = 0; index < args.length; index += 2) {
    const flag = args[index];
    const value = args[index + 1];
    if (!["--artifact", "--archive-sha256", "--source-tree-digest", "--destination"].includes(flag) || !value) {
      throw new Error("Usage: node scripts/install-site-shell-artifact.mjs --artifact PATH --archive-sha256 sha256:<hex> --source-tree-digest sha256:<hex> --destination NEW_DIRECTORY");
    }
    if (values[flag]) throw new Error(`Duplicate option: ${flag}`);
    values[flag] = value;
  }
  for (const required of ["--artifact", "--archive-sha256", "--source-tree-digest", "--destination"]) {
    if (!values[required]) throw new Error(`Missing required option: ${required}`);
  }
  return values;
};

const args = parseArgs(process.argv.slice(2));
const artifactPath = path.resolve(process.cwd(), args["--artifact"]);
const archive = await readStableSiteShellArchive(artifactPath, { label: "Artifact input" });

const result = await installSiteShellArtifact({
  archive,
  expectedArchiveDigest: args["--archive-sha256"],
  expectedSourceTreeDigest: args["--source-tree-digest"],
  destination: path.resolve(process.cwd(), args["--destination"]),
});
console.log(`Installed Verdify site shell ${result.contractVersion} into ${result.destination}`);
console.log(`archiveSha256=${result.archiveDigest}`);
console.log(`sourceTreeDigest=${result.sourceTreeDigest}`);
console.log(`files=${result.installedFiles.length}`);
