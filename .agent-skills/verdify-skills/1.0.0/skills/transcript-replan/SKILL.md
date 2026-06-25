---
name: transcript-replan
description: Converts walk transcripts, meeting notes, and spoken planning extracts into routed Verdify source evidence, proposed requirement/user-story/architecture changes, conflict flags, and GitHub issue or gate recommendations. Use when new conversational planning input may affect one or more repositories, North Star artifacts, lifecycle plans, or protected decisions.
compatibility: Requires access to the transcript source, target repositories or supplied snapshots, current .agent-workflow artifacts, and GitHub state when issue recommendations are requested.
metadata:
  author: Verdify
  version: "1.0.0"
  lifecycle-order: "2a"
---

# Transcript Replan

Turn conversation into durable proposed changes. Do not silently rewrite
protected artifacts, create implementation lanes, or start feature work.

## Canonical artifacts

- `.agent-workflow/intake/transcript-replan.yaml` - structured intake package
- `.agent-workflow/intake/transcript-replan.md` - human-readable summary
- Proposed GitHub Issues or gates when the repository policy permits creation

Validate YAML against `../../schemas/transcript-replan.schema.yaml`.

## Procedure

1. Read `../../COMMON_OPERATING_CONTRACT.md`.
2. Read the transcript or evidence record exactly enough to preserve material
   claims, uncertainties, likely transcription corrections, and incomplete
   thoughts.
3. Read current project definition, architecture, ADRs, state-of-union, sprint
   plans, open gates, issues, and PRs when available.
4. Normalize the transcript into source-backed items:
   - decisions;
   - strong directions;
   - exploratory ideas;
   - requirements;
   - user stories;
   - architecture inputs;
   - open questions;
   - conflicts;
   - personal context useful for planning.
5. Route each item to a repository, lane, lifecycle phase, and next owner.
6. Compare routed items against approved artifacts and GitHub state.
7. Produce proposed patches, issue drafts, and gate recommendations. Mark
   protected changes as proposed only.
8. Write the canonical YAML and Markdown summary.

## Stop conditions

Stop and open a gate when the transcript proposes changing protected North Star
content, crossing repository ownership, altering security boundaries, changing
production deployment policy, or starting Gravity implementation before the
readiness gate is signed off.

## Load references only when needed

- Read `references/routing-contract.md` for item taxonomy, conflict handling,
  and issue/gate output rules.
