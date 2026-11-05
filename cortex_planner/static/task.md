Your task: produce the plan for this trigger.

Rules:
- Use exactly the waypoint anchors listed below, in order; 3 to 6 of them, starting with the first.
- Start from the incumbent values in the decision surface. Keep each one unless one of its drivers changed; list every changed field in `changed` and give it a reason of at most 25 words in `reasons`.
- `key_facts`: up to 5 short sentences restating the evidence that drives the plan (forecast, band misses, night VPD), written before the waypoints.
- `hypothesis`: the conditions you expect and up to 3 numeric predictions (`metric`, `window`, `value`), for example metric "vpd_below_band_hours", window "tonight".
- `expected_outcome`: VPD-below-band hours, cold hours and water gallons over the next 24 h.
- `confidence`: 0 to 1.

Output contract: one JSON object, no prose, no code fence:
{"key_facts": [str], "hypothesis": {"conditions": str, "predictions": [{"metric": str, "window": str, "value": number}]},
 "waypoints": [{"ts": anchor, "climate_intent": {every field of the decision surface: number}, "changed": [field], "reasons": {field: str}}],
 "expected_outcome": {"vpd_below_band_hours": number, "cold_hours": number, "water_gal": number}, "confidence": number}
Every climate_intent must carry all fields of the decision surface within their min and max. Unknown fields are rejected.
