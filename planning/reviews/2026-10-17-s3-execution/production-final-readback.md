# Production checkpoint — 2026-10-18 05:05 UTC

ROOT's retained readbacks show `verdify-prod-dark` **Synced / Healthy** at 05:05:24 UTC. Argo's observed comparison revision is `5f2f488cc50ec2747fb2f07774f43434abd23ccb`; its last full sync remains `55285aafc96855419ba5e50ab11475cb71a60a0f`, completed at 02:47:03 UTC. This checkpoint reflects a comparison refresh. No new production sync or image promotion occurred.

The read-only smoke passed **9/9** checks against API source `cb21fd3d3295aa8d7183f70b0d5ac82405410b1a` and digest `sha256:75f510f52d71846197fdc1ddabead87912d8a31070439a29747e4ec36dc37624`. Two API replicas were ready; both MCP replicas passed authenticated initialization and the exact 23-tool inventory. The canonical 48-field setpoint snapshot was eight seconds old. The separate device monitor observed exactly **one ESP32 writer connection**, belonging to the ingestor.

Ingestor UID `44386520-a799-468c-ae13-fe93ec9e1f1c`, exporter UID `de49c954-0ba4-42c8-8e73-146b52db7772`, and database Pod UID `b1fa2c0e-a995-481f-a29f-4cfaad4e697b` match the earlier [#977](977-three-replicas-20261017.md) and [#980](980-exporter-acceptance.md) acceptance. All were Running and Ready with zero restarts. The production database's Longhorn volume was attached and healthy, with three running RW replicas on three distinct nodes and no rebuild in progress.

The exporter returned HTTP 200 on its source-defined port **8080** in **0.040856 seconds**, with exporter-up `1` and dump count `15`. Its last-success timestamp remained `1792225121.8947577`, matching the earlier acceptance. This readback started no backup or restore.

Two verification corrections remain explicit. Attempt 01 stopped before any smoke check because `KUBECONFIG` was unset. Attempt 02's first exporter probe used incorrect port 9100 and was refused; the corrected source port 8080 probe passed. Both original outcomes remain in private custody and do not establish a service failure.

This is a bounded runtime checkpoint. It does not turn newer source or CI results into production adoption, establish a new sustained observation window, or close remaining physical, recovery, backup, or experiment acceptance. Exact object identities and hashes of all retained receipts, metadata and logs are in [the sanitized receipt](production-final-readback.json); raw logs and resource dumps remain private.
