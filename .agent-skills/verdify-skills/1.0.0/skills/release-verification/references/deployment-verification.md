# Deployment verification

Prove identity before behavior:

1. expected commit/tag;
2. built artifact or image digest;
3. deployment target and configuration version;
4. observed running identity.

Then prove behavior through health checks, smoke/end-to-end tests, logs, metrics, external dependencies, permissions, and data/migration checks. Capture timestamps and environment for every observation.
