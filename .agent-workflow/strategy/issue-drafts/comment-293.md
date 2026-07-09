July 9 live verification changes this from “local implementation, not applied” to a source/live divergence defect: migration 186 exists on main, but the production database still runs the old `local_hour - 13.0` solar-noon function.

Acceptance now requires serialized rollback proof, live apply, function-definition/version proof, equinox/solstice parity fixtures, and refresh of dependent views before solar-phase dry-out or planner analytics are trusted. This is a release prerequisite for the revised #410 outcome surface.
