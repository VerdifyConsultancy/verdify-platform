# Public ingress incident #988 — October 18 MDT / October 19 UTC

**Recovered, verified and accepted. #988 closes on its four original unchanged clauses.** Both repaired API replicas are source-bound; actual public API/MCP authentication and denial behavior passed through Cloudflare. After the independent provider startup, both real public camera images and successive natural/exact-backend recovery windows passed. Original failures and historical limits remain retained.

## Cause and retained failures

The exact tier-1 backend is `verdify-prod-verdify-tier1-forward-f72f492d4cf39c2efd7f@kubernetescrd`. Its suffix equals the first 20 hexadecimal characters of the active rule's SHA256. Existing Loki access logs for October 18 20:50–21:00 UTC identify both public camera URLs returning 503 from the tier-2 API router after approximately four seconds; API Pod logs independently confirm the failures. October 19 04:40–04:48 logs show the same recurring paths. These are application camera errors, not ForwardAuth denials.

Both old API processes, source `56b316f3fda666b25665dfc0f791d6be6db0fa46`, had an empty go2rtc base and the obsolete `http://frigate.frigate.svc.cluster.local:5000` fallback. The supported provider is the existing authenticated HTTPS Service 8971 → target 8973, with TLS identity `cameras.vallery.net`. Its credential permits only the two fixed `/api/vision/greenhouse_{1,2}/latest.jpg` source-frame routes, with no query parameters. The original 503s precede the later Iris model rollout; this evidence does not attribute them to that rollout.

The original incident `a527cc469654b07dcf09c29648d3d27762ac71499985a0838a1b239712556a36` and recurrence `36cb643c8716d445d9fbc43ff499990cc82084015e92e4a36a8684ba03572c39` remain retained. Original observations are preserved here rather than replaced by newer green measurements:

| Original observation | UTC October 18 | Value and limit |
|---|---|---|
| Source event began / resolved | 20:52:52.778 / 20:59:52.858340 | Source-resolved event only |
| Exact-backend five-minute 5xx ratio | 21:13:26.576 | 6.580278392% |
| Parent's five-minute total increase | 21:19:46.389 | 279.609934, extrapolated Prometheus increase, not an integer raw request count |
| Parent's later five-minute 5xx increase | 21:19:46.713 | 0 in a separately timestamped window; not durable recovery |
| Anonymous public MCP observation | 21:15:35 | 403; historical cause remains unresolved |
| Historical metrics proxy attempt | Before first implementation-task query | Kubernetes Service proxy 502; separate observer-path failure |

The fresh exact-backend range query for 20:40–22:20 retains 401 samples per status code. 503 rates remain positive throughout that range, while 502 is zero. Rate values are extrapolated per-second rates, not raw counts. The current pre-repair five-minute sample also retains an extrapolated GET503 increase of 10.507, independently of the original source-resolved event. The query/result hashes and observation times are retained with the evidence.

At the later API rollout preflight, Sentinel's provider had independently been scaled to zero for its own authorized archive cutover. Connection refusal under that condition does not justify a NetworkPolicy or authentication change. After the API repair,24natural tier-2 camera requests in05:14:30–05:27still returned503 while the provider remained stopped, with examples19–38ms instead of the obsolete-path four-second timeout. These failures are retained and do not qualify recovery. The historical natural vision recovery under #951 remains a point-in-time result; it does not certify continuous camera availability. This repair did not restart or modify Sentinel.

## Actual route and authentication chain

The observed route is Cloudflare → existing tunnel → HTTPS `192.168.7.10` → shared Traefik → Verdify tier-1 forwarding service → tier-2 Traefik → API8080 / MCP8000. Source and runtime inputs agree:

- Verdify route and controller specifications equal repaired source `617ab6dbd886ccc66905674f07265e22c155afff`; no ingress/controller change was needed.
- The shared controller arguments declare no entrypoint middleware or file provider. The tier-1 route attaches no middleware. Tier-2 attaches the existing identity-header stripping middleware; stripping those seven headers is not ForwardAuth.
- The network owner's deployed source is `0bf1f1ef74e0e3a45ce81fcc0ff1875d1dda05a8`. The live tunnel ConfigMap data and shared-controller Helm values match that pinned source. The running connector's mounted configuration independently matches API rule3 / MCP rule9, both forwarding to HTTPS `192.168.7.10`.
- API uses its existing API-key guard. MCP uses its existing bearer/audience enforcement. No external ForwardAuth call is introduced or inferred.

This is the authoritative observed path for these two hosts. It is not a full Cloudflare account-wide WAF/Access inventory: the old inventory-token file is unavailable, and no replacement identity or security grant was created. Actual selected public requests still provide independent edge/authentication proof. No unsupported absence-of-all-global-policy claim is made.

The Mac's system resolver sends both hostnames to the LAN VIP. Therefore hostname-only smoke tests were classified as LAN evidence. The public harness instead used globally routable public DNS answers, connected to `104.26.8.72` with the original hostname/SNI and normal certificate verification, and required `server: cloudflare` plus a CF-Ray receipt. There were no redirects or automatic send retries.

| Public acceptance | Actual result |
|---|---|
| API existing-key boundary | Missing and wrong key403; existing key400 with exact `Invalid circuit or action` response |
| API write safety | Deliberately invalid circuit/action rejected before DB/device access; no valid command was sent |
| MCP identity | Wrong bearer401; existing Iris initialize200, negotiated protocol2025-03-26 |
| MCP ordinary behavior | 23-tool inventory and real `climate` read200 |
| MCP audience boundary | `outcome_kpi` absent from ordinary inventory and direct call denied with `isError`/unauthorized |
| Public deployed-source readiness | `/health/detailed`200 through Cloudflare at05:21:29, source617ab6db and DB reachable |
| Post-rollout API guard | Repeated after both new replicas were Ready at05:17:30–31;403/403/400 through Cloudflare |

The initial harness mishandled an empty notification response after successful API/initialize checks. Those receipts were retained; a corrected continuation completed only the remaining stateless MCP read/denial cases. No model generation, experiment, action tool, synthetic DB row, token value or greenhouse response body is published.

## Narrow source, build and runtime delivery

Source commit `617ab6dbd886ccc66905674f07265e22c155afff` changes the API camera reader, API credential mount and focused regression tests. The reader connects via cluster DNS while verifying the provider TLS identity, mounts the existing credential read-only, allows exactly two camera routes, follows no redirects, and validates bounded JPEG status/MIME/size/SOI/EOI. There is no unauthenticated admin fallback, cached detect-frame fallback or caller-credential forwarding. Provider failures remain generic503. Legacy `h` remains validated120–1080 for URL compatibility; the provider intentionally returns fixed1080px source frames.

44 focused camera/auth-boundary tests and lint passed. Full source CI `verdify-platform-ci-wknl4` / `de9aaca6-8b81-4b53-b4c3-9e325adf3d68` completed Succeeded at05:11:36. Actual terminal custody covers all three Pods: full gate, exactly one API Kaniko build, and candidate publisher. Every declared container exited0 with zero restarts. The trusted classifier selected only API; all seven other image flags were false. Image output source/tree/Dockerfile/digest provenance was checked before promotion.

| Delivery coordinate | Value |
|---|---|
| API image | `registry.vallery.net/verdifyconsultancy/verdify-api@sha256:701b4dfe22bde2d80d39f5dbb5e18efdf268484634b886f7220d1dbe496c38a4` |
| Managed candidate commit | `9923036f2b008dd0ccc1a3a98742ffdafb7c1589` |
| API-only release commit | `7fc3956df475503f8ac52ae7d7e63a02484e1401` |
| Attended Argo operation | Started05:13:20, Succeeded05:13:25; exact revision above; one `apps/Deployment verdify-prod/verdify-api` result |
| Actual runtime | Both new Pods Ready; source617ab6db and `/app/main.py` SHA256 `51aecd9dd402f8a5d50556f543b077be762dc025c388dab36eb837214376b838` match |
| Preservation proof | Render129objects, only API Deployment differs; zero Secrets; other128objects and winter pins equal; other25long-lived workload specs/UIDs equal |
| Release CI | `verdify-platform-ci-zdmzd` / `8a7386be-09d0-4757-a6b4-81307d18d831`; Succeeded05:25:40, one validate Pod, zero builds, all declared containers exit0/zero restarts |

**No whole-application sync occurred.** The single-resource selector was verified immediately after submission and again in the terminal actual operation. No hook, migration, schema change, prune, other workload restart, firmware/control change, new credential/RBAC/security setting or provider mutation occurred. Argo apply success was followed by an independent two-replica rollout/source readback; it is not used as a substitute for public camera behavior.

Shared CI was coordinated with the Agents owner. The only build held the existing generation178 window until its actual publisher completed. The subsequent179/316 two-ConfigMap hold concerns shared inventory/policy resources, not this independently owned API Deployment. This lane made no shared inventory/controller/policy mutation and does not claim179certifies the new API digest. The build used normal bounded Kaniko resources and fresh cluster/quota/pressure admission. No heavy suite or image build ran on the Mac.

Practical rollback coordinates are the previous desired revision `970299ae92f0e17af76f35bc2efee7b924e24f7b`, applied with the same single API Deployment selector. Its API pin is `sha256:67c0705274d699d21f257e4787f2a8f7c1fcc33d152aa567a4de534e45d9dedf`. Winter Job/CronJob pin `75f510f5…` remains frozen and unrelated. A rollback would restore the known camera-source defect; no rollback was submitted merely because the independently maintained provider is temporarily unavailable.

## Actual recovery acceptance and dependency plan

The first05:39–05:49 two-window attempt was **not accepted**: camera2 returned503 at05:45:12/05:46:12, with2.1090965extrapolated503increase in the second window. Provider audio/RTSP interruption is temporally correlated, but the exact failed upstream status is unavailable. Both API Pods subsequently fetched both valid JPEGs at05:50. The failed attempt,20natural records and provider diagnostic limits remain in failed-recovery-observation.json; later stable windows are a separate qualification, not a rewrite of that failure.

Sentinel source27d503a restored its provider independently: Deployment generation189 became Ready at05:38:00. This lane submitted no provider mutation. At05:38:19/20, actual public Cloudflare requests returned200/cache MISS for both camera URLs. Native decoding confirms1920×1080JPEGs,124823/205205bytes, in1605/1725ms. The actual edge advertises max-age14400; distinct query URLs qualify fresh capture, as the existing consumer does. No bare-URL freshness guarantee or cache-policy repair is inferred. Provider aggregate health was Degraded at the first Ready capture; this report accepts actual scoped camera behavior, not Sentinel's whole cutover program.

The two nonoverlapping05:47–05:52 and05:52–05:57 exact-backend windows contain real traffic and zero extrapolated5xx. Natural successful requests for both cameras, excluding the explicit acceptance marker, independently establish recovery under ordinary traffic. recovery-health.json retains exact query/timestamps, per-window values, original result hashes and natural-log provenance. These are extrapolated counter increases, not integer raw request counts. This finite observation supports recovered recurring camera503behavior, not perpetual availability. Earlier failures, the original incident and later recurrence remain intact. The historical anonymous MCP403 cause remains explicitly unresolved; current expected public authentication and denial cases pass.

All four original acceptance clauses remain verbatim. The observed active chain and stated global-inventory limit, existing-identity authenticated public behavior, two-window/natural recurrence proof, and supported camera-cause/unresolved-historical403 conclusion are recorded separately in acceptance.json. No closure is inferred from a build, source-resolved event or single zero-error sample.

The plan retains94 canonical records, 47 open issues, 143 open acceptance clauses, 26 active local edges and 25 closed predecessor records. Home Assistant#878 completed at04:27:44 under PR#894/ff250799, so its native #981 link is retained as closed history; #981 becomes a ready bounded consumer-adoption packet, with its own original acceptance still open. #988 remains standalone with no parent or completion dependency, and is removed only from open execution allocation after actual acceptance. Temporary provider readiness is an execution condition, not an invented edge requiring completion of Sentinel's whole cutover issue. Original sprint estimates and all unrelated scientific/physical criteria remain unchanged. #75 stays open on its remaining coverage contract.

## Custody

Source and release are pushed. Curated report evidence and final native/remote custody are published with their actual verification results. Original full private diagnostic inputs remain under `/private/tmp/verdify-988-fix-20261018`; hashes preserve custody without exporting private JPEGs, credentials, greenhouse response bodies, raw unrelated inventory or connector credentials. Public proof retains status, hashes, timestamps, dimensions and source/CI/operation identities. Final CI receipts must come from terminal Workflows and all actual declared container states, not log tails or commit-status assumptions.


Final public readback after the retained interruption: both cameras again returned200/cache MISS and native1920×1080JPEGs. Exact per-camera timestamps, lengths, durations and body hashes are in public-camera-post-recurrence.json; actual image bytes remain private. Fresh05:54 public API key403/403/400 and MCP23-tool/real-climate/admin-denial checks also passed, retained separately in public-final-api.json and public-final-mcp.json. No command or model generation was sent.
