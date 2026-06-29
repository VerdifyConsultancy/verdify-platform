# Controller State Machine

## Required transition data

Every transition needs:

- previous state;
- next state;
- triggering event;
- responsible role;
- required artifacts;
- evidence inspected;
- gates checked;
- child sessions affected;
- rollback or recovery behavior.

## Standard wave states

Use these until an approved project overrides them:

```text
INTAKE
PROJECT_ROUTING
RESEARCH_FANOUT
NORTH_STAR_DRAFT
ADVERSARIAL_REVIEW
DESIGN_REVIEW
DESIGN_COMMITTED
REPO_HYGIENE
WAVE_PLANNED
EXECUTING
CI_VALIDATING
PREVIEW_DEPLOYED
HUMAN_REVIEW
FIX_REQUIRED
FIX_WAVE
REPLAN_REQUIRED
PLAN_UPDATE
WAVE_SIGNED_OFF
NEXT_WAVE
NORTH_STAR_PROVEN
IDLE_UNTIL_NEW_REQUIREMENTS
```

## Invariants

- State must survive session restarts.
- A child worker session never self-certifies completion.
- Human feedback blocks protected progression until dispositioned.
- Gravity implementation cannot start unless the Gravity readiness gate is
  approved.
- Controller state references authoritative artifacts rather than duplicating
  their content.
