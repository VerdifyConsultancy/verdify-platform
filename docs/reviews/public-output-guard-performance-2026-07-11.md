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
