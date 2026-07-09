Correction after raw-edge/runtime audit: the earlier July 9 planning comment overreached by carrying the old 120-second min-on/max-cycles proposal into the combined OTA.

PR #404's live 45-second re-fire fence is already effective: over the latest seven-day audit, center/south/west had 234/178/55 on-edges and only 0/1/0 re-fires inside 45 seconds. Center-only climate routing will also redistribute demand, so extending pulses or adding a new governor before measuring that topology risks overwatering.

Current recovery scope is preservation and proof only:

- climate intent may energize center mist only; south/west climate-attributed edges must be zero;
- preserve/test the existing `mister_min_off_s` behavior through #434's resolver rewrite;
- add a deterministic test that the center re-fire fence survives and never extends requested pulse duration;
- add no new min-on/governor tunable or entity;
- judge runtime/water/cycles through #389/#390 after the topology change.

The broader governor remains in this issue only as follow-up if post-change evidence proves a real residual defect.
