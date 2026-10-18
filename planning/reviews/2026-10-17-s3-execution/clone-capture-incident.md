# Clone capture exit 137: confirmed cgroup OOM

The initial kill was **a pod memory cgroup OOM at 02:36:26 UTC on October 18**, established by node5's kernel journal. It was not the later CNPG demotion shutdown. The exact target was `verdify-db-rehearsal/verdify-cnpg-s2-integration-1`, pod UID `45cbb6ef-a246-4448-9111-8d1a2ba4afad`, original container `626652c44145765027a2aefdaef986b090cefae634d7e28106c6670da6d64abb`.

The cgroup reported **6,291,456 KiB used against a 6,291,456 KiB limit (6 GiB)**, with 6,119,337,984 bytes anonymous memory. At `02:36:26.458990Z`, the kernel reported `CONSTRAINT_MEMCG`, selected a PostgreSQL process, and explicitly killed the container's other processes because `memory.oom.group` was set. The group kill included the instance manager and `psql`, explaining the observed exit 137. This identifies a cgroup limit event; it does not establish node-wide/global OOM.

Three killed PostgreSQL processes had roughly 2.29 GB, 2.25 GB and 1.40 GB of anonymous RSS. The kernel's names and host PIDs do not identify SQL, query-plan nodes or parallel-worker roles, so a specific aggregate/plan cause is not claimed. Preserve the exact submitted SQL version: the file currently inspected includes 10,000-row hash blocks, which do not prove all intermediate or final aggregates are bounded.

The chronology separates the initial kill from successful recovery:

- `02:36:26.455657`: kernel invokes OOM killer; exact pod cgroup reaches 6 GiB.
- `02:36:26.459112`: kernel group-kills the original container.
- `02:36:27`: kubelet starts the first replacement container in the same pod.
- `02:36:29.364846872`: CNPG initiates failover after observing the restarting primary unhealthy.
- `02:36:29.630179313`: the restarted former primary requests fast shutdown so it can be demoted.
- `02:36:30.717211`: cluster status records primary2.
- `02:36:33`: the demotion shutdown exits Completed/0. That later lastState masks the initial kill.
- `02:36:46`: the same pod starts again, rewinds and rejoins as a replica.
- `02:37:02.906616719`: operator reports the cluster healthy; retained metadata shows three Ready.

Retained startup/readiness failures occur during this recovery. They are not evidence of an initial liveness-probe kill. No complete capture baseline was produced, and healthy failover does not prove dataset equality or executor/lifecycle acceptance.

The justified next source step is a memory-bounded capture design that reviews every aggregate/serialization/materialization step and parallelism. `work_mem` is not a process or cgroup memory cap. Do not retry the full capture or raise resource/security limits based on recovery alone. No capture retry, database query, credential change, new Job or live mutation was performed for this diagnosis.

[Sanitized diagnosis and exact kernel evidence](clone-capture-incident.json) include the custody hashes. Raw kernel/operator/Loki files remain private. Prometheus service-proxy attempts returned HTTP 502, so historical metric values are unavailable; the authoritative kernel journal is sufficient to establish the initial OOM cause.

Input provenance: the retained `capture-input.reconstructed.private.sql` is the current input reconstruction, not a presealed submitted copy. ROOT reports the driver read the input bytes at startup but only emitted the full receipt on success. Its exact current hash and mtime are retained in `diagnosis.json`; control confirms no edits since the first temp-DDL fix, with earlier changes preceding that fix; this does not create a retrospective execution seal. Any redesigned driver must seal its inputs and attempt-start receipt before execution and preserve failure results.

ROOT stopped clone execution on the failed capture. No credential installation, synthetic fixture, behavior Job or persistent experiment mutation preceded the incident. The production database retained its original Ready pod with zero restarts. The former primary binding is invalid; any later finite qualification must be freshly admitted against primary2 and must preserve this failed attempt. Subsequent bounded metadata probes succeeded with unchanged primary2 UID and zero restarts; they are not complete data seals.
