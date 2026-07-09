July 9 scope correction: preserve the already-shipped firmware solar-phase night control and safety guards; do **not** replace them with a fixed 02:00–06:00 controller window or another speculative physics rewrite.

The remaining acceptance boundary is realized evidence:

- apply/prove migration 186 so DB solar phase matches firmware;
- materialize each sunset→sunrise dry-out episode with outdoor absolute-humidity advantage, temperature floor, actuator duty, admission/block/stop reason, and observed 10–20 minute response;
- classify ineffective episodes and expose them to alerts/planner tuning;
- require zero daytime admission and retain `heat2=off` plus existing floor/re-entry guards;
- replace the early 2-night PASS claim with a representative evidence window. The expanded July 9 review found only 3/5 qualifying nights and the latest failed.

Jason approved continued solar-night dry-out work and immediate bounded planner use of valid evidence. Firmware response changes should be added only if realized data proves a device-side failure.
