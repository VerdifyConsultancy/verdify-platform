The July 9 review promotes this into the one combined recovery OTA. Reconcile it with the approved topology: climate mist has one actuator (`mister_center`), while south/west are intentional-irrigation paths and must not share the climate pulse governor.

Implement one center-mister dwell/cycle budget through the common relay resolver; preserve water delivery by testing integrated on-time and VPD response, not only fewer starts. South/west get independent explicit-intent safety/dwell coverage under #434. New tunables still require cfg readbacks. Acceptance uses #389/#390 transition truth and the refreshed outdoor-aware corpus from #419.

The old dependency on running the June 0.25 experiment is superseded: #377 is now an ordered cleanup to zero.
