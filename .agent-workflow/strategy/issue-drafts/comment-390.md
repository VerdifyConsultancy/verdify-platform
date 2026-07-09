Included in the July 9 combined-OTA recovery. The gate must consume #389's raw-transition truth and judge the reviewed firmware delta, not `daily_summary` snapshots.

Required behavior:

- compare matched completed local-day windows with per-relay starts/day, short cycles, runtime, and peak transitions/hour;
- declare issue-specific expected improvements and explicit non-regression tolerances rather than a blanket zero-positive rule that weather can invalidate;
- reproduce the #295 grow-light regression as a failing historical fixture;
- emit a durable pre/post artifact and block last-good promotion when the declared contract fails;
- distinguish insufficient/poor-coverage evidence from PASS.

Offline gates run before OTA; post-OTA evidence is part of runtime acceptance.
