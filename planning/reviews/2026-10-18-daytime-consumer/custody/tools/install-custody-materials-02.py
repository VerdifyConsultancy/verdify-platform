import hashlib
import json
import pathlib
import subprocess

b = pathlib.Path("/private/tmp/verdify-daytime-consumer-20261018")
r = pathlib.Path("/private/tmp/verdify-daytime-consumer-source-20261018")
p = r / "planning/reviews/2026-10-18-daytime-consumer"
branch = "codex/daytime-consumer-evidence-20261018"


def git(*a):
    return subprocess.check_output(["git", *a], cwd=r).decode().strip()


assert git("status", "--porcelain") == "" and git("rev-parse", "HEAD") == "af46d263352d6403201a4642378197e9ca00303c"
for d in ["a", "b"]:
    s = pathlib.Path("/private/tmp/verdify-daytime-evidence-packet-" + d + "-02")
    m = json.loads((s / "manifest.json").read_bytes())
    assert (
        m["status"] == "byte_preserved_archive_verified"
        and m["archive_readback_exact"]
        and hashlib.sha256((s / "evidence.tar.gz").read_bytes()).hexdigest() == m["archive_sha256"]
    )
subprocess.run(["git", "switch", "-c", branch], cwd=r, check=True)
for d in ["a", "b"]:
    src = pathlib.Path("/private/tmp/verdify-daytime-evidence-packet-" + d + "-02")
    out = p / "evidence" / ("packet-" + d)
    out.mkdir(parents=True)
    for f in src.iterdir():
        assert f.is_file()
        (out / f.name).write_bytes(f.read_bytes())
ctx = p / "custody"
ctx.mkdir()
for n in [
    "source-ci-verified.json",
    "release-ci-verified.json",
    "model-source-ci-verified.json",
    "model-release-ci-initial-verified.json",
    "model-release-ci-verified.json",
    "report-ci-verified.json",
    "final-live-status.json",
    "acceptance-summary.json",
    "planning-validation-final.json",
    "final-publication-source.json",
]:
    (ctx / n).write_bytes((b / n).read_bytes())
for prefix, files in [
    ("graph-after", ["verified.json"]),
    ("runtime-model-02", ["verified.json"]),
    ("consumer-native-v3-01", ["verified.json"]),
    ("science", ["consumer-v3-actual-independent-review.json"]),
]:
    for n in files:
        (ctx / (prefix.replace("/", "-") + "-" + n)).write_bytes((b / prefix / n).read_bytes())
for dirname, label in [
    ("verdify-daytime-selection-final-01", "selection"),
    ("verdify-daytime-selection-split-02", "partition"),
]:
    src = pathlib.Path("/private/tmp") / dirname
    out = ctx / label
    out.mkdir()
    for f in src.iterdir():
        assert f.is_file()
        (out / f.name).write_bytes(f.read_bytes())
(ctx / "completion-bindings.final.json").write_bytes((b / "completion-bindings.final.json").read_bytes())
a = json.loads((pathlib.Path("/private/tmp/verdify-daytime-evidence-packet-a-02") / "manifest.json").read_bytes())
z = json.loads((pathlib.Path("/private/tmp/verdify-daytime-evidence-packet-b-02") / "manifest.json").read_bytes())
ci = json.loads((b / "report-ci-verified.json").read_bytes())
mc = json.loads((b / "model-release-ci-verified.json").read_bytes())
text = f"""# Final immutable custody — daytime and GPT-6 Luna consumer packet

Source/report main: `af46d263352d6403201a4642378197e9ca00303c`. Live model release: `1f132f20eea07ab57c5d705a9bd626ac6fe72faf`. The [execution report](README.md) retains its report-preparation boundary. This custody supplies the later actual CI terminals, native graph match, full logs, failed attempts and accepted runtime/consumer evidence. Evidence publication uses this separate branch, with no main or runtime change.

Live Iris and the bounded qualification use GPT-6 Luna/xhigh. Exact-four operation, actual profile/API source, nine smoke checks and one writer passed. Two actual Iris-authenticated historical reads and four provider requests passed with818 output tokens, zero other tools and no retries. Oct4 preserves all46 fields/fourrows:2ineffective and2insufficient_evidence; Oct16empty/pending/NULL. Complete Oct16/17 recorded daytime zero remainsNULL because25/23minute buckets are missing. #410/#775 remainopen. Natural-cycle use/cost, physical efficacy and crop safety remain separate requirements. No full gateway cycle, firmware/tuning, experiment or database change was performed.

Final preservation release CI `{mc["workflow"]}` completed `{mc["finished_at"]}`. Final report CI `{ci["workflow"]}` completed `{ci["finished_at"]}`. Each has its actual owning Workflow/Pod UID, all declared containers exit0/restarts0, full main log and source binding in [custody](custody/). Earlier successful delivery, original failed model test, stale-integrity refusal, one supported failed-build-only recovery, premutation six-object guard refusal and unsettled runtime precondition remain preserved; no historical failure is relabeled.

Native graph matched all47openissues/143acceptanceclauses/27activeedges at report source; issue bodies match canonical. The subsequent Argo read remained Healthy/Synced at published report revision. All states, acceptance, parents, dependencies and effort values remain unchanged.

| Packet | Selected original files | Selected bytes | Compressed bytes | SHA256 |
|---|---:|---:|---:|---|
| [A: original MCP/source/release](evidence/packet-a/evidence.tar.gz) | {a["selected_task_files"]} | {a["selected_task_bytes"]} | {a["archive_bytes"]} | `{a["archive_sha256"]}` |
| [B: model/final acceptance](evidence/packet-b/evidence.tar.gz) | {z["selected_task_files"]} | {z["selected_task_bytes"]} | {z["archive_bytes"]} | `{z["archive_sha256"]}` |

Each manifest records every retained member's bytes/SHA256; archive readback is exact. The partition index proves the complete selected union, with required shared provenance. Original file32MiB/packet128MiB/member10000/depth2 limits remain. Raw node/metrics inventory, source-check fixture trees, symlinks and caches are excluded; no original acceptance/failure/CI evidence is removed. Exact retry actor metadata is byte/UID/owner/image-bound; this adds no credential exception. Public source DSN expressions and metadata-only references are separately reviewed; independent credential scan/readback is retained alongside custody. No credential values, provider reasoning/ciphertext or Secret objects are intended for publication.

A separate ingress observation is retained with bounded scope. Public API health returned200 and unauthenticated public MCP returned403 at21:15:35UTC. The existing metrics proxy failed502 before the first query, so the supplied aggregate5xx ratio/denominator was not independently verified. Internal service/consumer acceptance does not assert authenticated public MCP health, sustained ingress recovery or deployment causation. No ingress mutation or alert acknowledgement occurred.

Next execution: define supported freshness/observation semantics and collect a prospective complete recorded daytime window; observe intended consumption in a naturally scheduled ordinary planner cycle; advance authentic physical/scientific requirements independently. No user decision is pending for this completed bounded packet.
"""
(p / "CUSTODY.md").write_text(text)
print(
    json.dumps(
        {"status": "CUSTODY_MATERIALS_INSTALLED_NOT_COMMITTED", "branch": branch, "files": len(list(p.rglob("*")))}
    )
)
