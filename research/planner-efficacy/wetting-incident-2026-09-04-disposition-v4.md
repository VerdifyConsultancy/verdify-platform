# September 4 incident disposition and retained physical-proof constraint (#778 → #749)

This v4 disposition preserves v1–v3 and the original September 27 extraction. It completes the incident investigation through the contract's **fail-closed unresolved constraint** route. It does not authorize physical wetting proof or claim physical verification from reported relay states.

## Reproduced evidence

`wetting_incident_join.py` consumes the twelve immutable allowlisted CSV projections in `incident-778-inputs-20260927/`. Their byte hashes and row counts match the original private manifest and v3 receipt. `queries.json` preserves the exact SELECT-only COPY projections and their original SQL hashes. Free-form logs, plan narratives, credentials and raw binary event bundles are excluded. The frozen result is `results-wetting-incident-2026-09-04-v4.json`.

The UTC window is September 4 20:30 through September 5 00:45 exclusive (14:30–18:45 MDT). Each of 271 controller actions has its raw row hash, nearest climate/diagnostic observation within 90 seconds, as-of effective snapshot context with age and row hashes, and as-of HA occupancy observations. Snapshot context is explicitly distinct from independently attested consumed guards. Equipment transitions, forecast vintages, plan delivery/confirmation, overrides, water-meter events and equipment-source receipts remain exact separately hashed streams; no invented missing rows or zeros are inserted.

The join reproduces 199 fog=false/vent=true/vent_interlock action rows: 129 VENT_COOL_FOG_ASSIST and 70 SAFETY_COOL; largest gap 66.289615 seconds. Fog turns off at 20:57:03 and returns at 00:13:03. Both changes align with crossing 90°F, the historical 95−5°F sealed-humidification margin erroneously applied to vented survival fog. [v3](wetting-incident-2026-09-04-causal-v3.md) contains the exact threshold-crossing hashes and historical source hashes. This is a source-backed controller refusal, with incomplete continuous physical actuator and source-receipt coverage.

The 600 gal cumulative meter and 157.3797 gal mister-today estimate have distinct scope/reset semantics. Neither is classified as an exhausted 600-gallon budget. Effective hard-volume readback and native reset epochs remain absent. Valid SNTP/monotonic uptime undermine an invalid-clock or in-window reboot explanation; the retained Task WDT reset string is historical. HA empty occupancy observations are not direct occupied-pin proof. The controller's repeated vent_interlock label points to that veto rather than leak, occupancy or irrigation/fertilizer guards; independent physical faults remain unexcluded. The 21:01 planner delivery follows onset, its 21:03 pulse duration confirms after onset, and 00:29 UTC delivery follows fog recovery. No matching plan_journal row survives under the reported delivery planID.

## Correction and present boundary

Source correction 5e22ca6436d512b82aa9dc6269eb7f221661215a already exists on main. Its native regression verifies explicit fog requests in VENTILATE and SAFETY_COOL, denial without a request and continued exclusion in sealed/fault modes. It exempts only the vent-close veto; leak, occupancy, feed, clock and water rails remain in the caller. Pure-controller replay alone cannot prove the ESPHome relay interlock; the native helper and call-site checks cover that distinction.

On October 5 at 19:36:05 UTC a read-only diagnostics query reported firmware 2026.10.2.0637.620a218a-wifi-bound, observation 19:35:27 UTC, uptime 279788.5625 seconds and SNTP valid 1. Commit 5e22ca64 is an ancestor of 620a218a. This establishes reported source lineage, **not independent binary attestation or live physical rail qualification**. A later firmware string, current wetting, or successful OTA cannot establish the historical physical cause or release the proof hold.

## Executable constraint incorporated into #749

`scripts/experiment_v2_proof_packet.py` continues emitting `wetting_incident_778_disposition=false`; its comment now binds the hold to this disposition. `experiment_v2_readiness_guard.py` requires the prerequisite for every GateP physical-proof boundary. Missing/incomplete disposition blocks proof while independently qualified zero-exposure GateR recovery remains available. The constraint survives closing #778's investigation and cannot be removed by relabeling the incident, raising caps, or accepting only static Ready status.

Release requires source/binary-bound correction evidence, current safe fog admission and preserved rails, qualified equipment/counter-reset boundary observations and current full GateP readiness. Until then physical wetting proof remains disallowed. No settings, database rows, controller state or physical exposure changed in this analysis.

## Reproduction and custody

Run from a clean checkout with Python 3 only:

```
env -i PATH="$PATH" python3 research/planner-efficacy/wetting_incident_join.py --output /tmp/incident-778-new.json
cmp /tmp/incident-778-new.json research/planner-efficacy/results-wetting-incident-2026-09-04-v4.json
```

The tool refuses existing output paths and changed input/SQL hashes. It has no network/DB/device client. Preserve `/Users/jason/verdify-778-evidence-20260927/`; repository projections provide independently reproducible non-secret custody. October5 live SELECT-only re-extraction of the climate and controller action streams byte-matched the frozen hashes, independently confirming those current historical rows. File-level hashes are in `wetting-incident-2026-09-04-v4-sha256.json`. These hashes do not upgrade evidence scope.
