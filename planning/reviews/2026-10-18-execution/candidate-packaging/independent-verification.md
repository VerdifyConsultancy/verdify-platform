# Actual orchestrator candidate packaging — 2026-10-18

The one candidate-image check completed successfully. Workflow `2f234349-1f7e-4735-8c18-996b1b801f1d` reached Succeeded at 08:41:51 UTC; its sole check Pod `7500686b-e813-46aa-b1fb-5fb1c188c5b3` exited 0 without restarts. The observed immutable image is `sha256:8636ac4e3346ff9c7752d7f1f3bcef976b8797a03d45762a7361734f32c9ecad`, built from `db896d0fb2856d5caccb269185597d197e0f459d`.

Independent readback matched all 29 packaged files against exact Git blobs, the reviewed command, `/app` module origins and empty temporary working directory. The actual freezer allowlist contains all six expected functions. The collector, guard and wetting helper imported from the image, and pure collection/validation retained exactly four missing physical criteria. The analysis Markdown is present with its expected hash; no speculative Docker-context correction is needed.

The unchanged supported template ran the check as UID/GID 1001. Contrary to the initial admission assumption, Kubernetes projected the existing `argo-ci-workflow` service-account token into the main container. No application Secret volume or environment reference was present. Original preparation/admission records remain unchanged, and the actual-mount correction is explicitly retained. This review inspected metadata and output only; it did not read token values. The reviewed command clears inherited environment, does not read the projected token, and denies external operations. No syscall audit is claimed.

This is packaging and pure-contract evidence only. It does not exercise the application's normal UID10001, authenticate a database role, run the collector's native preflights, satisfy readiness/physical acceptance, deploy a service or promote an image. Ingestor packaging was not executed. The parent main pipeline was still running when this specific published artifact was admitted.

The full 9,270-byte main log is retained with SHA256 `769daf1cbedaa9d030b1328b57e68a36631ff990d44891b0f854490fe205e5c6`. The companion JSON binds actual image/Workflow/Pod identities, all source and custody hashes, exact blockers and the admission correction.
