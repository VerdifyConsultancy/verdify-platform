# Lifecycle coverage

Use this pass to prove the project definition is complete enough for architecture, planning, implementation, review, and deployment verification. Do not ask this as a generic checklist before reading evidence. First reconstruct what sources already prove, then ask only targeted questions for material unknowns.

## Required coverage areas

Record each area in `lifecycle.coverage` with status `covered`, `not_applicable`, `deferred`, or `unknown`.

- `product_outcome`: value, problem, success, and user-visible completion.
- `users_stakeholders_relationships`: primary users, buyers, operators, maintainers, support, approval owners, and relationship boundaries.
- `domain_data_model`: domain vocabulary, entities, identifiers, ownership, lineage, retention, import/export, migration, and deletion.
- `scope_non_goals`: in-scope, out-of-scope, deferred, and deliberately unsupported behavior.
- `design_surfaces`: UI, API, CLI, event, tool, agent, admin, review, configuration, and approval surfaces.
- `security_privacy_compliance`: authn/authz, trust boundaries, secrets, audit, regulated data, privacy, abuse cases, and compliance obligations.
- `infrastructure_hosting`: hosting model, runtime platform, networking, storage, resource needs, scaling, availability, and resilience.
- `environments_configuration`: local, test, staging, production, tenant/region/environment differences, config, secrets, feature flags, and seed data.
- `integrations_dependencies`: internal/external services, vendors, APIs, data providers, queues, webhooks, protocols, rate limits, SLAs, and failure isolation.
- `deployment_release_rollback`: CI/CD, artifact identity, releases, approvals, migrations, rollback, disaster recovery, and runtime verification.
- `operations_observability_support`: logs, metrics, traces, alerts, dashboards, runbooks, incident response, maintenance, backup/restore, and support ownership.
- `quality_testing_evidence`: acceptance evidence, automated/manual test strategy, performance/security/accessibility checks, and review gates.
- `governance_ownership_approvals`: source-of-truth owners, decision authorities, issue/PR policy, data stewardship, and human gates.
- `documentation_enablement`: operator docs, user docs, onboarding, training, API docs, changelog/release notes, and support scripts.
- `cost_procurement_risk`: budget, paid services, licensing, procurement, vendor lock-in, quota risk, and material project risks.
- `migration_legacy`: legacy systems, data migration, compatibility, rollout/coexistence, cutover, decommissioning, and backfill.
- `accessibility_localization`: accessibility standards, localization, time zones, currencies, device/browser support, and inclusive interaction requirements.

## Gap handling

Use `unknown` only when the missing answer may change architecture, public behavior, data handling, delivery, operations, or acceptance. Open a durable gate for every blocking unknown. Use `not_applicable` only with a short rationale. Use `deferred` only when the deferral owner, trigger, and risk are explicit.

## Relationship handling

Record relationships when the project depends on a person, team, organization, service, platform, vendor, regulator, customer class, or approval owner. Name the parties, nature of dependency, owner, and evidence. Missing relationship ownership is a blocking gap when it affects delivery, deployment, support, data access, compliance, or cost.

## Architecture inputs

Capture what architecture must solve, not the architecture itself:

- system context and actors;
- architecture decision areas such as storage, runtime topology, integration pattern, tenancy, identity, migration, or AI/model/provider choice;
- quality attributes and constraints such as latency, availability, recovery, privacy, auditability, portability, maintainability, operability, and cost;
- unresolved assumptions that architecture must validate or escalate.

Do not choose components, modules, vendors, frameworks, or hosting products unless already approved by evidence or human decision.

## Delivery and operations

Project definition must be explicit enough that later skills can plan delivery without hidden context. Capture expected environments, hosting/runtime assumptions, config/secrets, deployment flow, rollback/recovery expectations, migration needs, observability, support model, incident response, documentation, and release verification expectations.

## Approval rule

An approved project definition has all required coverage areas recorded, no material `unknown` coverage, no open blocking gaps, and traceability from lifecycle coverage to evidence, decisions, requirements, or explicit non-goals.
