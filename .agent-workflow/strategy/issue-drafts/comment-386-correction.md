Correction after raw transition audit: the old severe 30–44 starts/day premise is not current raw-edge truth. Recent completed days show roughly 3–16 starts per circuit, with an episodic 15–22 window around June 20–22. Code inspection also shows minimum-on handling already occurs after the `outside_window` decision.

Current recovery scope is validation only:

- add a unit regression proving `min_on_ms` survives `in_window → outside_window`;
- prove DLI-unavailable work does not alter qualified-light-minute/photoperiod actuation;
- observe per-circuit raw counts through #389/#390 for the recovery bake;
- add no shoulder-hysteresis or freshness-hold tunable unless a reproducible raw-edge failure and isolated response test first justify it.

Jason's OTA authorization still removes the stale separate operator gate, but it does not justify speculative behavior.
