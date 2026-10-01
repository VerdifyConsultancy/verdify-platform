# Temporary C1 grid qualification

The ordinary dispatcher resolves all five policy layers first. A C1 worksheet
then selects only explicit off-grid canonical decisions, with exact source
values and rationales. Physics and current moisture caps run after selection.
The underlying ordinary policy and continuous crop/solar control remain active.

This path uses the existing sole ingestor, queue, durable setpoint lifecycle,
confirmation monitor and current-generation readbacks. No replay or separate
client is used. A worksheet does not grant component experiment authority,
recovery credit, exposure credit, or qualification credit.

## Source binding and effects

1. Deploy the reviewed source and firmware, then complete ordinary convergence.
   Retain an original passive C1 capture of the actual off-grid blockers.
2. Read `/srv/verdify/state/c1-qualification-preview.json` from the ingestor.
   It binds runtime source, pod/session, firmware/grid, connection generation,
   exact current ordinary48 values and stable source input provenance. Function
   output timestamps are excluded; Iris source timestamps remain bound.
3. Write explicit field decisions with `from`, `to`, and `rationale`. Prepare:

   ```sh
   PYTHONPATH=. python scripts/prepare-c1-qualification-worksheet.py \
     --preview fresh-preview.json --decisions explicit-decisions.json \
     --output fresh-worksheet.json
   ```

   Preview age must be at most60 seconds and ordinary full48 must be converged.
   The ingestor separately requires no outstanding ordinary desired differences.
   The immutable worksheet expires six minutes after the preview capture;
   preparation, confirmation and capture consume that same authority window.
4. After the concrete bundle review under the campaign authorization, copy the
   exact worksheet atomically to the sole ingestor's state directory as
   `c1-qualification-worksheet.json`. The existing scheduler wakes its dispatcher.
   Each stage is bounded to12 native commands; every requested/queued/sent/failed
   row is retained and confirmations must match current-generation readbacks.
5. Read `c1-qualification-state.json`. Only `active` means native commands are
   confirmed. Prepare passive capture with the current status and that state:

   ```sh
   python scripts/prepare-c1-native-capture.py current-status.json request.json \
     --qualification-state current-qualification-state.json
   ```

   Capture expiry is bounded by worksheet expiry. It still requires two distinct
   complete original48 callback epochs, advancing timestamps, the original
   consumed-band computation marker and genuine band-member callbacks. Cached
   SubscribeStates replay invalidates the request. A late/missing publication
   remains unavailable; expiry is never extended to manufacture acceptance.

## Yield and recovery

Admission binds every exact current source value: an on-grid field cannot gain
a grid decision. After admission, only an explicitly selected off-grid field
in the existing seven-field moisture-guardrail list may change its ordinary
source value under unchanged source-input provenance. Its original `from`,
fixed `to`, projection and expiry remain immutable; the fixed selection must
still satisfy the fresh moisture cap and physics before every setter. A missing
cap for a varying source field, unsafe fixed selection, changed untouched field or other policy/identity
change ends authority. This permits a safe explicit threshold such as 1.05 to
remain eligible when its solar-derived source cap moves 1.11 to 1.09; it cannot
keep an untouched 1.10 threshold above a fresh 1.09 cap.

Any bound source policy, runtime identity, firmware/grid, worksheet content or
expiry change ends qualification authority. Expiry
and immutable worksheet/identity are checked again at the physical queue
chokepoint. A C1-only async guard rereads the stable source inputs and current
moisture caps from the existing pool immediately after pacing and before every
setter. It opens no long snapshot transaction; ordinary requests use no guard. Ordinary source values are resolved afresh on each dispatcher pass;
restoration prioritizes the touched fields and remains bounded to12 commands.
Failed qualification rows are retained alongside the separate restoration
stages. Unknown inflight outcomes hold without replay; a failed restoration
holds without resetting its run. No historical failed experiment row is replayed.

The state and packet always say `qualification_claimed: false`. ToolA must
validate the real callback packet separately; source projection and confirmed
writes do not prove callback freshness, control fidelity or natural fog behavior.
