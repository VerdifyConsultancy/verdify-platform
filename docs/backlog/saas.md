# Backlog: `saas`

Owned by the [`saas`](../agents/saas.md) agent. Cloud migration, multi-tenancy, React app.

**Last content update:** 2026-04-07 (moved into agent-scoped backlog 2026-04-18).

**Principle:** Every task moves toward multi-tenant SaaS OR keeps the greenhouse running. Nothing creates SaaS-hostile debt. When choosing between two ways to build something, pick the way that works for multi-tenant.

**Track A** (operational reliability, planner quality, crop outcomes) lives across the other agents' backlogs. This doc is Track B.

## Project recovery posture - 2026-05-22

Canonical recovery plan:
[`project-recovery-2026-05-22.md`](project-recovery-2026-05-22.md).

SaaS/product work is intentionally parked behind the recovery train unless it
directly supports Track A reliability, public-site availability, or a required
deployment/secret boundary. The active recovery plan keeps PR #80 as the
baseline, then cleans the production-linked dirty worktree, stale worktrees,
stashes, and partially deployed site/Grafana/ingestor work before resuming
multi-tenant cloud milestones.

- [x] **S-RM8 Recovery handoff review.** After RM-8, re-evaluate this backlog
  against the refocus strategy and decide which Sprint 10 cloud-hardening items
  remain active versus product-later. Decision for recovery closure: SaaS work
  remains product-later unless it directly supports Track A reliability,
  public-site availability, or a required deployment/secret boundary.

---

## Launch Support — Public Proof + Capture

Coordinated through [`docs/backlog/launch.md`](launch.md). These are launch support tasks, not a replacement for Sprint 10 cloud hardening.

- [x] **S-L0.7 Public Grafana access QA.** Server-side checks verify anonymous Grafana health, dashboard boot data, d-solo routes, and JS bundle availability; Jason should still do final mobile/in-app smoke before posting.
- [x] **S-L0.9 Public API lockdown.** Mutating crop/event/harvest/light routes now require `X-Verdify-API-Key`; unauthenticated writes fail closed, `/docs` is hidden by default, and `/openapi.json` remains noindexed for contract checks.
- [x] **S-L0.10 Robots/indexing alignment.** Traefik/API/Grafana headers and site `robots.txt` are aligned: launch pages index, raw/API/Grafana surfaces noindex.
- [x] **S-L2.2 Waitlist/newsletter decision.** Explicit no-capture decision for broad launch: defer waitlist/newsletter until after HN/Reddit so launch does not add form secrets, spam handling, or product-positioning overhead.
- [x] **S-L1.3 Public cost callout support.** Economics page now uses defensible solar-aligned/grid/gas wording and clean USD rendering.
- [x] **S-L0.1 Public infra scrub.** Public site scrubbed for local IPs, camera/security details, family names, and ambiguous cloud/no-cloud claims; API/Grafana noindex headers added.

## Sprint 7: SaaS Foundation — COMPLETE ✅

All items done. `greenhouse_id` on all tables, `greenhouses` registry table, parameterized scripts, greenhouse-scoped API endpoints.

## Sprint 8: Cloud Communication — COMPLETE ✅

All items done. GCE MQTT broker (mqtt.verdify.ai), Pub/Sub pipeline, Cloud Run ingestor, cloud setpoints endpoint, dual-path data flow (local + cloud).

## Sprint 9: Cloud SaaS Foundation — COMPLETE ✅ (2026-04-07)

Full cloud mirror deployed. See `/srv/verdify/docs/CLOUD-DEPLOYMENT.md` for details.

| # | Task | Status | Deliverable |
|---|------|--------|-------------|
| C9.1 | Cloud SQL | DONE | verdify-db (PG16, 44 tables, 54 views, 2.36M rows) |
| C9.2 | Cloud Run: Ingestor | DONE | Pub/Sub → Cloud SQL, live data every 10s |
| C9.3 | Cloud Run: Setpoints | DONE | ESP32 fallback endpoint |
| C9.4 | GCE MQTT broker | DONE | mqtt.verdify.ai, Mosquitto + Pub/Sub bridge |
| C9.5 | Historical data backfill | DONE | 2.36M rows synced, cloud-sync.py |
| C9.6 | Cloud SQL views + functions | DONE | 54 views, 23 functions, time_bucket compat |
| C9.7 | Cloud Grafana | DONE | cloud.verdify.ai, 24 dashboards |
| C9.8 | Ongoing data sync | DONE | cloud_sql_sync task (5min), cloud-sql-proxy.service |
| C9.9 | Crop catalog API | DONE | api.verdify.ai, 14 endpoints |
| C9.10 | Cloud planner | DONE | Gemini 2.5 Pro, Cloud Run Job (dry run verified) |
| C9.11 | Load balancer + SSL | DONE | Global LB (34.160.228.207), managed cert |
| C9.12 | DNS setup | DONE | 9 Cloudflare records (cloud, api, mqtt, app, auth, dashboard) |

**Milestone achieved:** The cloud can fully replace the local system. All data, dashboards, APIs, and planning exist in GCP.

---

## Sprint 10: Cloud Production + Decommission Prep — NEXT

**Goal:** Harden the cloud for production. Enable ESP32 to operate cloud-only. Begin local decommission.

### Track A: Operations

| # | Task | Effort | Notes |
|---|------|--------|-------|
| A10.1 | Enable cloud planner production | 1h | DRY_RUN=false + Cloud Scheduler (6AM/12PM/6PM MDT) |
| A10.2 | GreenLight calibration with Jason | 3h | Physical parameters for physics model |
| A10.3 | Crop health → plan integration | 2h | Gemini Vision observations inform setpoint decisions |
| A10.4 | Website crops page | 2h | verdify.ai/crops/ with health scores from API |

### Track B: Cloud Hardening

| # | Task | Effort | Why |
|---|------|--------|-----|
| B10.1 | Cloud SQL security hardening | 2h | VPC connector, Secret Manager for passwords, remove 0.0.0.0/0 |
| B10.2 | Cloud monitoring + alerting | 2h | Cloud Monitoring for Run latency, SQL connections, Pub/Sub backlog |
| B10.3 | Upgrade Cloud SQL tier | 1h | Evaluate db-f1-micro. Consider AlloyDB for analytical queries. |
| B10.4 | HTTPS verification + HTTP redirect | 30m | Verify cert, add HTTP→HTTPS redirect rule on LB |
| B10.5 | Move credentials to Secret Manager | 2h | DB passwords, MQTT creds, API keys → Secret Manager refs |

### Track B: ESP32 Cloud Independence

| # | Task | Effort | Why |
|---|------|--------|-----|
| B10.6 | ESP32 cloud setpoints fallback | 3h | Firmware OTA: add cloud URL as secondary setpoint source |
| B10.7 | ESP32 direct MQTT to cloud | 3h | Firmware OTA: publish to mqtt.verdify.ai directly |
| B10.8 | Cloud-only test window | 4h | 24h test: disable local ingestor, verify ESP32 runs from cloud |

### Milestone: After Sprint 10
- ESP32 can operate with zero local infrastructure
- Cloud is production-hardened (secrets, monitoring, alerting)
- Local decommission is a DNS/config change, not a rebuild

---

## Sprint 11: Multi-Tenant MVP (4-6 Weeks)

**Goal:** A second greenhouse (simulated or real) runs through the cloud.

### Track B: SaaS Core

| # | Task | Effort | Notes |
|---|------|--------|-------|
| B11.1 | Firebase Auth setup | 4h | Google Sign-In, user registration at auth.verdify.ai |
| B11.2 | React web app scaffold | 8h | app.verdify.ai — dashboard, crops, plan viewer. James frontend. |
| B11.3 | Greenhouse registration flow | 4h | Create greenhouse → provision MQTT creds → generate ESP32 config |
| B11.4 | Per-greenhouse dashboard routing | 2h | dashboard.verdify.ai/{id} → filtered views |
| B11.5 | Device provisioning prototype | 4h | Web page generates ESPHome YAML with user's MQTT credentials |
| B11.6 | Simulated second greenhouse | 4h | Test data generator that publishes fake sensor data for "greenhouse-2" |
| B11.7 | Multi-tenant API auth | 4h | Firebase token validation, greenhouse-scoped data access |
| B11.8 | Onboarding wizard | 4h | Step-by-step: account → greenhouse → controller → first dashboard |

### Milestone: After Sprint 11
- A user can sign up, create a greenhouse, and see a dashboard
- The Vallery greenhouse is "just another tenant" (zero special treatment)
- Simulated greenhouse proves multi-tenant data isolation

---

## Sprint 12+: Scale + Polish

| # | Task | Notes |
|---|------|-------|
| S12.1 | BigQuery analytics pipeline | Pub/Sub → BigQuery subscription for long-term analytics |
| S12.2 | AlloyDB migration | If Cloud SQL performance becomes a bottleneck |
| S12.3 | GreenLight integration | Physics model as Cloud Run sidecar for plan scoring |
| S12.4 | Mobile app (React Native) | Push notifications, quick checks |
| S12.5 | Marketplace for grow profiles | Shared crop settings across greenhouses |
| S12.6 | Hardware partnership | Pre-configured ESP32 + sensor kit |
| S12.7 | Pricing + billing | Stripe integration, per-greenhouse subscription |

---

## What's Done vs What's Left

### Cloud Infrastructure ✅
| Component | Local | Cloud | Status |
|-----------|-------|-------|--------|
| Database | TimescaleDB (Docker) | Cloud SQL (PG16) | Both active, synced |
| Dashboards | Grafana (Docker) | Cloud Run Grafana | Both active, same data |
| Crop API | verdify-api.service (port 8300) | Cloud Run API | Both active |
| Data ingestion | verdify-ingestor.service | Cloud Run + Pub/Sub | Both active |
| Setpoints | setpoint-server.py (port 8200) | Cloud Run setpoints | Both active |
| Planning | planner-gemini.py (cron) | Cloud Run Job | Cloud in dry run |
| MQTT | Local Mosquitto | GCE Mosquitto + Pub/Sub | Both active |

### Still Needed
| Component | Current | Target |
|-----------|---------|--------|
| ESP32 firmware | Pulls from local only | Pull from cloud as fallback |
| User auth | None (Authentik proxy) | Firebase Auth |
| Web app | Quartz static site | React app (app.verdify.ai) |
| Multi-tenant | Single greenhouse_id | Full tenant isolation + onboarding |
| Local decommission | Everything runs locally | Local becomes optional |

---

## Rules for Every Task Going Forward

1. **Cloud-first.** Every new feature deploys to cloud. Local is legacy.
2. **Every new table gets `greenhouse_id`.** Default = 'vallery'.
3. **Every new script accepts `--greenhouse-id`.** Default = vallery.
4. **Every new API endpoint routes through `/greenhouses/{id}/`.** Keep aliases.
5. **No new local-only dependencies.** Wrap in adapters that work with cloud APIs.
6. **ESP32 firmware stays self-contained.** Never assumes specific cloud architecture.
7. **Test with "what if I had 100 of these?"** If it wouldn't scale, redesign.
8. **No credentials in container images.** Use Secret Manager + env vars.

---

## Decision Log

| Decision | Rationale | Date |
|----------|-----------|------|
| Cloud SQL (PG16, not AlloyDB) for initial deployment | Cheapest option (~$7.50/mo), good enough for single greenhouse. Migrate to AlloyDB when multi-tenant performance matters. | 2026-04-07 |
| Global External Application LB | Proper SaaS-ready routing. Host-based routing scales to many subdomains. Managed SSL. Worth $3/mo. | 2026-04-07 |
| `invokerIamDisabled` for public Cloud Run services | Org policy blocks allUsers. This is the GCP-approved alternative. | 2026-04-07 |
| Keep local running during cloud build | Plants don't care about architecture. Zero-downtime migration. | 2026-04-06 |
| time_bucket() compat function | Simpler than rewriting 14 views. One function, all views work. | 2026-04-07 |
| Vertex AI for all Google AI | Proper auth, billing, audit trail, service account. | 2026-04-06 |
| Dual-path data: local + cloud MQTT + incremental sync | Belt and suspenders. Three paths ensure no data loss during transition. | 2026-04-07 |
| Cloud SQL public IP + password auth (temporary) | Fast to deploy. Must harden with VPC + Secret Manager in Sprint 10. | 2026-04-07 |
