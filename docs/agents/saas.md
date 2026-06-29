# Agent: `saas`

Cloud migration: Cloud Run services, Cloud SQL, GCE Mosquitto, Cloud Scheduler, Firebase Auth, the React web app, and every step toward multi-tenant.

## Owns

- All GCP resources (Cloud Run, Cloud SQL, GCE, Pub/Sub, Cloud Scheduler, load balancer, managed certs)
- `cloud-sync` related scripts (data sync between local TSDB → Cloud SQL)
- Future `app/` directory (React frontend when it exists)
- Cloud Run service definitions for ingestor, setpoints, api, planner
- Cloudflare DNS records for `verdify.ai` subdomains (cloud, api, mqtt, app, auth, dashboard)

## Does not own

- Local production systems (on-prem VM, Docker compose stack) — those stay with the owning agent of each subsystem
- The ESP32 firmware — cloud fallback in firmware is a coordinator-scheduled handoff (Sprint 10 B10.6/7)
- Schemas (shared; coordinator)

## Handshakes

| With agent | When | Protocol |
|---|---|---|
| `ingestor` | Adding a table or view that needs to replicate to Cloud SQL | Ingestor/coordinator migrates both TSDB + Cloud SQL; saas updates cloud-sync cadence if needed |
| `web` | Deploying an API endpoint to Cloud Run that already exists locally | Web defines the endpoint; saas builds the Cloud Run service against the same code |
| `genai` | Cloud planner (Gemini on Cloud Run Job) needs a prompt change | Genai changes `templates/`; saas redeploys the Cloud Run Job |
| `coordinator` | Anything that changes multi-tenancy rules (`greenhouse_id` handling, auth scope) | Coordinator reviews — these ripple everywhere |

## Gates

- Every new table must have `greenhouse_id` column (default `'vallery'`).
- Every new script must accept `--greenhouse-id`.
- Every new endpoint routes through `/greenhouses/{id}/`.
- No credentials in container images — Secret Manager only.
- Cloud changes that affect live routing (DNS, load balancer) require coordinator approval.

## Ask coordinator before

- Changing the production DNS (`verdify.ai` records)
- Migrating from Cloud SQL to AlloyDB or swapping database providers
- Switching Firebase Auth tenants or touching OAuth config
- Merging anything that flips the ESP32 over to cloud-only

## Current state

Full cloud mirror shipped 2026-04-07 (Sprint 9). Cloud planner is in dry-run.
Use GitHub issues for current SaaS/platform work.

The old `docs/backlog/saas.md` roadmap is archived in
`/Users/jason/Orbit/context_dump/verdify-platform/`.
