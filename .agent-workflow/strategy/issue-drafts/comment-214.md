#427 now owns the concrete required-plan recovery. Update this umbrella's acceptance semantics: a tactical trigger succeeds only when its **actual terminal action** satisfies that trigger's contract. A trigger that requires `set_plan` must receive a valid full plan; an acknowledgement or one-shot bounded `set_tunable` may be correct for other event types but cannot masquerade as `set_plan`.

The repaired bounded planner activates immediately after #427 acceptance, per Jason's July 9 approval. Deterministic firmware remains authoritative.
