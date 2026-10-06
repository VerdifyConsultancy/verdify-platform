# Full-plan payload preflight

Use this reference through `skill_view(name="greenhouse-planning-mcp", file_path="references/full-plan-payload-preflight.md")`. The event prompt and current MCP schema are authoritative; this checklist never overrides them.

1. Preserve the exact prompt `trigger_id` and `planner_instance` on every write. Do not invent another audit identifier after a rejection.
2. Read fresh climate, equipment, forecast and `plan_status`. Evaluate only a genuinely completed plan; future waypoints identify an active plan, not an evaluation backlog entry.
3. For a required full-plan event, prepare one bounded replacement `set_plan`. Include its plan ID, hypothesis, current-first strictly ascending timezone-aware transitions and expiry after the last transition. Cover the current time; do not start in the future. Validity may not exceed 78 hours.
4. Explicitly supply every `ClimateIntent` field at every transition using the current MCP tool schema. Use registry-valid tactical parameters; never write crop-band anchors, safety rails, direct relay commands or invented tunables.
5. Include the required structured hypothesis for SUNRISE/SUNSET. Its rationale parameter names must resolve in the registry. Follow the event's waypoint count and timing requirements rather than copying a historical example blindly.
6. Inspect the exact `set_plan` result. A gateway acceptance or worker readiness is not persistence. Confirm `plan_status` identifies the persisted plan with current coverage; distinguish queued setpoints from firmware-confirmed values.
7. Treat reporting and semantic-retrieval outcomes separately from plan persistence. Record unavailable resource measurements honestly; never infer energy/water savings from a valid plan. Do not retry missing skill aliases or invent reference paths: use the bare installed skill name and its listed references.
