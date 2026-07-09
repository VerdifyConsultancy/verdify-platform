Correction after raw-edge/control audit: the current recovery does **not** add a new post-wet hold, closed-heat flag, or other device-side tuning. The prior planning comment's proposed behavior is superseded.

#410 already delivered the held-temperature vent/reheat path. Jason wants that solar-night behavior evaluated, and the July 9 contract requires representative realized episodes before another physics/control change. Current scope is therefore:

- no fixed-clock controller and no new tunable/entity;
- preserve zero daytime admission, temperature-floor exits, and `heat2=off`;
- use #410's episode surface to record outdoor AH advantage, temperature response, duty, stop reason, and effectiveness;
- use #389/#390 to determine whether wet/dry ping-pong is actually present after topology/writer repair;
- reopen firmware tuning only if representative episodes prove a repeatable failure and a response test proves the candidate improves moisture without worsening temperature/cycling.

This satisfies the one-OTA decision by avoiding an unproven firmware delta, not by bundling one prematurely.
