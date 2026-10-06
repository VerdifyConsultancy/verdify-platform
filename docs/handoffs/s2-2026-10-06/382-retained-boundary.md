# Retained ingest baseline boundary

The accepted production image ran in a device-denied isolated qualification Pod on a separate 2 GiB claim using the same Longhorn workspace RWO class and two replicas. Three queues committed 1,000 immutable records each, then exited abruptly. A second process checked the hashes, deleted inside three uncommitted transactions with forced page spill/hot journals and exited abruptly. After removing only the stopped qualification Pods, Kubernetes reattached the same PV from node5 to node4.

All 3,000 records recovered with identical accepted hashes, FIFO, original source timestamps, runtime and writer generation. Identical replay added zero rows; changed identity payloads and capacity overflow were rejected without losing accepted rows. Recovery decode/verification took 0.478 seconds after mount; the interrupt finished at 04:22:07 UTC and recovery started at 04:22:54 UTC. Initial image-pull authentication failure occurred before execution and is preserved privately. The corrected Pods used an existing namespace pull Secret, never mounted in the process.

[Bound receipt](evidence/382/current-runtime-retained-boundary.json) identifies actual image, source module hash, Pod/claim/PV UIDs and phase timestamps. The volume detached normally after the recovery Pod completed; final Longhorn robustness is unknown while detached, not claimed healthy. Accepted records and the claim remain retained for the database join.

This proves the declared abrupt-process/interrupted-local-ACK and cross-worker retained-volume boundary. It does not prove physical host power loss, PostgreSQL replay, routed alerts or promoted-source acceptance. Those remain required joins before #382 closure. No host/node restart, NFS mutation or second device consumer was used.
