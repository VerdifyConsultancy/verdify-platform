# Verdify platform discovery

**Status:** approved

**Canonical record:** `project-definition.yaml`

**Evidence cutoff:** 2026-07-09 15:05 MDT

**Approver:** Jason Vallery

## Verdict

Verdify is one live, safety-critical greenhouse product spanning deterministic ESP32 firmware, the sole production device writer, telemetry and TimescaleDB, bounded AI planning through MCP, API and generated evidence surfaces, CI/GHCR, and the single `verdify-prod` ArgoCD application. Track A plant safety outranks platform evolution. GitHub Issues are backlog authority, `main` is accepted code, and protected device and production actions remain explicit human gates.

The July 9 evidence and operator corrections resolve the product intent needed for this recovery:

- center drip is intentionally unconnected and disabled;
- VPD climate mist is center-only and fertilizer-free;
- south and west misters are explicit intentional irrigation only;
- automatic fertilizer is a commissioned, weekly pilot on wall drips only;
- dormant center, south, and west infrastructure stays present but disabled;
- interior crop DLI is unavailable until the broken sensor is replaced and validated;
- night dry-out follows firmware solar phase and existing environmental guards;
- the June `band_track_fraction=0.25` experiment is retired;
- the bounded planner becomes active as soon as the repaired path passes acceptance checks;
- implementation, production delivery, and OTA are authorized, with deterministic gates unchanged.

## Material current contradictions

1. A stable ESPHome transport still receives a 69-value batch every five to six minutes because generic config drift is mislabeled as reconnect, 56 registry anchor wire IDs do not match actual ESPHome slugs, and confirmation is recorded before delivery completes.
2. Hermes can remain TCP-healthy after its Verdify MCP tool dies, so completed model sessions cannot deliver a plan. Full-plan materialization also applies incompatible bounds in the wrong order.
3. Firmware rotates climate wet-assist into unplanted south and west zones and exposes stale center/non-wall fertilizer paths.
4. Wall fertigation is a dead fixed-time schedule with guessed durations and a 90-minute hold, while the approved contract requires calibrated liters and immediate clean flushing.
5. Firmware and downstream consumers publish an interior DLI value despite the broken sensor and compound it with time and double-count defects.
6. The dehumidification vent-hold path is not constrained to the night solar phase.

## Evidence and accepted risk

The source inventory and traceability live in the canonical YAML. Exact fertigation chemistry and volume remain a commissioning dependency, not a code-design blocker: automation must fail closed until source water, product analysis, injector ratio, aggregate flow, distribution uniformity, line fill, flush endpoint, delivered EC/pH, and seasonal multiplier are recorded. Broader SaaS privacy, accessibility, localization, procurement, and physical upgrades are deferred from this bounded recovery.

## Handoff

Discovery, requirements, product intent, and design surfaces are approved. The next lifecycle step is architecture contracts, followed by issue reconciliation and bounded delivery lanes. No approval question remains for the implementation or protected rollout described above.
