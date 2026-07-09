July 9 recovery scope: this replay blindness is a release prerequisite for the combined firmware package, not a follow-up observation.

Acceptance is now:

- export real `outdoor_data_age_s` from the source timestamp rather than forcing freshness in the harness;
- refresh and archive the corpus through the standard path;
- assert a meaningful minimum count and regime spread of fresh-outdoor rows, including cold/wet and vent/heat-assist candidates;
- prove the stock replay reaches outdoor-aware estimator branches without `REPLAY_EMIT_OUTDOOR_FRESH=1`;
- retain the synthetic fixture only as deterministic edge coverage.

The firmware recovery cannot claim replay coverage for #410/#383 until this is green.
