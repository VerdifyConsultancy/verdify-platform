# Dormant exposure-close-first rollback

This component is intentionally absent from every overlay. When selected
instead of the day-1 activation, its PreSync hook calls the authenticated
`set_admission:emergency_hold` command. The database closes every open exposure
before revoking experiment authority and records the facility yield. Sync then
sets every experiment consumer to coarse-off, clears the active experiment ID,
and keeps generalized vectors off.

This is a kill/yield stage, not permission to fabricate baseline recovery.
Afterward, baseline recovery requires a separate immutable facility
authorization, exclusive writer execution, and two fresh current-generation
confirming receipts before ordinary ownership restoration.
