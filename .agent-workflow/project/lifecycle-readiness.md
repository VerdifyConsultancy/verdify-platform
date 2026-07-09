# Verdify platform lifecycle readiness

**Status:** ready for architecture contracts

Discovery, requirements, product intent, design surfaces, and protected-action approval are recorded in `project-definition.yaml`. No material product question blocks software implementation.

## Ready

- Live-product ownership, users, scope, non-goals, data/control paths, infrastructure, deployment, rollback, migration safety, observability, tests, governance, and documentation are covered.
- Reconcile, planner, irrigation/fertigation, DLI, night dry-out, stale-band retirement, production delivery, and OTA outcomes are approved.
- Exact fertigation values are intentionally modeled as fail-closed commissioning data rather than guessed product requirements.
- Release verification has exact safety and runtime evidence expectations.

## Deferred or accepted risk

- Vanda cultivar-specific optimization and missing crop-zone evidence.
- Physical sensors, HAF, intake, shade, meters, and standalone dehumidification equipment.
- Billing-grade utility targets.
- Broader SaaS identity/privacy/compliance and accessibility/localization work.
- Exact shared-line nutrient chemistry until source water, product, injector, flow, and crop measurements are commissioned.

## Next route

Create black-box module contracts for the sole device writer, bounded planner, irrigation/fertigation controller, DLI evidence boundary, and diurnal dry-out controller. Reconcile each implementation lane to GitHub, then execute and independently criticize the lanes before integration and release verification.
