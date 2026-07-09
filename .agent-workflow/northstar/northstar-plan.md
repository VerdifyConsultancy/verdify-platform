# North Star plan — July 9 software recovery

Status: approved by Jason Vallery. Canonical structured record: `northstar-plan.yaml`.

The recovery has five outcomes: stop false device-write and planner loops; align irrigation/fertilizer with actual topology; make broken interior-light evidence unavailable; evaluate solar-night dry-out from realized response; and deliver the complete result through production plus one gated OTA.

The implementation order is safety-driven. First correct actual readback IDs, true reconnect semantics, writer scheduling/accounting, and MCP tool health. Next land serialized schema changes and planner lifecycle, DLI availability, irrigation/fertigation topology, and solar-night outcome evidence. Then promote services, retire stale `band_track_fraction=0.25`, clear the planner blocker, run the full firmware gate set, perform one combined OTA, and verify live relay attribution and evidence truth.

Weekly wall feed is a pilot cadence. Software uses calibrated liters and immediate flush and remains unable to actuate until commissioning measurements exist. No new hardware, center drip program, non-wall fertilizer, fixed-clock dry-out, proxy crop DLI, proposal-only planner soak, or unbounded AI is part of this plan.

The only deferred question is the measured fertilizer recipe, volume, flow, distribution, and flush endpoint. It does not block software because the state machine fails closed until answered.

Next skill: `architecture-contracts` in contract-definition mode.
