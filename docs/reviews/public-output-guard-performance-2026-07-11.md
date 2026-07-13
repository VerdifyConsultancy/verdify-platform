# Public-output guard performance evidence — 2026-07-11

The scanner performance gate uses a materialized 424 MiB corpus: 423 distinct
1 MiB HTML files containing ordinary public prose. This intentionally exercises
the normal text path rather than sparse files or zero-filled binary fast paths.

Corpus construction:

```bash
BENCH_ROOT=$(mktemp -d /tmp/verdify-public-output-bench-423m.XXXXXXXX)
export BENCH_ROOT
yes '<article><p>ordinary public greenhouse telemetry and climate evidence remain available.</p></article>' \
  | head -c 1048576 > "$BENCH_ROOT/template.html"
for index in $(seq -w 1 423); do
  cp "$BENCH_ROOT/template.html" "$BENCH_ROOT/page-$index.html"
done
rm "$BENCH_ROOT/template.html"
du -sh "$BENCH_ROOT"
```

The first implementation exceeded 150 seconds and was stopped. Profiling a
materialized 20 MiB subset attributed 3.521 of 3.846 seconds to repeated regex
searches over ordinary prose. Syntactic eligibility gates now bypass base64,
decoded-variant, invalid-number, and data-URI parsers unless their notation is
present; eligible representations retain all fail-closed bounds.

One warm-up and three measured full CLI scans were run as child processes. The
driver used `time.perf_counter()` per process and
`resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss` for peak child RSS.

```python
import resource
import statistics
import subprocess
import sys
import time
import os

command = [sys.executable, "scripts/check-public-output.py", "--root", os.environ["BENCH_ROOT"]]
measurements = []
for run in range(4):
    started = time.perf_counter()
    completed = subprocess.run(command, stdout=subprocess.DEVNULL, check=False)
    elapsed = time.perf_counter() - started
    assert completed.returncode == 0
    if run:
        measurements.append(elapsed)
usage = resource.getrusage(resource.RUSAGE_CHILDREN)
print(measurements, statistics.median(measurements), usage.ru_maxrss)
```

```text
warmed_seconds=5.110,5.261,5.279
p50_seconds=5.261
maxrss_kib=27564
```

The rebuild pipeline builds directly into one hidden same-filesystem candidate,
scans it once, and atomically exchanges it with the live directory. There is no
post-scan copy or second scan. The guard timeout remains 120 seconds and is
validated within a 30–120 second range.

## Scan-to-promotion boundary

The scanner holds the candidate directory descriptor from the first inventory
through the final inventory and promotion. The inventory binds every entry's
device, inode, type, owner, link count, size, mtime, and ctime; symlinks,
hardlinks, special files, cross-device entries, and changes during or after the
scan fail closed. Promotion revalidates the exact descriptor, inventory, and
candidate name immediately before `renameat2`.

Linux still requires a source *name* for directory `renameat2`; an open
descriptor cannot make the final syscall inode-only. The safety boundary is
therefore explicit: the candidate parent is current-UID-owned and has no
group/world write bits, and all cooperating site initializers and publishers
take the same persistent wrapper lock. A malicious process already running as
the publisher UID and deliberately ignoring those locks is outside this
guarantee. The implementation does not claim otherwise.

## Restricted-compatible PVC layout

The shared PVC root remains writable through pod `fsGroup: 1000`. Each Lab pod
independently runs a non-root UID/GID 1000 initializer with RuntimeDefault
seccomp, no privilege escalation, a read-only root filesystem, and all
capabilities dropped. It creates `/work/publisher` mode 0700 and serves only
`/work/publisher/public`.

The nginx container mounts the stable PVC parent read-only at `/lab-cache` and
uses `/lab-cache/publisher/public` as its configured document root. It does not
use a Kubernetes `subPath` for the served directory: `subPath` would pin the
directory inode selected at pod start, while pathname resolution through the
parent mount observes each atomic exchange without restarting nginx.

Publisher and site initialization use the same persistent wrapper lock and a
`.layout-v1-ready` marker. Legacy `/work/public` or the baked fallback is copied
to a private candidate before a recoverable two-rename replacement, so a new
site container cannot mount a partially copied tree and publisher promotion
cannot overlap migration. The site extracts its baked fallback to an `emptyDir`
before taking the PVC lock; consequently either the site or publisher pod may
start first without losing the prior last-good tree.

## Mixed HLS snapshot follow-up — 2026-07-12

The `.ts` suffix is shared by TypeScript and MPEG transport streams. The guard
now probes that suffix before dispatch: UTF-8/UTF-16 source stays on the text
path, while media must have one unambiguous 188-byte packet layout, valid sync
and adaptation fields throughout, and CRC-valid single-program PAT/PMT tables.
Known transport metadata PIDs and private-data fields are scanned under a 1 MiB
per-file bound. Malformed, ambiguous, wrapped 192/204-byte, opaque, and
over-bound inputs fail closed.

The same frozen tree contains MP4 camera exports. Their top-level ISO-BMFF box
layout is validated and non-`mdat` metadata is scanned under a 4 MiB bound.
`stsc`/`stsz`/`stz2`/`stco`/`co64` sample tables must prove every skipped byte
belongs to a whitelisted audio/video sample; unreferenced `mdat` gaps and
unproven track formats are scanned or rejected fail-closed.

This driver suppresses scanner diagnostics and prints only aggregate evidence;
it cannot echo a matched protected value:

```python
import hashlib
import json
import resource
import subprocess
import sys
import time
from pathlib import Path

root = "/tmp/verdify-lab-snapshot-sanitized-20260712t1620z/content"
report = Path("/tmp/verdify-hls-guard-final-report.json")
command = [sys.executable, "scripts/check-public-output.py", "--root", root, "--json-report", str(report)]
started = time.perf_counter()
completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
elapsed = time.perf_counter() - started
payload = json.loads(report.read_text(encoding="utf-8"))
print("scanner_exit", completed.returncode)
print("elapsed_seconds", f"{elapsed:.3f}")
print("maxrss_kib", resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
print("finding_count", len(payload["findings"]))
print("report_sha256", hashlib.sha256(report.read_bytes()).hexdigest())
```

```text
files=429
bytes=409203669
scanner_exit=0
elapsed_seconds=17.712
maxrss_kib=35060
finding_count=0
report_sha256=8da094d7f9eb0957d38fff47dcd6b80f2d906676433c1b57613ad8aa632bf20d
```

## Media bypass regression follow-up — 2026-07-13

Adversarial review found two classification-order gaps: an undeclared MPEG-TS
PES payload could precede the PMT and disappear from the metadata scan, and all
top-level MP4 `mdat` bytes were skipped without proving their sample ownership.
Regression fixtures now require PAT/PMT-first declared PIDs and place protected,
invalid, base64, and UTF-16 text in an unreferenced `mdat` gap behind otherwise
valid sample tables. Unknown codecs and sample ranges outside `mdat` also fail
closed.

At `2026-07-13T07:59:07Z`, copies of the two MP4 camera exports currently served
by `lab-stage` (2 files, 101,976,633 bytes) scanned clean in 1.362 seconds. The
current 1080p HLS rendition (58 files, 150,826,788 bytes) scanned clean in 1.229
seconds. Both commands used the strengthened scanner and emitted JSON reports;
their report SHA-256 values were respectively
`8a2cd377d925c0534d1fed9a39e05b151c360b8872f5b8fc884997ecc608e80a` and
`b50cacc5714e8ff5c657644adc18e066f96de72dfa5b11413740bc110e5fb996`.

The complete current stage tree is not a replacement for the frozen clean
corpus above: its 1,184 files (453,366,254 bytes) completed in 125.060 seconds
and failed only on a pre-existing Pagefind `.pf_meta` `decode-limit`. No matched
value was printed. That independent generated-search artifact must be
remediated before this newer stage snapshot can serve as a clean acceptance
corpus.
