# Complete native dependency graph — October 12, 2026

95 nodes: 91 Verdify issues (64 open, 27 closed) and four transitive external prerequisites. All 80 native blocking edges are included. The 88 canonical campaign nodes retain their 76 exact source/native edges; #317/#801/#802 and external owner dependencies are explicit. Arrows mean predecessor → dependent acceptance. Parentage is ownership and is excluded from blocking arrows. Preparation can proceed before final acceptance.

```mermaid
flowchart TD
  VerdifyConsultancy_verdify_platform_14["VerdifyConsultancy/verdify-platform#14 (C8)(P2) Twin program: qualify declared oracle coverage before u"]
  class VerdifyConsultancy_verdify_platform_14 open
  VerdifyConsultancy_verdify_platform_16["VerdifyConsultancy/verdify-platform#16 (C7)(P2) Hardware program: commission only the sensing and equip"]
  class VerdifyConsultancy_verdify_platform_16 open
  VerdifyConsultancy_verdify_platform_31["VerdifyConsultancy/verdify-platform#31 (C8)(P2) Close twin setpoint and actuator coverage gaps with gen"]
  class VerdifyConsultancy_verdify_platform_31 open
  VerdifyConsultancy_verdify_platform_45["VerdifyConsultancy/verdify-platform#45 (C7)(P2) Commission soil and runoff feedback before enabling irr"]
  class VerdifyConsultancy_verdify_platform_45 open
  VerdifyConsultancy_verdify_platform_49["VerdifyConsultancy/verdify-platform#49 (C5)(P1) Audit and close historical suppressed sensor alerts wit"]
  class VerdifyConsultancy_verdify_platform_49 closed
  VerdifyConsultancy_verdify_platform_51["VerdifyConsultancy/verdify-platform#51 (C7)(P2) Calibrate CO2 against a real reference and publish meas"]
  class VerdifyConsultancy_verdify_platform_51 open
  VerdifyConsultancy_verdify_platform_52["VerdifyConsultancy/verdify-platform#52 (C7)(P2) Define species-appropriate seasonal dormancy care from "]
  class VerdifyConsultancy_verdify_platform_52 open
  VerdifyConsultancy_verdify_platform_75["VerdifyConsultancy/verdify-platform#75 (C4)(P1) Observability program: prove actionable health from sou"]
  class VerdifyConsultancy_verdify_platform_75 open
  VerdifyConsultancy_verdify_platform_89["VerdifyConsultancy/verdify-platform#89 (C4)(P1) Verify exact-source post-deploy smoke and device-route "]
  class VerdifyConsultancy_verdify_platform_89 closed
  VerdifyConsultancy_verdify_platform_174["VerdifyConsultancy/verdify-platform#174 (C8)(P2) Rehome only current Verdify admin surfaces to global SS"]
  class VerdifyConsultancy_verdify_platform_174 closed
  VerdifyConsultancy_verdify_platform_214["VerdifyConsultancy/verdify-platform#214 (C6)(P1) Prove crop-deviation trigger → valid plan on the sole o"]
  class VerdifyConsultancy_verdify_platform_214 closed
  VerdifyConsultancy_verdify_platform_218["VerdifyConsultancy/verdify-platform#218 (C5)(P1) Durability program: recoverable backups, measured RPO/R"]
  class VerdifyConsultancy_verdify_platform_218 open
  VerdifyConsultancy_verdify_platform_245["VerdifyConsultancy/verdify-platform#245 (C5)(P1) Execute a separately bounded database cutover only afte"]
  class VerdifyConsultancy_verdify_platform_245 open
  VerdifyConsultancy_verdify_platform_296["VerdifyConsultancy/verdify-platform#296 (C6)(P1) Consider slow-hysteresis firmware irrigation feedback o"]
  class VerdifyConsultancy_verdify_platform_296 open
  VerdifyConsultancy_verdify_platform_297["VerdifyConsultancy/verdify-platform#297 (C6)(P1) Add saturation alerts and a commissioned dispatcher-sid"]
  class VerdifyConsultancy_verdify_platform_297 open
  VerdifyConsultancy_verdify_platform_298["VerdifyConsultancy/verdify-platform#298 (C7)(P2) Rebaseline actual crop, pot and probe topology before f"]
  class VerdifyConsultancy_verdify_platform_298 open
  VerdifyConsultancy_verdify_platform_299["VerdifyConsultancy/verdify-platform#299 (C6)(P1) Preserve center-mister re-fire protection through topol"]
  class VerdifyConsultancy_verdify_platform_299 open
  VerdifyConsultancy_verdify_platform_303["VerdifyConsultancy/verdify-platform#303 (C4)(P1) Run real portable ESPHome compilation for declared firm"]
  class VerdifyConsultancy_verdify_platform_303 open
  VerdifyConsultancy_verdify_platform_304["VerdifyConsultancy/verdify-platform#304 (C4)(P1) Provide reproducible least-authority firmware and datab"]
  class VerdifyConsultancy_verdify_platform_304 open
  VerdifyConsultancy_verdify_platform_317["VerdifyConsultancy/verdify-platform#317 ArgoCD: unscoped sync operations on verdify-prod-dark get rewrit"]
  class VerdifyConsultancy_verdify_platform_317 open
  VerdifyConsultancy_verdify_platform_322["VerdifyConsultancy/verdify-platform#322 (C4)(P1) Replace remaining VM-era tests with current k3s deliver"]
  class VerdifyConsultancy_verdify_platform_322 closed
  VerdifyConsultancy_verdify_platform_324["VerdifyConsultancy/verdify-platform#324 (C6)(P1) Extend zonal band lineage compatibly for future determi"]
  class VerdifyConsultancy_verdify_platform_324 open
  VerdifyConsultancy_verdify_platform_350["VerdifyConsultancy/verdify-platform#350 (C6)(P1) Irrigation program: topology truth, overwatering protec"]
  class VerdifyConsultancy_verdify_platform_350 open
  VerdifyConsultancy_verdify_platform_359["VerdifyConsultancy/verdify-platform#359 (C6)(P1) Control program: truthful crop corridors before evidenc"]
  class VerdifyConsultancy_verdify_platform_359 open
  VerdifyConsultancy_verdify_platform_361["VerdifyConsultancy/verdify-platform#361 (C6)(P1) Reassess diurnal anchors only after solar parity and me"]
  class VerdifyConsultancy_verdify_platform_361 open
  VerdifyConsultancy_verdify_platform_367["VerdifyConsultancy/verdify-platform#367 (C6)(P1) Consolidate anti-chatter into one explicit dwell contra"]
  class VerdifyConsultancy_verdify_platform_367 open
  VerdifyConsultancy_verdify_platform_368["VerdifyConsultancy/verdify-platform#368 (C6)(P1) Define shared sensor jump, flatline and contributor-int"]
  class VerdifyConsultancy_verdify_platform_368 open
  VerdifyConsultancy_verdify_platform_369["VerdifyConsultancy/verdify-platform#369 (C6)(P1) Remove dead tunables and code from a verified consumer "]
  class VerdifyConsultancy_verdify_platform_369 open
  VerdifyConsultancy_verdify_platform_370["VerdifyConsultancy/verdify-platform#370 (C6)(P1) Consolidate equipment conflict resolution into one pure"]
  class VerdifyConsultancy_verdify_platform_370 open
  VerdifyConsultancy_verdify_platform_371["VerdifyConsultancy/verdify-platform#371 (C0)(P0) Repair climate score semantics and publish physical out"]
  class VerdifyConsultancy_verdify_platform_371 closed
  VerdifyConsultancy_verdify_platform_378["VerdifyConsultancy/verdify-platform#378 (C6)(P1) Decide corridor widths from fixed-target crop and resou"]
  class VerdifyConsultancy_verdify_platform_378 open
  VerdifyConsultancy_verdify_platform_379["VerdifyConsultancy/verdify-platform#379 (C8)(P2) Gate grey-box forecasting and MPC on validated response"]
  class VerdifyConsultancy_verdify_platform_379 open
  VerdifyConsultancy_verdify_platform_382["VerdifyConsultancy/verdify-platform#382 (C5)(P1) Persist ingestor queue and spool across restart and nod"]
  class VerdifyConsultancy_verdify_platform_382 open
  VerdifyConsultancy_verdify_platform_386["VerdifyConsultancy/verdify-platform#386 (C4)(P1) Prove grow-light minimum-on behavior at the solar-windo"]
  class VerdifyConsultancy_verdify_platform_386 open
  VerdifyConsultancy_verdify_platform_390["VerdifyConsultancy/verdify-platform#390 (C4)(P1) Gate firmware releases on topology, cycling, runtime sa"]
  class VerdifyConsultancy_verdify_platform_390 open
  VerdifyConsultancy_verdify_platform_394["VerdifyConsultancy/verdify-platform#394 (C4)(P1) Deliver and fault-test live split-brain and no-writer a"]
  class VerdifyConsultancy_verdify_platform_394 closed
  VerdifyConsultancy_verdify_platform_396["VerdifyConsultancy/verdify-platform#396 (C5)(P1) Design and rehearse CNPG alongside the live database on"]
  class VerdifyConsultancy_verdify_platform_396 open
  VerdifyConsultancy_verdify_platform_398["VerdifyConsultancy/verdify-platform#398 (C7)(P2) Replace stale soil threshold seeds with commissioned cr"]
  class VerdifyConsultancy_verdify_platform_398 open
  VerdifyConsultancy_verdify_platform_399["VerdifyConsultancy/verdify-platform#399 (C4)(P1) Prove the HA grow-light writer remains within the singl"]
  class VerdifyConsultancy_verdify_platform_399 closed
  VerdifyConsultancy_verdify_platform_410["VerdifyConsultancy/verdify-platform#410 (C6)(P1) Measure realized solar-night dry-out before changing th"]
  class VerdifyConsultancy_verdify_platform_410 open
  VerdifyConsultancy_verdify_platform_412["VerdifyConsultancy/verdify-platform#412 (C7)(P2) Close the seasonal door screen only when measured night"]
  class VerdifyConsultancy_verdify_platform_412 open
  VerdifyConsultancy_verdify_platform_419["VerdifyConsultancy/verdify-platform#419 (C4)(P1) Populate real outdoor freshness in replay and enforce b"]
  class VerdifyConsultancy_verdify_platform_419 open
  VerdifyConsultancy_verdify_platform_424["VerdifyConsultancy/verdify-platform#424 (C0)(P0) Resolve served, consumed and raw-readback band lineage "]
  class VerdifyConsultancy_verdify_platform_424 closed
  VerdifyConsultancy_verdify_platform_427["VerdifyConsultancy/verdify-platform#427 (C4)(P1) Prove active Hermes/MCP recovery and truthful non-autho"]
  class VerdifyConsultancy_verdify_platform_427 closed
  VerdifyConsultancy_verdify_platform_428["VerdifyConsultancy/verdify-platform#428 (C6)(P1) Diagnose heap/watchdog risk and qualify the actual last"]
  class VerdifyConsultancy_verdify_platform_428 open
  VerdifyConsultancy_verdify_platform_430["VerdifyConsultancy/verdify-platform#430 (C6)(P1) Simplify firmware only while preserving autonomous cont"]
  class VerdifyConsultancy_verdify_platform_430 open
  VerdifyConsultancy_verdify_platform_433["VerdifyConsultancy/verdify-platform#433 (C4)(P1) Finish truthful single-writer command lifecycle and no-"]
  class VerdifyConsultancy_verdify_platform_433 closed
  VerdifyConsultancy_verdify_platform_434["VerdifyConsultancy/verdify-platform#434 (C6)(P1) Enforce center-only climate mist and commissioned wall-"]
  class VerdifyConsultancy_verdify_platform_434 open
  VerdifyConsultancy_verdify_platform_563["VerdifyConsultancy/verdify-platform#563 (C4)(P1) Enforce the actual ConfigMap-based alert delivery contr"]
  class VerdifyConsultancy_verdify_platform_563 closed
  VerdifyConsultancy_verdify_platform_581["VerdifyConsultancy/verdify-platform#581 (C2)(P0) Experiment program: qualify, run and read out the bound"]
  class VerdifyConsultancy_verdify_platform_581 open
  VerdifyConsultancy_verdify_platform_586["VerdifyConsultancy/verdify-platform#586 (C8)(P2) Qualify a future atomic firmware policy engine with cra"]
  class VerdifyConsultancy_verdify_platform_586 open
  VerdifyConsultancy_verdify_platform_587["VerdifyConsultancy/verdify-platform#587 (C1)(P0) Qualify fail-closed lifecycle, kill switch and blinded "]
  class VerdifyConsultancy_verdify_platform_587 open
  VerdifyConsultancy_verdify_platform_588["VerdifyConsultancy/verdify-platform#588 (C2)(P0) Lock the qualified pilot identity and finalize exactly "]
  class VerdifyConsultancy_verdify_platform_588 open
  VerdifyConsultancy_verdify_platform_606["VerdifyConsultancy/verdify-platform#606 (C8)(P2) Register the twin build profile only through its owning"]
  class VerdifyConsultancy_verdify_platform_606 open
  VerdifyConsultancy_verdify_platform_638["VerdifyConsultancy/verdify-platform#638 (C8)(P2) Unify future manifest/vector wire schema and content id"]
  class VerdifyConsultancy_verdify_platform_638 open
  VerdifyConsultancy_verdify_platform_639["VerdifyConsultancy/verdify-platform#639 (C1)(P0) Qualify the existing component executor and full-state "]
  class VerdifyConsultancy_verdify_platform_639 open
  VerdifyConsultancy_verdify_platform_640["VerdifyConsultancy/verdify-platform#640 (C3)(P0) Freeze the first assigned-day ITT outcome and blinded r"]
  class VerdifyConsultancy_verdify_platform_640 open
  VerdifyConsultancy_verdify_platform_641["VerdifyConsultancy/verdify-platform#641 (C1)(P0) Execute authorized orphan recovery and one separately a"]
  class VerdifyConsultancy_verdify_platform_641 open
  VerdifyConsultancy_verdify_platform_642["VerdifyConsultancy/verdify-platform#642 (C2)(P0) Authorize and verify one randomized day-1 activation fr"]
  class VerdifyConsultancy_verdify_platform_642 open
  VerdifyConsultancy_verdify_platform_643["VerdifyConsultancy/verdify-platform#643 (C5)(P1) Extend least-privilege database roles beyond the alread"]
  class VerdifyConsultancy_verdify_platform_643 open
  VerdifyConsultancy_verdify_platform_644["VerdifyConsultancy/verdify-platform#644 (C4)(P1) Integrate exact-SHA, path-aware and device-safe deliver"]
  class VerdifyConsultancy_verdify_platform_644 open
  VerdifyConsultancy_verdify_platform_670["VerdifyConsultancy/verdify-platform#670 (C5)(P1) Make backup artifacts sufficient to restore roles, owne"]
  class VerdifyConsultancy_verdify_platform_670 closed
  VerdifyConsultancy_verdify_platform_671["VerdifyConsultancy/verdify-platform#671 (C4)(P1) Make Lab publishing failures and cache contention obser"]
  class VerdifyConsultancy_verdify_platform_671 closed
  VerdifyConsultancy_verdify_platform_672["VerdifyConsultancy/verdify-platform#672 (C5)(P1) Make compressed-chunk ownership restoration a supported"]
  class VerdifyConsultancy_verdify_platform_672 closed
  VerdifyConsultancy_verdify_platform_747["VerdifyConsultancy/verdify-platform#747 (C1)(P0) Verify corrected scheduled backups and current producti"]
  class VerdifyConsultancy_verdify_platform_747 closed
  VerdifyConsultancy_verdify_platform_749["VerdifyConsultancy/verdify-platform#749 (C1)(P0) Qualify current three-probe readiness and explicit safe"]
  class VerdifyConsultancy_verdify_platform_749 open
  VerdifyConsultancy_verdify_platform_750["VerdifyConsultancy/verdify-platform#750 (C1)(P0) Recovery/proof campaign: rebaseline current state and s"]
  class VerdifyConsultancy_verdify_platform_750 open
  VerdifyConsultancy_verdify_platform_751["VerdifyConsultancy/verdify-platform#751 (C7)(P2) Replace the failed south climate probe and diagnose hyd"]
  class VerdifyConsultancy_verdify_platform_751 open
  VerdifyConsultancy_verdify_platform_775["VerdifyConsultancy/verdify-platform#775 (C0)(P0) Campaign: trustworthy greenhouse outcomes → qualified p"]
  class VerdifyConsultancy_verdify_platform_775 open
  VerdifyConsultancy_verdify_platform_778["VerdifyConsultancy/verdify-platform#778 (C0)(P0) Investigate September 4 hot/dry peak and three-hour wet"]
  class VerdifyConsultancy_verdify_platform_778 closed
  VerdifyConsultancy_verdify_platform_779["VerdifyConsultancy/verdify-platform#779 (C0)(P1) Re-run historical planner comparisons on a fixed sensor"]
  class VerdifyConsultancy_verdify_platform_779 closed
  VerdifyConsultancy_verdify_platform_780["VerdifyConsultancy/verdify-platform#780 (C0)(P0) Score as-of outdoor forecasts against outdoor truth and"]
  class VerdifyConsultancy_verdify_platform_780 closed
  VerdifyConsultancy_verdify_platform_781["VerdifyConsultancy/verdify-platform#781 (C0)(P0) Commission a claim-safe water and electricity endpoint "]
  class VerdifyConsultancy_verdify_platform_781 closed
  VerdifyConsultancy_verdify_platform_782["VerdifyConsultancy/verdify-platform#782 (C0)(P0) Choose a season-appropriate exploratory pilot and freez"]
  class VerdifyConsultancy_verdify_platform_782 open
  VerdifyConsultancy_verdify_platform_783["VerdifyConsultancy/verdify-platform#783 (C1)(P0) Prove restore → selector → setter schema → receipt → fr"]
  class VerdifyConsultancy_verdify_platform_783 open
  VerdifyConsultancy_verdify_platform_784["VerdifyConsultancy/verdify-platform#784 (C3)(P1) Operate the complete blinded pilot and preserve every a"]
  class VerdifyConsultancy_verdify_platform_784 open
  VerdifyConsultancy_verdify_platform_785["VerdifyConsultancy/verdify-platform#785 (C3)(P1) Reveal once, reproduce the frozen analysis and publish "]
  class VerdifyConsultancy_verdify_platform_785 open
  VerdifyConsultancy_verdify_platform_786["VerdifyConsultancy/verdify-platform#786 (C8)(P2) Design the next comparison against a deterministic fore"]
  class VerdifyConsultancy_verdify_platform_786 open
  VerdifyConsultancy_verdify_platform_787["VerdifyConsultancy/verdify-platform#787 (C8)(P2) Plan measured heating, overnight and whole-resource eco"]
  class VerdifyConsultancy_verdify_platform_787 open
  VerdifyConsultancy_verdify_platform_801["VerdifyConsultancy/verdify-platform#801 (VP-02) Exclude www-dev and proposals-hello from the verdify-tie"]
  class VerdifyConsultancy_verdify_platform_801 open
  VerdifyConsultancy_verdify_platform_802["VerdifyConsultancy/verdify-platform#802 (VP-01) Put verdify-descheduler under Argo (manual) with Git equ"]
  class VerdifyConsultancy_verdify_platform_802 open
  VerdifyConsultancy_verdify_platform_835["VerdifyConsultancy/verdify-platform#835 Fix reconnect equipment snapshot provenance and stale relay aler"]
  class VerdifyConsultancy_verdify_platform_835 closed
  VerdifyConsultancy_verdify_platform_862["VerdifyConsultancy/verdify-platform#862 Lab publisher: warm-cache init rescans the public tree for ~5 mi"]
  class VerdifyConsultancy_verdify_platform_862 closed
  VerdifyConsultancy_verdify_platform_949["VerdifyConsultancy/verdify-platform#949 (C4)(P1) Bound planner-policy drift holds and prove autonomous s"]
  class VerdifyConsultancy_verdify_platform_949 open
  VerdifyConsultancy_verdify_platform_950["VerdifyConsultancy/verdify-platform#950 (C4)(P1) Restore the ingestor Slack credential-file contract and"]
  class VerdifyConsultancy_verdify_platform_950 closed
  VerdifyConsultancy_verdify_platform_951["VerdifyConsultancy/verdify-platform#951 (C4)(P1) Restore authenticated greenhouse camera snapshots after"]
  class VerdifyConsultancy_verdify_platform_951 closed
  VerdifyConsultancy_verdify_platform_952["VerdifyConsultancy/verdify-platform#952 (C4)(P1) Preserve Verdify Grafana monitoring identity across pod"]
  class VerdifyConsultancy_verdify_platform_952 closed
  VerdifyConsultancy_verdify_platform_953["VerdifyConsultancy/verdify-platform#953 (C4)(P2) Reconcile Iris planning skill lookup and required refer"]
  class VerdifyConsultancy_verdify_platform_953 closed
  VerdifyConsultancy_verdify_platform_963["VerdifyConsultancy/verdify-platform#963 Greenhouse ESPHome text sensor moisture_exchange publishes >255-"]
  class VerdifyConsultancy_verdify_platform_963 open
  VerdifyConsultancy_verdify_platform_964["VerdifyConsultancy/verdify-platform#964 (C4)(P0) Recover failed production promotion and schedulable Lab"]
  class VerdifyConsultancy_verdify_platform_964 closed
  VerdifyConsultancy_verdify_platform_965["VerdifyConsultancy/verdify-platform#965 (C4)(P1) Separate API grow-light command intent from observed eq"]
  class VerdifyConsultancy_verdify_platform_965 open
  jvallery_agents_4461["jvallery/agents#4461 (ARGO-02) Adopt the hand-applied tenant Applications into the lo"]
  class jvallery_agents_4461 open
  jvallery_agents_4451["jvallery/agents#4451 (EST-01) Create estate-ownership.yaml and the estate lane map (d"]
  class jvallery_agents_4451 open
  jvallery_agents_4459["jvallery/agents#4459 (ROOT-02) Reconcile the three env roots and agent-fleet-appproje"]
  class jvallery_agents_4459 closed
  jvallery_agents_4460["jvallery/agents#4460 (ARGO-01) Adopt 10 hand-applied AppProjects into agents and dele"]
  class jvallery_agents_4460 open
  VerdifyConsultancy_verdify_platform_398 --> VerdifyConsultancy_verdify_platform_45
  VerdifyConsultancy_verdify_platform_563 --> VerdifyConsultancy_verdify_platform_89
  VerdifyConsultancy_verdify_platform_780 --> VerdifyConsultancy_verdify_platform_214
  VerdifyConsultancy_verdify_platform_427 --> VerdifyConsultancy_verdify_platform_214
  VerdifyConsultancy_verdify_platform_396 --> VerdifyConsultancy_verdify_platform_245
  VerdifyConsultancy_verdify_platform_382 --> VerdifyConsultancy_verdify_platform_245
  VerdifyConsultancy_verdify_platform_434 --> VerdifyConsultancy_verdify_platform_296
  VerdifyConsultancy_verdify_platform_297 --> VerdifyConsultancy_verdify_platform_296
  VerdifyConsultancy_verdify_platform_45 --> VerdifyConsultancy_verdify_platform_296
  VerdifyConsultancy_verdify_platform_398 --> VerdifyConsultancy_verdify_platform_297
  VerdifyConsultancy_verdify_platform_45 --> VerdifyConsultancy_verdify_platform_297
  VerdifyConsultancy_verdify_platform_390 --> VerdifyConsultancy_verdify_platform_299
  VerdifyConsultancy_verdify_platform_322 --> VerdifyConsultancy_verdify_platform_303
  VerdifyConsultancy_verdify_platform_304 --> VerdifyConsultancy_verdify_platform_303
  VerdifyConsultancy_verdify_platform_424 --> VerdifyConsultancy_verdify_platform_324
  VerdifyConsultancy_verdify_platform_371 --> VerdifyConsultancy_verdify_platform_324
  VerdifyConsultancy_verdify_platform_410 --> VerdifyConsultancy_verdify_platform_361
  VerdifyConsultancy_verdify_platform_378 --> VerdifyConsultancy_verdify_platform_361
  VerdifyConsultancy_verdify_platform_390 --> VerdifyConsultancy_verdify_platform_367
  VerdifyConsultancy_verdify_platform_424 --> VerdifyConsultancy_verdify_platform_368
  VerdifyConsultancy_verdify_platform_433 --> VerdifyConsultancy_verdify_platform_369
  VerdifyConsultancy_verdify_platform_390 --> VerdifyConsultancy_verdify_platform_369
  VerdifyConsultancy_verdify_platform_390 --> VerdifyConsultancy_verdify_platform_370
  VerdifyConsultancy_verdify_platform_367 --> VerdifyConsultancy_verdify_platform_370
  VerdifyConsultancy_verdify_platform_785 --> VerdifyConsultancy_verdify_platform_378
  VerdifyConsultancy_verdify_platform_371 --> VerdifyConsultancy_verdify_platform_378
  VerdifyConsultancy_verdify_platform_786 --> VerdifyConsultancy_verdify_platform_379
  VerdifyConsultancy_verdify_platform_419 --> VerdifyConsultancy_verdify_platform_379
  VerdifyConsultancy_verdify_platform_371 --> VerdifyConsultancy_verdify_platform_379
  VerdifyConsultancy_verdify_platform_303 --> VerdifyConsultancy_verdify_platform_386
  VerdifyConsultancy_verdify_platform_419 --> VerdifyConsultancy_verdify_platform_390
  VerdifyConsultancy_verdify_platform_303 --> VerdifyConsultancy_verdify_platform_390
  VerdifyConsultancy_verdify_platform_563 --> VerdifyConsultancy_verdify_platform_394
  VerdifyConsultancy_verdify_platform_672 --> VerdifyConsultancy_verdify_platform_396
  VerdifyConsultancy_verdify_platform_670 --> VerdifyConsultancy_verdify_platform_396
  VerdifyConsultancy_verdify_platform_298 --> VerdifyConsultancy_verdify_platform_398
  VerdifyConsultancy_verdify_platform_424 --> VerdifyConsultancy_verdify_platform_410
  VerdifyConsultancy_verdify_platform_419 --> VerdifyConsultancy_verdify_platform_410
  VerdifyConsultancy_verdify_platform_371 --> VerdifyConsultancy_verdify_platform_410
  VerdifyConsultancy_verdify_platform_433 --> VerdifyConsultancy_verdify_platform_427
  VerdifyConsultancy_verdify_platform_433 --> VerdifyConsultancy_verdify_platform_428
  VerdifyConsultancy_verdify_platform_390 --> VerdifyConsultancy_verdify_platform_434
  VerdifyConsultancy_verdify_platform_299 --> VerdifyConsultancy_verdify_platform_434
  VerdifyConsultancy_verdify_platform_785 --> VerdifyConsultancy_verdify_platform_586
  VerdifyConsultancy_verdify_platform_638 --> VerdifyConsultancy_verdify_platform_586
  VerdifyConsultancy_verdify_platform_390 --> VerdifyConsultancy_verdify_platform_586
  VerdifyConsultancy_verdify_platform_783 --> VerdifyConsultancy_verdify_platform_588
  VerdifyConsultancy_verdify_platform_782 --> VerdifyConsultancy_verdify_platform_588
  VerdifyConsultancy_verdify_platform_641 --> VerdifyConsultancy_verdify_platform_588
  VerdifyConsultancy_verdify_platform_642 --> VerdifyConsultancy_verdify_platform_640
  VerdifyConsultancy_verdify_platform_749 --> VerdifyConsultancy_verdify_platform_641
  VerdifyConsultancy_verdify_platform_747 --> VerdifyConsultancy_verdify_platform_641
  VerdifyConsultancy_verdify_platform_639 --> VerdifyConsultancy_verdify_platform_641
  VerdifyConsultancy_verdify_platform_587 --> VerdifyConsultancy_verdify_platform_641
  VerdifyConsultancy_verdify_platform_588 --> VerdifyConsultancy_verdify_platform_642
  VerdifyConsultancy_verdify_platform_670 --> VerdifyConsultancy_verdify_platform_643
  VerdifyConsultancy_verdify_platform_964 --> VerdifyConsultancy_verdify_platform_644
  VerdifyConsultancy_verdify_platform_670 --> VerdifyConsultancy_verdify_platform_672
  VerdifyConsultancy_verdify_platform_949 --> VerdifyConsultancy_verdify_platform_749
  VerdifyConsultancy_verdify_platform_778 --> VerdifyConsultancy_verdify_platform_749
  VerdifyConsultancy_verdify_platform_424 --> VerdifyConsultancy_verdify_platform_749
  VerdifyConsultancy_verdify_platform_371 --> VerdifyConsultancy_verdify_platform_779
  VerdifyConsultancy_verdify_platform_781 --> VerdifyConsultancy_verdify_platform_782
  VerdifyConsultancy_verdify_platform_780 --> VerdifyConsultancy_verdify_platform_782
  VerdifyConsultancy_verdify_platform_779 --> VerdifyConsultancy_verdify_platform_782
  VerdifyConsultancy_verdify_platform_371 --> VerdifyConsultancy_verdify_platform_782
  VerdifyConsultancy_verdify_platform_782 --> VerdifyConsultancy_verdify_platform_783
  VerdifyConsultancy_verdify_platform_639 --> VerdifyConsultancy_verdify_platform_783
  VerdifyConsultancy_verdify_platform_587 --> VerdifyConsultancy_verdify_platform_783
  VerdifyConsultancy_verdify_platform_640 --> VerdifyConsultancy_verdify_platform_784
  VerdifyConsultancy_verdify_platform_784 --> VerdifyConsultancy_verdify_platform_785
  VerdifyConsultancy_verdify_platform_785 --> VerdifyConsultancy_verdify_platform_786
  VerdifyConsultancy_verdify_platform_780 --> VerdifyConsultancy_verdify_platform_786
  VerdifyConsultancy_verdify_platform_785 --> VerdifyConsultancy_verdify_platform_787
  VerdifyConsultancy_verdify_platform_781 --> VerdifyConsultancy_verdify_platform_787
  VerdifyConsultancy_verdify_platform_410 --> VerdifyConsultancy_verdify_platform_787
  jvallery_agents_4461 --> VerdifyConsultancy_verdify_platform_801
  jvallery_agents_4451 --> VerdifyConsultancy_verdify_platform_802
  jvallery_agents_4459 --> VerdifyConsultancy_verdify_platform_802
  jvallery_agents_4460 --> jvallery_agents_4461
  classDef closed fill:#e5e7eb,color:#374151,stroke:#9ca3af
  classDef open fill:#e0f2fe,color:#0c4a6e,stroke:#0284c7
```

## Exact edge list

| Predecessor | State | Dependent | State |
|---|---|---|---|
| [VerdifyConsultancy/verdify-platform#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) | OPEN | [VerdifyConsultancy/verdify-platform#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) | OPEN |
| [VerdifyConsultancy/verdify-platform#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) | CLOSED | [VerdifyConsultancy/verdify-platform#89](https://github.com/VerdifyConsultancy/verdify-platform/issues/89) | CLOSED |
| [VerdifyConsultancy/verdify-platform#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) | CLOSED | [VerdifyConsultancy/verdify-platform#214](https://github.com/VerdifyConsultancy/verdify-platform/issues/214) | CLOSED |
| [VerdifyConsultancy/verdify-platform#427](https://github.com/VerdifyConsultancy/verdify-platform/issues/427) | CLOSED | [VerdifyConsultancy/verdify-platform#214](https://github.com/VerdifyConsultancy/verdify-platform/issues/214) | CLOSED |
| [VerdifyConsultancy/verdify-platform#396](https://github.com/VerdifyConsultancy/verdify-platform/issues/396) | OPEN | [VerdifyConsultancy/verdify-platform#245](https://github.com/VerdifyConsultancy/verdify-platform/issues/245) | OPEN |
| [VerdifyConsultancy/verdify-platform#382](https://github.com/VerdifyConsultancy/verdify-platform/issues/382) | OPEN | [VerdifyConsultancy/verdify-platform#245](https://github.com/VerdifyConsultancy/verdify-platform/issues/245) | OPEN |
| [VerdifyConsultancy/verdify-platform#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) | OPEN | [VerdifyConsultancy/verdify-platform#296](https://github.com/VerdifyConsultancy/verdify-platform/issues/296) | OPEN |
| [VerdifyConsultancy/verdify-platform#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) | OPEN | [VerdifyConsultancy/verdify-platform#296](https://github.com/VerdifyConsultancy/verdify-platform/issues/296) | OPEN |
| [VerdifyConsultancy/verdify-platform#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) | OPEN | [VerdifyConsultancy/verdify-platform#296](https://github.com/VerdifyConsultancy/verdify-platform/issues/296) | OPEN |
| [VerdifyConsultancy/verdify-platform#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) | OPEN | [VerdifyConsultancy/verdify-platform#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) | OPEN |
| [VerdifyConsultancy/verdify-platform#45](https://github.com/VerdifyConsultancy/verdify-platform/issues/45) | OPEN | [VerdifyConsultancy/verdify-platform#297](https://github.com/VerdifyConsultancy/verdify-platform/issues/297) | OPEN |
| [VerdifyConsultancy/verdify-platform#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | OPEN | [VerdifyConsultancy/verdify-platform#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299) | OPEN |
| [VerdifyConsultancy/verdify-platform#322](https://github.com/VerdifyConsultancy/verdify-platform/issues/322) | CLOSED | [VerdifyConsultancy/verdify-platform#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303) | OPEN |
| [VerdifyConsultancy/verdify-platform#304](https://github.com/VerdifyConsultancy/verdify-platform/issues/304) | OPEN | [VerdifyConsultancy/verdify-platform#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303) | OPEN |
| [VerdifyConsultancy/verdify-platform#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) | CLOSED | [VerdifyConsultancy/verdify-platform#324](https://github.com/VerdifyConsultancy/verdify-platform/issues/324) | OPEN |
| [VerdifyConsultancy/verdify-platform#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) | CLOSED | [VerdifyConsultancy/verdify-platform#324](https://github.com/VerdifyConsultancy/verdify-platform/issues/324) | OPEN |
| [VerdifyConsultancy/verdify-platform#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) | OPEN | [VerdifyConsultancy/verdify-platform#361](https://github.com/VerdifyConsultancy/verdify-platform/issues/361) | OPEN |
| [VerdifyConsultancy/verdify-platform#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) | OPEN | [VerdifyConsultancy/verdify-platform#361](https://github.com/VerdifyConsultancy/verdify-platform/issues/361) | OPEN |
| [VerdifyConsultancy/verdify-platform#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | OPEN | [VerdifyConsultancy/verdify-platform#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367) | OPEN |
| [VerdifyConsultancy/verdify-platform#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) | CLOSED | [VerdifyConsultancy/verdify-platform#368](https://github.com/VerdifyConsultancy/verdify-platform/issues/368) | OPEN |
| [VerdifyConsultancy/verdify-platform#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) | CLOSED | [VerdifyConsultancy/verdify-platform#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369) | OPEN |
| [VerdifyConsultancy/verdify-platform#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | OPEN | [VerdifyConsultancy/verdify-platform#369](https://github.com/VerdifyConsultancy/verdify-platform/issues/369) | OPEN |
| [VerdifyConsultancy/verdify-platform#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | OPEN | [VerdifyConsultancy/verdify-platform#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370) | OPEN |
| [VerdifyConsultancy/verdify-platform#367](https://github.com/VerdifyConsultancy/verdify-platform/issues/367) | OPEN | [VerdifyConsultancy/verdify-platform#370](https://github.com/VerdifyConsultancy/verdify-platform/issues/370) | OPEN |
| [VerdifyConsultancy/verdify-platform#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) | OPEN | [VerdifyConsultancy/verdify-platform#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) | OPEN |
| [VerdifyConsultancy/verdify-platform#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) | CLOSED | [VerdifyConsultancy/verdify-platform#378](https://github.com/VerdifyConsultancy/verdify-platform/issues/378) | OPEN |
| [VerdifyConsultancy/verdify-platform#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786) | OPEN | [VerdifyConsultancy/verdify-platform#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379) | OPEN |
| [VerdifyConsultancy/verdify-platform#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) | OPEN | [VerdifyConsultancy/verdify-platform#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379) | OPEN |
| [VerdifyConsultancy/verdify-platform#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) | CLOSED | [VerdifyConsultancy/verdify-platform#379](https://github.com/VerdifyConsultancy/verdify-platform/issues/379) | OPEN |
| [VerdifyConsultancy/verdify-platform#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303) | OPEN | [VerdifyConsultancy/verdify-platform#386](https://github.com/VerdifyConsultancy/verdify-platform/issues/386) | OPEN |
| [VerdifyConsultancy/verdify-platform#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) | OPEN | [VerdifyConsultancy/verdify-platform#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | OPEN |
| [VerdifyConsultancy/verdify-platform#303](https://github.com/VerdifyConsultancy/verdify-platform/issues/303) | OPEN | [VerdifyConsultancy/verdify-platform#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | OPEN |
| [VerdifyConsultancy/verdify-platform#563](https://github.com/VerdifyConsultancy/verdify-platform/issues/563) | CLOSED | [VerdifyConsultancy/verdify-platform#394](https://github.com/VerdifyConsultancy/verdify-platform/issues/394) | CLOSED |
| [VerdifyConsultancy/verdify-platform#672](https://github.com/VerdifyConsultancy/verdify-platform/issues/672) | CLOSED | [VerdifyConsultancy/verdify-platform#396](https://github.com/VerdifyConsultancy/verdify-platform/issues/396) | OPEN |
| [VerdifyConsultancy/verdify-platform#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) | CLOSED | [VerdifyConsultancy/verdify-platform#396](https://github.com/VerdifyConsultancy/verdify-platform/issues/396) | OPEN |
| [VerdifyConsultancy/verdify-platform#298](https://github.com/VerdifyConsultancy/verdify-platform/issues/298) | OPEN | [VerdifyConsultancy/verdify-platform#398](https://github.com/VerdifyConsultancy/verdify-platform/issues/398) | OPEN |
| [VerdifyConsultancy/verdify-platform#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) | CLOSED | [VerdifyConsultancy/verdify-platform#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) | OPEN |
| [VerdifyConsultancy/verdify-platform#419](https://github.com/VerdifyConsultancy/verdify-platform/issues/419) | OPEN | [VerdifyConsultancy/verdify-platform#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) | OPEN |
| [VerdifyConsultancy/verdify-platform#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) | CLOSED | [VerdifyConsultancy/verdify-platform#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) | OPEN |
| [VerdifyConsultancy/verdify-platform#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) | CLOSED | [VerdifyConsultancy/verdify-platform#427](https://github.com/VerdifyConsultancy/verdify-platform/issues/427) | CLOSED |
| [VerdifyConsultancy/verdify-platform#433](https://github.com/VerdifyConsultancy/verdify-platform/issues/433) | CLOSED | [VerdifyConsultancy/verdify-platform#428](https://github.com/VerdifyConsultancy/verdify-platform/issues/428) | OPEN |
| [VerdifyConsultancy/verdify-platform#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | OPEN | [VerdifyConsultancy/verdify-platform#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) | OPEN |
| [VerdifyConsultancy/verdify-platform#299](https://github.com/VerdifyConsultancy/verdify-platform/issues/299) | OPEN | [VerdifyConsultancy/verdify-platform#434](https://github.com/VerdifyConsultancy/verdify-platform/issues/434) | OPEN |
| [VerdifyConsultancy/verdify-platform#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) | OPEN | [VerdifyConsultancy/verdify-platform#586](https://github.com/VerdifyConsultancy/verdify-platform/issues/586) | OPEN |
| [VerdifyConsultancy/verdify-platform#638](https://github.com/VerdifyConsultancy/verdify-platform/issues/638) | OPEN | [VerdifyConsultancy/verdify-platform#586](https://github.com/VerdifyConsultancy/verdify-platform/issues/586) | OPEN |
| [VerdifyConsultancy/verdify-platform#390](https://github.com/VerdifyConsultancy/verdify-platform/issues/390) | OPEN | [VerdifyConsultancy/verdify-platform#586](https://github.com/VerdifyConsultancy/verdify-platform/issues/586) | OPEN |
| [VerdifyConsultancy/verdify-platform#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) | OPEN | [VerdifyConsultancy/verdify-platform#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) | OPEN |
| [VerdifyConsultancy/verdify-platform#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) | OPEN | [VerdifyConsultancy/verdify-platform#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) | OPEN |
| [VerdifyConsultancy/verdify-platform#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) | OPEN | [VerdifyConsultancy/verdify-platform#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) | OPEN |
| [VerdifyConsultancy/verdify-platform#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) | OPEN | [VerdifyConsultancy/verdify-platform#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) | OPEN |
| [VerdifyConsultancy/verdify-platform#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) | OPEN | [VerdifyConsultancy/verdify-platform#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) | OPEN |
| [VerdifyConsultancy/verdify-platform#747](https://github.com/VerdifyConsultancy/verdify-platform/issues/747) | CLOSED | [VerdifyConsultancy/verdify-platform#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) | OPEN |
| [VerdifyConsultancy/verdify-platform#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) | OPEN | [VerdifyConsultancy/verdify-platform#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) | OPEN |
| [VerdifyConsultancy/verdify-platform#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) | OPEN | [VerdifyConsultancy/verdify-platform#641](https://github.com/VerdifyConsultancy/verdify-platform/issues/641) | OPEN |
| [VerdifyConsultancy/verdify-platform#588](https://github.com/VerdifyConsultancy/verdify-platform/issues/588) | OPEN | [VerdifyConsultancy/verdify-platform#642](https://github.com/VerdifyConsultancy/verdify-platform/issues/642) | OPEN |
| [VerdifyConsultancy/verdify-platform#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) | CLOSED | [VerdifyConsultancy/verdify-platform#643](https://github.com/VerdifyConsultancy/verdify-platform/issues/643) | OPEN |
| [VerdifyConsultancy/verdify-platform#964](https://github.com/VerdifyConsultancy/verdify-platform/issues/964) | CLOSED | [VerdifyConsultancy/verdify-platform#644](https://github.com/VerdifyConsultancy/verdify-platform/issues/644) | OPEN |
| [VerdifyConsultancy/verdify-platform#670](https://github.com/VerdifyConsultancy/verdify-platform/issues/670) | CLOSED | [VerdifyConsultancy/verdify-platform#672](https://github.com/VerdifyConsultancy/verdify-platform/issues/672) | CLOSED |
| [VerdifyConsultancy/verdify-platform#949](https://github.com/VerdifyConsultancy/verdify-platform/issues/949) | OPEN | [VerdifyConsultancy/verdify-platform#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) | OPEN |
| [VerdifyConsultancy/verdify-platform#778](https://github.com/VerdifyConsultancy/verdify-platform/issues/778) | CLOSED | [VerdifyConsultancy/verdify-platform#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) | OPEN |
| [VerdifyConsultancy/verdify-platform#424](https://github.com/VerdifyConsultancy/verdify-platform/issues/424) | CLOSED | [VerdifyConsultancy/verdify-platform#749](https://github.com/VerdifyConsultancy/verdify-platform/issues/749) | OPEN |
| [VerdifyConsultancy/verdify-platform#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) | CLOSED | [VerdifyConsultancy/verdify-platform#779](https://github.com/VerdifyConsultancy/verdify-platform/issues/779) | CLOSED |
| [VerdifyConsultancy/verdify-platform#781](https://github.com/VerdifyConsultancy/verdify-platform/issues/781) | CLOSED | [VerdifyConsultancy/verdify-platform#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) | OPEN |
| [VerdifyConsultancy/verdify-platform#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) | CLOSED | [VerdifyConsultancy/verdify-platform#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) | OPEN |
| [VerdifyConsultancy/verdify-platform#779](https://github.com/VerdifyConsultancy/verdify-platform/issues/779) | CLOSED | [VerdifyConsultancy/verdify-platform#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) | OPEN |
| [VerdifyConsultancy/verdify-platform#371](https://github.com/VerdifyConsultancy/verdify-platform/issues/371) | CLOSED | [VerdifyConsultancy/verdify-platform#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) | OPEN |
| [VerdifyConsultancy/verdify-platform#782](https://github.com/VerdifyConsultancy/verdify-platform/issues/782) | OPEN | [VerdifyConsultancy/verdify-platform#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) | OPEN |
| [VerdifyConsultancy/verdify-platform#639](https://github.com/VerdifyConsultancy/verdify-platform/issues/639) | OPEN | [VerdifyConsultancy/verdify-platform#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) | OPEN |
| [VerdifyConsultancy/verdify-platform#587](https://github.com/VerdifyConsultancy/verdify-platform/issues/587) | OPEN | [VerdifyConsultancy/verdify-platform#783](https://github.com/VerdifyConsultancy/verdify-platform/issues/783) | OPEN |
| [VerdifyConsultancy/verdify-platform#640](https://github.com/VerdifyConsultancy/verdify-platform/issues/640) | OPEN | [VerdifyConsultancy/verdify-platform#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784) | OPEN |
| [VerdifyConsultancy/verdify-platform#784](https://github.com/VerdifyConsultancy/verdify-platform/issues/784) | OPEN | [VerdifyConsultancy/verdify-platform#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) | OPEN |
| [VerdifyConsultancy/verdify-platform#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) | OPEN | [VerdifyConsultancy/verdify-platform#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786) | OPEN |
| [VerdifyConsultancy/verdify-platform#780](https://github.com/VerdifyConsultancy/verdify-platform/issues/780) | CLOSED | [VerdifyConsultancy/verdify-platform#786](https://github.com/VerdifyConsultancy/verdify-platform/issues/786) | OPEN |
| [VerdifyConsultancy/verdify-platform#785](https://github.com/VerdifyConsultancy/verdify-platform/issues/785) | OPEN | [VerdifyConsultancy/verdify-platform#787](https://github.com/VerdifyConsultancy/verdify-platform/issues/787) | OPEN |
| [VerdifyConsultancy/verdify-platform#781](https://github.com/VerdifyConsultancy/verdify-platform/issues/781) | CLOSED | [VerdifyConsultancy/verdify-platform#787](https://github.com/VerdifyConsultancy/verdify-platform/issues/787) | OPEN |
| [VerdifyConsultancy/verdify-platform#410](https://github.com/VerdifyConsultancy/verdify-platform/issues/410) | OPEN | [VerdifyConsultancy/verdify-platform#787](https://github.com/VerdifyConsultancy/verdify-platform/issues/787) | OPEN |
| [jvallery/agents#4461](https://github.com/jvallery/agents/issues/4461) | OPEN | [VerdifyConsultancy/verdify-platform#801](https://github.com/VerdifyConsultancy/verdify-platform/issues/801) | OPEN |
| [jvallery/agents#4451](https://github.com/jvallery/agents/issues/4451) | OPEN | [VerdifyConsultancy/verdify-platform#802](https://github.com/VerdifyConsultancy/verdify-platform/issues/802) | OPEN |
| [jvallery/agents#4459](https://github.com/jvallery/agents/issues/4459) | CLOSED | [VerdifyConsultancy/verdify-platform#802](https://github.com/VerdifyConsultancy/verdify-platform/issues/802) | OPEN |
| [jvallery/agents#4460](https://github.com/jvallery/agents/issues/4460) | OPEN | [jvallery/agents#4461](https://github.com/jvallery/agents/issues/4461) | OPEN |

The current build-capacity/registry and real restored-target requirements are operational owner interfaces, listed in [the status review](README.md) and [related owner evidence](related-owner-summary.json). An entire owner umbrella is not silently made a new blocking node. Native connections were checked for pagination/count equality and cycles; zero missing source edges or parent mismatches.
