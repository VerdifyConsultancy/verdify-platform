import base64
import datetime
import hashlib
import json
import pathlib
import subprocess

b = pathlib.Path("/private/tmp/verdify-daytime-consumer-20261018")
r = pathlib.Path("/private/tmp/verdify-daytime-consumer-source-20261018")
repo = "VerdifyConsultancy/verdify-platform"
branch = "codex/daytime-consumer-evidence-20261018"


def git(*a):
    return subprocess.check_output(["git", *a], cwd=r).decode().strip()


def gh(path):
    return json.loads(subprocess.check_output(["gh", "api", "repos/" + repo + "/" + path], cwd=r, timeout=60))


commit = git("rev-parse", branch)
ref = gh("git/ref/heads/" + branch)
assert ref["object"]["sha"] == commit
main = "af46d263352d6403201a4642378197e9ca00303c"
assert gh("git/ref/heads/main")["object"]["sha"] == main
assert git("rev-parse", commit + "^") == main
prefix = "planning/reviews/2026-10-18-daytime-consumer/"
paths = git("ls-tree", "-r", "--name-only", commit, "--", prefix).splitlines()
tree = gh("git/trees/" + commit + "?recursive=1")
assert not tree["truncated"]
mapping = {x["path"]: x for x in tree["tree"] if x["type"] == "blob"}
rows = []
source = git("diff", "--name-only", "ff8d72e18cd08625c2a888106be1572c9c04b079", main).splitlines()
for path in sorted(set(paths + source)):
    meta = mapping[path]
    blob = gh("git/blobs/" + meta["sha"])
    assert blob["encoding"] == "base64"
    raw = base64.b64decode(blob["content"])
    expected = subprocess.check_output(["git", "show", commit + ":" + path], cwd=r)
    assert raw == expected and len(raw) == meta["size"]
    rows.append(
        {
            "path": path,
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "github_blob_sha": meta["sha"],
            "exact_remote_bytes": True,
        }
    )
out = {
    "status": "SOURCE_REPORT_TWO_ARCHIVES_AND_ALL_REMOTE_BYTES_VERIFIED",
    "source_revision": main,
    "evidence_commit": commit,
    "evidence_branch": branch,
    "main_not_mutated_by_evidence": True,
    "custody_url": f"https://github.com/{repo}/blob/{commit}/{prefix}CUSTODY.md",
    "files": rows,
    "verified_at": datetime.datetime.now(datetime.UTC).isoformat(),
}
(b / "final-remote-publication.json").open("x").write(json.dumps(out, indent=2) + "\n")
print(json.dumps({k: out[k] for k in ["status", "evidence_commit", "custody_url"]}))
