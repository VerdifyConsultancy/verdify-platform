# Verdify campaign #775 — shutdown handoff, October 2, 2026

Status: **paused at Jason's request; full C0–C8 and genuine 30-pair/60-local-day pilot unfinished.**

## Durable recovery coordinates

- Main at shutdown preparation: `a6f190f7594df2f7903d7a09a2752b7126d4feb7` (PR948 merged).
- Handoff branch: `handoff/775-paused-20261002`.
- [Branch custody](branches.json): every recorded local branch and detached worktree head maps to an independently verified GitHub ref. Backup refs use `handoff/shutdown-20261002/…`; no existing remote branch was force-updated.
- [Worktree inventory](worktrees.json): 394 registered, 387 existing at initial scan. Seven missing/prunable entries were recorded, not pruned. Original dirty worktrees and indices were preserved.
- [Uncommitted source snapshots](uncommitted-source-snapshots.json): separate WIP commits preserve changes without altering their original worktrees. These are unreviewed custody, not merge/deployment candidates. Cache/environment symlinks stay separate.
- [Issue index](issues.json): 85 issues updated during the sprint at initial shutdown scan; open and closed states retained. No status was inferred from a test count alone.
- Private evidence: encrypted assets on draft GitHub release `sprint-775-shutdown-20261002`. Encryption uses the existing Jason age recipient; decrypt with the existing local SOPS age identity, never publish its private key. Asset custody manifest records hashes and remote verification. Includes a verified Git bundle, original local receipts, firmware rollback artifacts, private operator scripts and dirty-file custody. No plaintext credential-bearing corpus is committed.
- Local continuation journal: `/Users/jason/Documents/Codex/verdify-release-20260930/continuation.md`.

## Completed and verified

### Production and controller recovery

- Controller recovered remotely; USB recovery is unnecessary. Current accepted firmware is `2026.10.2.0637.620a218a-wifi-bound`, built from `620a218a93cf24ca1e1b49dfed617cc97de7d258`.
- OTA SHA256 `0f84ea9a06a8c96928d3675361515aa8fa1ab39528d256353babb59717bd9176`; ELF SHA256 `39a237cfb044e72633d52646c203d27b1217ba9f9de3d4a8308798b1b7a96b5b`.
- Sensor health: 23 PASS / 0 FAIL / 4 recorded WARN; expected-version alert resolved causally. Calendar bake wait waived by Jason; no invented 48-hour elapsed claim.
- Selected authoritative local rollback floor: `/Volumes/Work/Codex/verdify-c1-final-periodic-operational-source-20261002/firmware/artifacts`. Exact previous1315 triplet preserved under `floor-before-accepted620`; other dated copies and native archive were not overwritten. Use this exact accepted floor on resumption. July rebuild remains a separate provisional artifact, never the unavailable accepted July binary.
- PR945 release commit `5435537098c3c6c96dbeb7c2cf2ab0ba8e7b4c30` delivered through actual full126-resource no-prune/no-selector hook sync. First false-green Argo result with old images/hooks remains failed evidence. PR946 documents atomic prior operation-state clearing and actual hooks/images verification.
- API/MCP/ingestor source `e91b2a7d5df14baf327bd198b4333ac65e28cc6b`; exact running pins below. Smoke9/9, authenticated MCP23tools on two pods, one ESP32 connection, original300-second ingestor watch0schema errors/0restarts.
- Latest live check: Argo Synced + Healthy at `229e338633786deca26628968ebeba8d8795c7df`; API2/MCP2/ingestor1 Ready, same accepted images. Main source-only changes after that check do not imply a new product deployment.

| Image | Accepted digest |
| --- | --- |
| api | `sha256:7387c2c0e5b22caa0679c02668240e3b639e2e6b3797dc9fe2c43ebf30655ab9` |
| mcp | `sha256:64a0c79a721878c3998d0fd599dc5c4b64c028d595803d46df7816cf73cba74b` |
| ingestor | `sha256:4c5fa5d67c256fd01b4f6519183c20ff4cf6023326ae4100c4b8daff9468b664` |
| migrate | `sha256:a5063e1e6a4e885ac67d48a6bf27c88844705240edcaa0c251e1b1def4039aeb` |
| experiment-v2-orchestrator | `sha256:985be9a9b427287767d66817a1d2e5106314186126803eda8c65eb713a5e2a5d` |

### C1 current controller qualification and ordinary handoff

- Original worksheet `4eb0e875-d9d8-4081-b35b-25fa2f3e2475`, capture `4fbf0d07-35b4-4ed3-9e1d-f13511b8935a`, original expiry15:16:55.565384Z never extended.
- Genuine full48 +24anchor epochs `95ab4731-60f6-4bf7-a401-c5253841b456` and `2d3fb5aa-7643-4332-a4cd-ec6d3711ee4e`, 30.02004s apart. All48 timestamps advanced; both ToolA qualified, all six independent served/control/observed wire series exact. ROOT review SHA69a6c071990327a2d35f4870fde38d0055ac75fecdad16874a2c2416f1a724a7.
- Original ordinary west SEND crossed expiry and failed; originalHALTED and incomplete epoch preserved. Source naturally recovered through fresh ordinary policy, without renewal/replay; original3491 YIELDED and all14 restoration rows causalconfirmed/nonexpired, full48 converged (proof367efa6be5699451ffbe5fadd875a6dc20f165f830ccceae17073ccb337f6b20).
- Subsequent real Iris plan8a3 delivered18canonical +2dynamic-zone changes in native12+8 stages. All20 original actuator rows confirmed; currentfull48 converged at15:39:55Z (proof60555f0a634e5b7b276e404f894582f8f410678642a029c41a178af0b1364879).
- First cleanup refused before writes when that fresh policy introduced18differences. After genuine convergence, original92564 archived exactexpired pointer b9bbf2d5e1c3cbe100438b56d6c5f40d8f133134e178270fb7a957785252776f mode0600 and removed it; nativeYIELDED state unchanged. Cleanup completed15:44:52.668163Z; prior failures/history retained.
- #424 remains open: same-semantic wire agreement is proven only for these two captures. IdealSQL/device-wire difference remains present (maximum0.01677°F /0.003388kPa); no tolerance was added. Original September4 discrepancy and independent agronomic target/contributor proof remain separate. Exact additional disposition artifactSHA62429d119584618535bbf0e8b55a13d004cdcaeab4c3abf0e8f2ac9a7284cf36 retained in private receipts.

## Immediate unfinished critical path: #396 full database recovery

- Real CNPG backup/495MBbase/WAL and distinct A/B PITR markers/timeline5 are proven; SOURCE/A/B full catalog/roles/278ledger/original seals and custody preserved. This does not prove full product recovery.
- SOURCE cluster `verdify-cnpg-rehearsal` UIDe11f1014-a77e-4ccf-9d97-e8cf5c037484; primary `verdify-cnpg-rehearsal-2` UID5129dbaf-9aaf-4f47-943e-3fb063fd6afb; container2d09e2fcc070ca981853ae89f9546a5c1ae173f1f5d457955f348c01d983beb6; SystemID7691706769041801249/TLI4. Revalidate live identity before resumed work.
- Original full collector38263, source-bound PR947 merge87cd756e, failed SOURCE native120-second timeout on `v_policy_twin_asof_input`; A/B unattempted. Exact backend36178 settled absent15:44:58Z. SQL/errors/empty stdout are retained; no parity/admission credit.
- Candidate cumulative-window fix preserves186view inventory,103CTEs,seven timestamp endpoints, nativeconflicting-duplicate fallback and exact inclusive24h semantics. 44 focused checks passed; private20k branch1130ms→38ms with bidirectional EXCEPT ALL equality. Initial private benchmark's wrong NestedLoop plan is retained separately.
- Original actual policy-only proof39638 (sqlSHA97456bc6f5757172af6c26f5304dd3a35590f2bf1ae525ed606a9548e8b95e05) also failed native120-second statement timeout, exit3, outertimeoutfalse,120.554741s. Fresh exact app backend scan returnedempty. Candidate is WIP, **not a proven actual performance fix**, never promoted. No fullcollector repeated after this result.
- Next: inspect retained actual plan to locate remaining full policy bottleneck; fix owning source with semantics preserved; qualify actual complete policy count+seven times, required CI/source merge, then one full SOURCE/A/B comparison. Do not simply repeat the failed query, raise timeout blindly or remove relation requirements.
- After actual full parity: existing exact physical input consumer → native rollback qualification → current admission → target-only installation of existing app credentials → six real A/B API/ingestor/MCP TCP authentications/pools/hot queries. Credential operator is prepared but **never executed**. No production DB cutover is authorized by this handoff.

## Scientific and restored experiment qualification

- PR948 merged `a6f190f7594df2f7903d7a09a2752b7126d4feb7` after exact requiredCI/nativefm9kcSucceeded. It freezes stated0.975 confidence in a predeclared contract and truthfully marks physical missing-outcome bounds unavailable; empirical jointpower/covariance remainNULL. This is not design lock, draw or #782/#783 closure.
- #782 lane: branch `codex/warm-authentic-pretrial-replay-20261002`, head492b409c1136a43b495bd5c82fbe3e15b19c3073. See `docs/research/warm-pretrial-replay-handoff-2026-10-02.md` on that branch and issuecomment5956255448. Actual79domain hashes/31forecastvintages authenticate, but0targets/0contributors/no originalproviderchoice/currentclock-not-pre06. Private numericalgoldens passed2 on Python3.14.5/NumPy2.5.3/SciPy1.18.1; sampled productionAPI lacked NumPy/SciPy, so no deployed analyzer environment claim. No caller-clock bypass or fake selector replay.
- #783 lane: branch `work/783-vertical-preparation-20261002`, headf040c67254f25084b9d1281900957b1a1ab04094. See issuecomment5956282639 and its source handoff. 52 offline checks passed; retained7547byte export analyzed twice identically with current analyzer, tamper refused. Existing connected fixture uses synthetic owner clock/fixedexperiment UUID/FakeTransport; do not apply it to physical SOURCE/A/B and call it restored whole-path proof. Real selector→setter schema→receipt→freezer→unreshaped analyzer twice and current role boundaries remain incomplete.
- Warm candidate calendar June1–July30,2027, America/Denver,30pairs/60localdays,06:00–24:00 (18h). Genuine outcome days absent. Preserve every assignment/failed/fallback/null/zero/reset day; no fake clock shift, redraw or synthetic outcome credit.
- November2–December31 winter feasibility is separately labeled; not hot/dry pilot outcome evidence.

## Remaining full-scope requirements

- #778 September4 incident disposition, #371 independent physical target/contributor acceptance, #424 historical/served lineage and #749/#641 broader physical readiness/proof remain open; passing C1 does not automatically close them.
- #419 real corpus still lacks HUMIDIFY branch coverage; no synthetic branch credit.
- #386 actual OFF outside solar window observed; eligible transition/minimum-on/DLI/efficiency acceptance incomplete. Never infer lighting efficiency without measurement or force lights solely to create proof.
- #297 soil inputs remain uncommissioned/null position/calibration;80/80/75 are seeds, not measured thresholds. Staged irrigation stop-safety source is snapshotted; physical delivery/effectiveness remains incomplete.
- #382 durable spool work delivered/qualified portions preserved; issue remains open pending its full acceptance. #322/#394/#214/#427/#433/#781/#779/#174 stay closed as observed; do not reopen already completed proofs.
- C2 prospective packet not applied. #784 operation and #785 reproducible revealed readout await qualified genuine pilot; #786 comparator preparation mergedPR932 but conclusion depends on #785 outcomes.
- #14/#638/#586 future device-denied programs remain explicitly deferred/open; #16 hardware program remains Jason-deferred/open. Preserve full campaign scope without manufacturing completion.

## Process shutdown and safe resume

- No new promotion, Argo sync, OTA, draw, target-credential installation, restore or device action was initiated for shutdown.
- Original policy probe39638 terminal failure and exact backendabsence verified. Child CI62966, tests26982, venv93135/51362 and vertical verification handles terminal. All child lanes stopped. Final process inventory accompanies custody manifest.
- Production and rehearsal clusters remain running. Stopping operator work does not shut down greenhouse control or delete recovery data.
- Use `/Users/jason/.kube/config`, context `vallery`. Repo Python `/Users/jason/repos/verdify-platform/.venv/bin/python`; Bash `/opt/homebrew/bin/bash`; actual ESPHome2026.6.5 environment `/Volumes/Work/Codex/verdify-firmware-recovery-20260929/venv/bin/python`. Export `VERDIFY_DB_BACKEND=kube` for whole firmware operations, never print OTA password.
- Revalidate branch/main/live identities and fresh source-owned desired policy on resume. Never replay expired worksheets, approvals, failed setters or old snapshots. Preserve sole Recreate ingestor and no-prune/no-selector full sync semantics.

### Paste-ready resume objective

```text
/goal Resume Verdify campaign #775 C0–C8 through actual production/recovery and genuine blinded30pair/60localday hot/dry pilot. Read docs/handoffs/775-2026-10-02/README.md on handoff/775-paused-20261002, its GitHub branch/worktree/issue custody and encrypted sprint-775-shutdown-20261002 assets. Preserve original objective and Jason's calendar-bake waiver. Controller620 recovery/C1full48/restoration completed; do not repeat them. Revalidate live e91 digests/solewriter and fresh policy. Immediate blocker: full policy count/time query still native120s timeout even after cumulative candidate; original39638/38263 terminal and backendabsent. Diagnose/fix semantics-preserving owning query, then genuine SOURCE/A/B full parity, rollback/admission, target-only existing credentials and six actual app authentications. Continue782authentic replay and783whole restored selector→setter→receipt→freezer→analyzer twice/currentroles without synthetic credit; winter separately labeled, real warm60days/outcomes absent. Work from current evidence, preserve all failed custody/old floors, use existing enforced CI, no arbitrary gates. No completion claim until all original C0–C8 and true pilot outcomes/readout are proven.
```
