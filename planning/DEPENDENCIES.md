# Complete blocking dependency graph

The [current reviewed native graph](reviews/2026-11-07-triage/DEPENDENCIES.md) includes independent local issues and transitive cross-repository predecessors. The canonical 190-issue/85-edge campaign graph is retained below.

Generated from [backlog.yaml](backlog.yaml). An arrow means **predecessor must finish before dependent acceptance/activation**, not parentage. Isolated nodes are included so coverage is auditable. Read [CAMPAIGN.md](CAMPAIGN.md) for the smaller critical path.

```mermaid
flowchart TD
  subgraph C0["C0: Measurement and incident truth"]
    i775["#775 (open) Campaign: trustworthy greenhouse outcomes → qualified pilo"]
    i778["#778 (closed) Investigate September 4 hot/dry peak and three-hour wettin"]
    i371["#371 (closed) Repair climate score semantics and publish physical outcom"]
    i424["#424 (closed) Resolve served, consumed and raw-readback band lineage wit"]
    i779["#779 (closed) Re-run historical planner comparisons on a fixed sensor pa"]
    i780["#780 (closed) Score as-of outdoor forecasts against outdoor truth and se"]
    i781["#781 (closed) Commission a claim-safe water and electricity endpoint con"]
    i782["#782 (closed) Finish the empirical inputs and frozen exploratory scienti"]
  end
  subgraph C1["C1: Recovery and integrated physical qualification"]
    i750["#750 (closed) Recovery/proof campaign: rebaseline current state and seal"]
    i747["#747 (closed) Verify corrected scheduled backups and current production "]
    i749["#749 (closed) Qualify current three-probe readiness and explicit safety "]
    i641["#641 (closed) Complete one fresh attended physical baseline–target–basel"]
    i639["#639 (closed) Qualify the component executor, exclusivity and recovery b"]
    i587["#587 (closed) Qualify lifecycle authority and blinded monitoring in cont"]
    i783["#783 (closed) Qualify the complete current-login selector-to-frozen-anal"]
  end
  subgraph C2["C2: Seasonal design lock and randomized start"]
    i581["#581 (closed) Complete the bounded AI-admission pilot and publish a deci"]
    i588["#588 (closed) Bind exact scientific/runtime identity and finalize one in"]
    i642["#642 (closed) Activate and verify one randomized day 1 from the locked p"]
  end
  subgraph C3["C3: Complete pilot and decision report"]
    i640["#640 (closed) Freeze and reconcile the first real assigned-day outcome a"]
    i784["#784 (closed) Operate the full blinded assigned-day pilot with immutable"]
    i785["#785 (closed) Reveal once, reproduce the frozen analysis and decide the "]
  end
  subgraph C4["C4: Delivery and runtime reliability"]
    i644["#644 (closed) Integrate exact-SHA, path-aware and device-safe delivery r"]
    i322["#322 (closed) Replace remaining VM-era tests with current k3s delivery c"]
    i304["#304 (closed) Provide reproducible least-authority firmware and database"]
    i303["#303 (closed) Run real portable ESPHome compilation for declared firmwar"]
    i390["#390 (open) Gate firmware releases on topology, cycling, runtime safet"]
    i433["#433 (closed) Finish truthful single-writer command lifecycle and no-rec"]
    i427["#427 (closed) Prove active Hermes/MCP recovery and truthful non-authorit"]
    i75["#75 (open) Observability program: prove actionable health from source"]
    i563["#563 (closed) Enforce the actual ConfigMap-based alert delivery contract"]
    i394["#394 (closed) Deliver and fault-test live split-brain and no-writer aler"]
    i89["#89 (closed) Verify exact-source post-deploy smoke and device-route hea"]
    i671["#671 (closed) Make Lab publishing failures and cache contention observab"]
    i399["#399 (closed) Prove the HA grow-light writer remains within the single-w"]
    i419["#419 (closed) Populate real outdoor freshness in replay and enforce bran"]
    i386["#386 (open) Prove grow-light minimum-on behavior at the solar-window b"]
    i949["#949 (closed) Bound planner-policy drift holds and prove autonomous safe"]
    i950["#950 (closed) Restore the ingestor Slack credential-file contract and op"]
    i951["#951 (closed) Restore authenticated greenhouse camera snapshots after co"]
    i952["#952 (closed) Preserve Verdify Grafana monitoring identity across pod re"]
    i953["#953 (closed) Reconcile Iris planning skill lookup and required referenc"]
    i835["#835 (closed) Fix reconnect equipment snapshot provenance and stale rela"]
    i862["#862 (closed) Lab publisher: warm-cache init rescans the public tree for"]
    i964["#964 (closed) Recover failed production promotion and schedulable Lab/ba"]
    i965["#965 (closed) Separate API grow-light command intent from observed equip"]
    i963["#963 (closed) Bound moisture-exchange state and preserve structured cons"]
    i976["#976 (closed) Qualify safe recovery of Lab publication receipt metadata"]
    i980["#980 (closed) Resolve backup-exporter readiness timeouts without obscuri"]
    i981["#981 (closed) Restore truthful energy source freshness and trace stale m"]
    i802["#802 (closed) Adopt the enforcing descheduler under manual Argo ownershi"]
    i988["#988 (closed) Restore public camera snapshots and qualify authenticated "]
    i990["#990 (closed) Delete verdify-prod qualification and restore-drill residu"]
    i991["#991 (closed) Prune verdify-db-rehearsal run residue (non-data, D-04)"]
    i993["#993 (closed) Adopt NetworkPolicy verdify-prod/allow-db-from-restore int"]
    i995["#995 (closed) Adopt Namespace verdify-descheduler in place and record Ve"]
    i997["#997 (closed) Keep Verdify dashboards single-sourced: delete stale Confi"]
    i998["#998 (closed) Retire writer/DB watchdog CronJobs after monitoring-stack "]
    i999["#999 (closed) Pin the remaining tag-only verdify-prod images by digest"]
    i1000["#1000 (closed) Close health coverage gaps for Verdify items and alert the"]
    i1001["#1001 (open) Finish retired Verdify namespace and hostname cleanup afte"]
    i1002["#1002 (closed) Stop anonymous internet users running ad-hoc SQL via graph"]
    i1003["#1003 (closed) Route verdify-platform main changes, including prod releas"]
    i1004["#1004 (closed) Bring kubectl-patched verdify-prod workloads back under Gi"]
    i1005["#1005 (closed) Give verdify-lab real node redundancy instead of two repli"]
    i1006["#1006 (closed) Scope the internet-facing verdify-traefik to verdify-prod:"]
    i1007["#1007 (closed) Put edge pre-auth in front of mcp.verdify.ai: one static I"]
    i1008["#1008 (closed) Add rate limits and request bounds to anonymous api.verdif"]
    i1009["#1009 (open) Sign greenhouse ESP32 firmware and flash only reviewed arc"]
    i1010["#1010 (open) Finish trusted client IP attribution for successful verdif"]
    i1011["#1011 (closed) Bound verdify-mcp egress and stop mounting the default Ser"]
  end
  subgraph C5["C5: Durability and least privilege"]
    i218["#218 (closed) Durability program: recoverable backups, measured RPO/RTO "]
    i670["#670 (closed) Make backup artifacts sufficient to restore roles, ownersh"]
    i672["#672 (closed) Make compressed-chunk ownership restoration a supported bl"]
    i382["#382 (closed) Persist ingestor queue and spool across restart and node l"]
    i396["#396 (closed) Design and rehearse CNPG alongside the live database on cu"]
    i245["#245 (closed) Execute a separately bounded database cutover only after p"]
    i643["#643 (closed) Extend least-privilege database roles beyond the already-r"]
    i49["#49 (closed) Audit and close historical suppressed sensor alerts withou"]
    i977["#977 (closed) Decide the database replica policy before November 12; kee"]
    i992["#992 (closed) Retire the verdify-db-rehearsal experiment namespace now t"]
    i994["#994 (closed) Record the verdify-db data PVC db-data-verdify-db-0 as pro"]
    i996["#996 (closed) Remove laptop-root-era verdify-agent RBAC"]
    i1021["#1021 (closed) Switch verdify-vision from shared Gemini credentials to Co"]
    i1022["#1022 (closed) Make the ESP32 writer identity and egress exact without lo"]
  end
  subgraph C6["C6: Evidence-led control and irrigation"]
    i359["#359 (open) Control program: truthful crop corridors before evidence-l"]
    i430["#430 (open) Simplify firmware only while preserving autonomous control"]
    i428["#428 (closed) Diagnose heap/watchdog risk and qualify the actual last-go"]
    i368["#368 (open) Define shared sensor jump, flatline and contributor-integr"]
    i367["#367 (open) Consolidate anti-chatter into one explicit dwell contract"]
    i370["#370 (open) Consolidate equipment conflict resolution into one pure ar"]
    i369["#369 (open) Remove dead tunables and code from a verified consumer inv"]
    i324["#324 (closed) Extend zonal band lineage compatibly for future determinis"]
    i410["#410 (closed) Measure realized solar-night dry-out before changing the c"]
    i378["#378 (closed) Decide corridor widths from fixed-target crop and resource"]
    i361["#361 (closed) Reassess diurnal anchors only after solar parity and measu"]
    i214["#214 (closed) Prove crop-deviation trigger → valid plan on the sole oper"]
    i350["#350 (closed) Irrigation program: topology truth, overwatering protectio"]
    i434["#434 (closed) Enforce center-only climate mist and commissioned wall-onl"]
    i299["#299 (open) Preserve center-mister re-fire protection through topology"]
    i297["#297 (open) Add saturation alerts and a commissioned dispatcher-side d"]
    i296["#296 (closed) Consider slow-hysteresis firmware irrigation feedback only"]
    i1431["#1431 (open) VPD: one-week night moisture-source experiment (E-1)"]
    i1432["#1432 (open) VPD: HAF circulation fans now; dehumidifier pilot decision"]
    i1434["#1434 (open) Planner study #1360: VPD scoring baseline (dry-out minutes"]
  end
  subgraph C7["C7: Physical and seasonal commissioning"]
    i16["#16 (open) Hardware program: commission only the sensing and equipmen"]
    i751["#751 (open) Replace the failed south climate probe and diagnose hydro "]
    i298["#298 (open) Rebaseline actual crop, pot and probe topology before feed"]
    i398["#398 (open) Replace stale soil threshold seeds with commissioned crop/"]
    i45["#45 (open) Commission soil and runoff feedback before enabling irriga"]
    i51["#51 (open) Calibrate CO2 against a real reference and publish measure"]
    i52["#52 (open) Define species-appropriate seasonal dormancy care from ope"]
    i412["#412 (closed) Close the seasonal door screen only when measured night co"]
    i1435["#1435 (open) VPD: recheck dry winter nights in December 2026"]
  end
  subgraph C8["C8: Comparator and full resource claim"]
    i174["#174 (closed) Rehome only current Verdify admin surfaces to global SSO, "]
    i14["#14 (closed) Twin program: qualify declared oracle coverage before usin"]
    i31["#31 (closed) Close twin setpoint and actuator coverage gaps with genera"]
    i606["#606 (closed) Register the twin build profile only through its owning fl"]
    i638["#638 (closed) Unify future manifest/vector wire schema and content ident"]
    i586["#586 (closed) Qualify a future atomic firmware policy engine with crash-"]
    i786["#786 (closed) Decide whether a deterministic forecast comparator merits "]
    i379["#379 (closed) Gate grey-box forecasting and MPC on validated response mo"]
    i787["#787 (closed) Decide and scope a measured heating/overnight/resource stu"]
  end
  subgraph VD["VD: Dashboard and irrigation program (epic #1120)"]
    i1120["#1120 (open) Program: dashboard.verdify.ai and the irrigation program"]
    i1125["#1125 (closed) Build the form controls, edit drawer, water confirm dialog"]
    i1130["#1130 (open) Build the Graphs screen (SCR-02) with the metric picker an"]
    i1132["#1132 (open) Build the Settings screen (SCR-05) with the edit drawer"]
    i1135["#1135 (open) Build the Water screen (SCR-04): read views and Stop all w"]
    i1143["#1143 (closed) Ship R1: put the read-only dashboard live at dashboard.ver"]
    i1161["#1161 (open) Rework the mist path: membership, scheduled pulses, occupa"]
    i1162["#1162 (open) Remove the Home Assistant buttons, add manual_override and"]
    i1164["#1164 (open) Ship R3: deploy the irrigation platform and flash the engi"]
    i1165["#1165 (open) Bring the clean lines online: L01 first, then L04, L07 and"]
    i1166["#1166 (open) Bring the fertilizer lines online: L02 with L03, then L08 "]
    i1181["#1181 (open) Ship R1.1: put every read screen, the water reads and the "]
    i1183["#1183 (open) Read the YINMIK hydroponic probe directly, without Home As"]
    i1184["#1184 (open) Add a PAR light meter: hardware, firmware, ingest and DLI"]
    i1186["#1186 (open) Ship R4: put the Next screens live"]
    i1300["#1300 (closed) Back to automatic leaves a grow light off its intent when "]
    i1322["#1322 (open) dashboard: chart corner labels overlap the band edge; stra"]
    i1433["#1433 (open) VPD: achievable vs aspirational band in the read model, da"]
  end
  subgraph W5["W5: Dashboard wave 2: Jason acceptance feedback (epic #1325)"]
    i1325["#1325 (open) [Epic] Dashboard wave 2: Jason's acceptance feedback on th"]
    i1327["#1327 (open) Model plantings on the location tree and serve them from t"]
    i1328["#1328 (open) Let agents record, move and harvest plantings through MCP"]
    i1329["#1329 (open) Reconcile greenhouse locations and current plantings with "]
    i1330["#1330 (closed) Add a Crops section: hydro slot grid, wall shelves and pla"]
    i1332["#1332 (open) Explain every setting: what it does to the controller and "]
    i1333["#1333 (open) Define the Settings filters and states in the UI, and expl"]
    i1334["#1334 (closed) Research the band math and design a plant-agnostic band ed"]
    i1335["#1335 (open) Give each light circuit a findable Auto / On / Off control"]
    i1336["#1336 (open) Model one physical line with clean and fertilized schedule"]
    i1337["#1337 (open) Redraw Now's band gauges as one track with a green band bo"]
    i1338["#1338 (open) Keep the Now headline steady and move the out-of-band deta"]
    i1339["#1339 (open) Replace codes on Now with plain labels and add a shared gl"]
    i1340["#1340 (closed) Give Running and next room for its labels and add Main and"]
    i1341["#1341 (open) Show line names everywhere and remove the L01/L02 relay co"]
    i1342["#1342 (open) Add Main and Grow light controls to Now's quick actions"]
    i1343["#1343 (open) Allow a one-time run of any line, misters included"]
    i1344["#1344 (closed) Explain VPD control, Manual and Fallback on Water; show VP"]
    i1345["#1345 (open) Remove plant-specific copy and make soil-moisture targets "]
    i1346["#1346 (open) Write a greenhouse snapshot with the local Cortex Qwen mod"]
    i1347["#1347 (open) Make the schedule timeline readable: daylight band on ever"]
    i1348["#1348 (closed) Separate irrigation runs from VPD mist and fog everywhere"]
    i1349["#1349 (open) Redesign Water cards for clear line and group delineation "]
    i1350["#1350 (closed) Replace the Hermes/OpenAI planner with one deterministic c"]
    i1351["#1351 (open) Audit and remove Verdify's external dependencies (local-on"]
    i1352["#1352 (open) Decommission Slack from Verdify: alerts on the dashboard, "]
    i1353["#1353 (closed) Show 72 h of history and 72 h of forecast on Graphs by def"]
    i1354["#1354 (open) Move chart labels out of the plots and use one chart vocab"]
    i1355["#1355 (open) Make event markers on Graphs show what each event is and w"]
    i1356["#1356 (open) Redesign the equipment lanes: shared time axis, solid bars"]
    i1357["#1357 (closed) Add a Lighting tab to Graphs: light state, outdoor lux and"]
    i1358["#1358 (open) Research and fix band compliance: grade time in band again"]
    i1360["#1360 (open) Run an AI-planner-versus-baseline study end to end by exte"]
    i1361["#1361 (closed) Add a Planner section: plan history, cost, experiments, ar"]
    i1363["#1363 (open) Pull every Verdify image through the local registry (no di"]
    i1364["#1364 (open) Use the estate's local NTP for the greenhouse controller i"]
    i1365["#1365 (open) Research sprint: a VPD band the greenhouse can actually ho"]
    i1366["#1366 (closed) Research the planner's 60K-token input pack: what the Cort"]
    i1370["#1370 (open) Turn the lights on when the cameras see someone in the gre"]
  end
  subgraph OPS["OPS: Platform, firmware and estate follow-ups"]
    i1113["#1113 (open) security: grafana-image-renderer chromium CVE-2026-118333/"]
    i1118["#1118 (open) verdify-vision crop-health analysis step fails (exit 1) af"]
    i1271["#1271 (closed) firmware: give sensor_degraded a boot grace before timed L"]
    i1277["#1277 (open) c1 wire validation: move the firmware pin to the engine fi"]
    i1283["#1283 (closed) v_water_meter_daily may read a 25-hour day as complete wit"]
    i1312["#1312 (closed) firmware-replay-diff: rounding lets up to 14 divergent row"]
    i1313["#1313 (open) firmware: runtime and drip accounting after an aborted OTA"]
    i1389["#1389 (closed) experiment-v2 lifecycle/freezer/selector CPU-throttled >50"]
    i1397["#1397 (open) Move CI, image builds and pin automation to GitHub Actions"]
    i1424["#1424 (open) heat1 Shelly ch1 power clamp is wired backwards (reads neg"]
    i1450["#1450 (open) Planner context gather runs ~98-120 s against its 120 s ca"]
  end
  i371 --> i779
  i424 --> i749
  i778 --> i749
  i949 --> i749
  i587 --> i641
  i639 --> i641
  i747 --> i641
  i749 --> i641
  i371 --> i782
  i779 --> i782
  i780 --> i782
  i781 --> i782
  i587 --> i783
  i639 --> i783
  i641 --> i588
  i782 --> i588
  i783 --> i588
  i588 --> i642
  i642 --> i640
  i640 --> i784
  i784 --> i785
  i964 --> i644
  i304 --> i303
  i322 --> i303
  i433 --> i427
  i563 --> i394
  i563 --> i89
  i670 --> i672
  i670 --> i396
  i672 --> i396
  i382 --> i245
  i396 --> i245
  i670 --> i643
  i433 --> i428
  i367 --> i370
  i424 --> i324
  i371 --> i324
  i371 --> i410
  i419 --> i410
  i424 --> i410
  i371 --> i378
  i785 --> i378
  i378 --> i361
  i410 --> i361
  i427 --> i214
  i780 --> i214
  i299 --> i434
  i45 --> i297
  i398 --> i297
  i297 --> i296
  i434 --> i296
  i45 --> i296
  i1183 --> i751
  i1329 --> i298
  i298 --> i398
  i298 --> i45
  i398 --> i45
  i298 --> i52
  i638 --> i586
  i390 --> i586
  i785 --> i586
  i780 --> i786
  i371 --> i379
  i419 --> i379
  i781 --> i787
  i390 --> i963
  i994 --> i999
  i1143 --> i1125
  i1135 --> i1125
  i1164 --> i1125
  i1161 --> i1164
  i1162 --> i1164
  i1165 --> i1166
  i1132 --> i1181
  i1354 --> i1322
  i1332 --> i1329
  i1322 --> i1335
  i1335 --> i1342
  i1434 --> i1360
  i1360 --> i1361
  i1350 --> i1366
  i1365 --> i1431
  i1365 --> i1432
  i1431 --> i1432
  i1433 --> i1434
```

## Exact edge list

| Predecessor | Dependent |
|---|---|
| [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed) | [#779](https://github.com/VerdifyConsultancy/verdify-platform/issues/779) (closed) |
| [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) (closed) | [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) (closed) |
| [#778](https://github.com/VerdifyConsultancy/verdify-platform/issues/778) (closed) | [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) (closed) |
| [#949](https://github.com/VerdifyConsultancy/verdify-platform/issues/949) (closed) | [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) (closed) |
| [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) (closed) | [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) (closed) |
| [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) (closed) | [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) (closed) |
| [#747](https://github.com/VerdifyConsultancy/verdify-platform/issues/747) (closed) | [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) (closed) |
| [#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) (closed) | [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) (closed) |
| [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed) | [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) (closed) |
| [#779](https://github.com/VerdifyConsultancy/verdify-platform/issues/779) (closed) | [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) (closed) |
| [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) (closed) | [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) (closed) |
| [#781](https://github.com/VerdifyConsultancy/verdify-platform/issues/781) (closed) | [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) (closed) |
| [#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) (closed) | [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) (closed) |
| [#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) (closed) | [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) (closed) |
| [#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) (closed) | [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) (closed) |
| [#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) (closed) | [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) (closed) |
| [#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) (closed) | [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) (closed) |
| [#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) (closed) | [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) (closed) |
| [#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) (closed) | [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) (closed) |
| [#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) (closed) | [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784) (closed) |
| [#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784) (closed) | [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) (closed) |
| [#964](https://github.com/VerdifyConsultancy/verdify-platform/issues/964) (closed) | [#644](https://github.com/VerdifyConsultancy/verdify-platform/issues/644) (closed) |
| [#304](https://github.com/VerdifyConsultancy/verdify-platform/issues/304) (closed) | [#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303) (closed) |
| [#322](https://github.com/VerdifyConsultancy/verdify-platform/issues/322) (closed) | [#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303) (closed) |
| [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) (closed) | [#427](https://github.com/VerdifyConsultancy/verdify-platform/issues/427) (closed) |
| [#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) (closed) | [#394](https://github.com/VerdifyConsultancy/verdify-platform/issues/394) (closed) |
| [#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) (closed) | [#89](https://github.com/VerdifyConsultancy/verdify-platform/issues/89) (closed) |
| [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) (closed) | [#672](https://github.com/VerdifyConsultancy/verdify-platform/issues/672) (closed) |
| [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) (closed) | [#396](https://github.com/VerdifyConsultancy/verdify-platform/issues/396) (closed) |
| [#672](https://github.com/VerdifyConsultancy/verdify-platform/issues/672) (closed) | [#396](https://github.com/VerdifyConsultancy/verdify-platform/issues/396) (closed) |
| [#382](https://github.com/VerdifyConsultancy/verdify-platform/issues/382) (closed) | [#245](https://github.com/VerdifyConsultancy/verdify-platform/issues/245) (closed) |
| [#396](https://github.com/VerdifyConsultancy/verdify-platform/issues/396) (closed) | [#245](https://github.com/VerdifyConsultancy/verdify-platform/issues/245) (closed) |
| [#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) (closed) | [#643](https://github.com/VerdifyConsultancy/verdify-platform/issues/643) (closed) |
| [#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) (closed) | [#428](https://github.com/VerdifyConsultancy/verdify-platform/issues/428) (closed) |
| [#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367) (open) | [#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370) (open) |
| [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) (closed) | [#324](https://github.com/VerdifyConsultancy/verdify-platform/issues/324) (closed) |
| [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed) | [#324](https://github.com/VerdifyConsultancy/verdify-platform/issues/324) (closed) |
| [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed) | [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) (closed) |
| [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) (closed) | [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) (closed) |
| [#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) (closed) | [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) (closed) |
| [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed) | [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) (closed) |
| [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) (closed) | [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) (closed) |
| [#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) (closed) | [#361](https://github.com/VerdifyConsultancy/verdify-platform/issues/361) (closed) |
| [#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) (closed) | [#361](https://github.com/VerdifyConsultancy/verdify-platform/issues/361) (closed) |
| [#427](https://github.com/VerdifyConsultancy/verdify-platform/issues/427) (closed) | [#214](https://github.com/VerdifyConsultancy/verdify-platform/issues/214) (closed) |
| [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) (closed) | [#214](https://github.com/VerdifyConsultancy/verdify-platform/issues/214) (closed) |
| [#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299) (open) | [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) (closed) |
| [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) (open) | [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) (open) |
| [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) (open) | [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) (open) |
| [#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) (open) | [#296](https://github.com/VerdifyConsultancy/verdify-platform/issues/296) (closed) |
| [#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) (closed) | [#296](https://github.com/VerdifyConsultancy/verdify-platform/issues/296) (closed) |
| [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) (open) | [#296](https://github.com/VerdifyConsultancy/verdify-platform/issues/296) (closed) |
| [#1183](https://github.com/VerdifyConsultancy/verdify-platform/issues/1183) (open) | [#751](https://github.com/VerdifyConsultancy/verdify-platform/issues/751) (open) |
| [#1329](https://github.com/VerdifyConsultancy/verdify-platform/issues/1329) (open) | [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) (open) |
| [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) (open) | [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) (open) |
| [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) (open) | [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) (open) |
| [#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) (open) | [#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) (open) |
| [#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) (open) | [#52](https://github.com/VerdifyConsultancy/verdify-platform/issues/52) (open) |
| [#638](https://github.com/VerdifyConsultancy/verdify-platform/issues/638) (closed) | [#586](https://github.com/VerdifyConsultancy/verdify-platform/issues/586) (closed) |
| [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) (open) | [#586](https://github.com/VerdifyConsultancy/verdify-platform/issues/586) (closed) |
| [#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) (closed) | [#586](https://github.com/VerdifyConsultancy/verdify-platform/issues/586) (closed) |
| [#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) (closed) | [#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786) (closed) |
| [#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) (closed) | [#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379) (closed) |
| [#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) (closed) | [#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379) (closed) |
| [#781](https://github.com/VerdifyConsultancy/verdify-platform/issues/781) (closed) | [#787](https://github.com/VerdifyConsultancy/verdify-platform/issues/787) (closed) |
| [#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) (open) | [#963](https://github.com/VerdifyConsultancy/verdify-platform/issues/963) (closed) |
| [#994](https://github.com/VerdifyConsultancy/verdify-platform/issues/994) (closed) | [#999](https://github.com/VerdifyConsultancy/verdify-platform/issues/999) (closed) |
| [#1143](https://github.com/VerdifyConsultancy/verdify-platform/issues/1143) (closed) | [#1125](https://github.com/VerdifyConsultancy/verdify-platform/issues/1125) (closed) |
| [#1135](https://github.com/VerdifyConsultancy/verdify-platform/issues/1135) (open) | [#1125](https://github.com/VerdifyConsultancy/verdify-platform/issues/1125) (closed) |
| [#1164](https://github.com/VerdifyConsultancy/verdify-platform/issues/1164) (open) | [#1125](https://github.com/VerdifyConsultancy/verdify-platform/issues/1125) (closed) |
| [#1161](https://github.com/VerdifyConsultancy/verdify-platform/issues/1161) (open) | [#1164](https://github.com/VerdifyConsultancy/verdify-platform/issues/1164) (open) |
| [#1162](https://github.com/VerdifyConsultancy/verdify-platform/issues/1162) (open) | [#1164](https://github.com/VerdifyConsultancy/verdify-platform/issues/1164) (open) |
| [#1165](https://github.com/VerdifyConsultancy/verdify-platform/issues/1165) (open) | [#1166](https://github.com/VerdifyConsultancy/verdify-platform/issues/1166) (open) |
| [#1132](https://github.com/VerdifyConsultancy/verdify-platform/issues/1132) (open) | [#1181](https://github.com/VerdifyConsultancy/verdify-platform/issues/1181) (open) |
| [#1354](https://github.com/VerdifyConsultancy/verdify-platform/issues/1354) (open) | [#1322](https://github.com/VerdifyConsultancy/verdify-platform/issues/1322) (open) |
| [#1332](https://github.com/VerdifyConsultancy/verdify-platform/issues/1332) (open) | [#1329](https://github.com/VerdifyConsultancy/verdify-platform/issues/1329) (open) |
| [#1322](https://github.com/VerdifyConsultancy/verdify-platform/issues/1322) (open) | [#1335](https://github.com/VerdifyConsultancy/verdify-platform/issues/1335) (open) |
| [#1335](https://github.com/VerdifyConsultancy/verdify-platform/issues/1335) (open) | [#1342](https://github.com/VerdifyConsultancy/verdify-platform/issues/1342) (open) |
| [#1434](https://github.com/VerdifyConsultancy/verdify-platform/issues/1434) (open) | [#1360](https://github.com/VerdifyConsultancy/verdify-platform/issues/1360) (open) |
| [#1360](https://github.com/VerdifyConsultancy/verdify-platform/issues/1360) (open) | [#1361](https://github.com/VerdifyConsultancy/verdify-platform/issues/1361) (closed) |
| [#1350](https://github.com/VerdifyConsultancy/verdify-platform/issues/1350) (closed) | [#1366](https://github.com/VerdifyConsultancy/verdify-platform/issues/1366) (closed) |
| [#1365](https://github.com/VerdifyConsultancy/verdify-platform/issues/1365) (open) | [#1431](https://github.com/VerdifyConsultancy/verdify-platform/issues/1431) (open) |
| [#1365](https://github.com/VerdifyConsultancy/verdify-platform/issues/1365) (open) | [#1432](https://github.com/VerdifyConsultancy/verdify-platform/issues/1432) (open) |
| [#1431](https://github.com/VerdifyConsultancy/verdify-platform/issues/1431) (open) | [#1432](https://github.com/VerdifyConsultancy/verdify-platform/issues/1432) (open) |
| [#1433](https://github.com/VerdifyConsultancy/verdify-platform/issues/1433) (open) | [#1434](https://github.com/VerdifyConsultancy/verdify-platform/issues/1434) (open) |

Closed #676 remains historical qualification evidence, not an open blocker. Cross-repository fleet/monitoring delivery is a concrete implementation interface in the relevant issues, not a fabricated in-repository node. Physical windows and minimum live-state invariants remain explicit issue conditions.
