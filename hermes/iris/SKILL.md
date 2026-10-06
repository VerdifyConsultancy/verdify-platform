---
name: greenhouse-planning-mcp
description: Verdify Iris planning with audited MCP tools and current-first full-plan validation
---

Use the bare skill name `greenhouse-planning-mcp` with `skill_view`. Read the event's assembled context first. Device-affecting operations use the configured Verdify MCP allowlist only; never use a terminal, raw SQL or arbitrary filesystem access.

Required references:
- references/greenhouse-playbook.md
- references/full-plan-payload-preflight.md

Existing validated planner lessons and event contracts take precedence over historical examples. A valid plan requires its exact audit identifiers, schema-valid current coverage and confirmed persistence; worker readiness alone is not plan success.
