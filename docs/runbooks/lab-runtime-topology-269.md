# Lab runtime topology correction (#643)

The first normal Lab publisher run after268 authenticated as its dedicated role,
then failed at the zone renderer with `permission denied for table positions`.
The shared public-crop query requires canonical crop zone, linked position/shelf
zone, then legacy zone fallback. `v_position_current` is not a faithful replacement:
it filters inactive positions and can duplicate a position across active crops.

Forward269 adds exactly two SELECT-only, owner-backed private projections:
`positions(id, greenhouse_id, shelf_id)` and `shelves(id, zone_id)` in the existing
Lab runtime schema. The renderer and protected-crop predicates remain unchanged.
There are no public base-table grants, DML grants, new credentials, or changes to
applied268. The owning C0 delivery runner admits only the exact269 hash and ordered
predecessor, retaining ordinary API/ingestor and MCP boundaries without resealing.

Qualification is recorded in `lab-runtime-topology-269-qualification.json`.
One fresh paired-backup restore qualified exact268 and269 source with clone-only
OID normalization, full original catalog entries and source/executed hashes kept
separately. All six native duty allow/deny fixtures and the full actual zone query
passed; new topology writes, public topology reads and cross-role reads were denied.
Rollback restored all three complete raw and qualified boundary catalogs exactly.
The fixture's runner-style stamps do not attest production application.

Production269 delivery and a successful unchanged normal Lab publisher run remain
pending. Delivery requires the normal migrate image build/promotion and attended
full hook-running sync. No planner/setpoint/Lab image update is necessary for this
projection-only correction. Verify exact269 ledger, unchanged three seals, actual
Lab runtime authentication, and a real committed publisher generation; Ready or a
zero-row unit fixture alone is not publisher acceptance. Preserve the original failed
Lab Job. Restart: none; normal Lab jobs acquire the new projections after migration.
