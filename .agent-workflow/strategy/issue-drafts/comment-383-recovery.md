July 9 supersedes the earlier multi-OTA sequencing. This issue contributes a bounded anti-ping-pong slice to the one reviewed recovery OTA; it does not replace #410's solar-night realized-evidence contract or disable the night-dry-out behavior Jason wants.

Current acceptance slice:

- one resolver-owned post-wet settle state prevents immediate wet→dry→wet reversal across fog/center mist/vent/heat1;
- settle duration is bounded by measured response and never bypasses temperature, dew, occupancy, or absolute-humidity safety;
- `closed_heat_dehum` remains an explicit cfg-readback capability with clear precedence, but default/enabled state must preserve the already approved #410 behavior and be proven by replay;
- #389/#390 prove fog/fan/heat cycling does not regress; #410 proves realized night dry-out, not just command intent;
- #419 must make the relevant outdoor-aware replay paths real.

The old “ship separately after #410/#377” text is obsolete for this authorized recovery. Any behavior that cannot pass the shared replay/heap/cycling packet is deferred rather than forcing a second OTA.
