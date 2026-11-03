# Frozen source snapshots

Byte-identical copies of source files whose SHA-256 a frozen research record
pins. Builders and tests hash the snapshot, never the live file, so firmware
edits that leave the bound values alone do not move those records. A snapshot is
evidence, not configuration: nothing loads it at runtime, and its bytes must
never change.

- `tunables-v1.yaml.txt` is `firmware/greenhouse/tunables.yaml` at SHA-256
  `6852247e30875062ab1340f857e30e9f761ae8591de19b933c97012d6394cd9b`. That hash
  is the `source_entity_grid_sha256` of
  `../../baseline/planner-switchback-v2-profiles.json` (whose own hash
  migrations 220, 222 and 225, the shadow-v2 locks and the direct-launch
  bootstrap pin) and of `../shadow-v2/source-lock-v1.json` `grid_revision`.
  The study binds only to the min/max/step grid of the 41 numeric wire fields,
  so `switchback/v2_profiles.py` and
  `tests/test_experiment_v2_shadow_source_lock.py` require the live file's grid
  to equal this snapshot's. If a firmware change moves one of those grid
  values, re-freeze the study locks; do not edit this snapshot or re-render the
  profile artifact in place.
