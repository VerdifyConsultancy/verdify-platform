# Contact verification rollout (#1008)

1. Deploy the publisher image built from `183af7a93fe236ab97f4556173a07195515b732a`: `sha256:a13dc8481a46829b5844fac733ca8717c4aaa4e9ca12fdf3b03d9286db0ae555`. Its build passed the existing Grafana transformer tests and the actual nested contact-form token lifecycle test. The API Secret reference is deliberately deferred, so legitimate submissions continue working while the new frontend publishes.
2. Wait for the supported publisher Job to complete, the served generation/custody checks to match, and the actual browser contact widget to load. Server enforcement is not yet active.
3. Activate the required central Secret reference and promote only the qualified API image `sha256:e7216c15a50863dbd28a1ca9f6e401afb8d2a27f1845594d17b1ff619f24e3b8`, from the same exact source revision. Five provider-free Siteverify boundary tests passed. Preserve every other release pin, especially the frozen ingestor and current migration runner.
4. Full non-pruning sync at the exact reviewed revision, inspect the recorded selector, verify intended image/Secret reference, public missing/invalid token denial before DB/email work, and the live contact widget. Positive submission verification must avoid sending a fabricated external email. Record actual limits of the proof.

Central Secret custody: jvallery/agents#5200. Cloudflare rules and widget: jvallery/network-infra#756, merged `18a2e03ce03584e67a1dfb0583ec0b4537ff8d23`. The literal same-URL public camera burst already passes. No firmware or device path changes.
