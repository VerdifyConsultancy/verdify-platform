# Saved public Grafana panels (#1002)

Jason approved native public sharing for the 20 existing `site-*` dashboards and subsequently the
existing Greenhouse: Weather dashboard on 2026-10-20 (21 total). Public link identifiers and allowed panel IDs live in
`verdify_public/grafana_catalog.json`; Quartz has a checked identical catalog.
No other dashboards or datasource access are shared.

Grafana 12.4.5's public page ignores `viewPanel`. For existing panel embeds,
Quartz uses `public-dashboards/<token>?panelId=<saved-id>`. Only GET of the exact
public metadata path is routed to the API adapter; it filters that saved public
model to one panel, using the same-origin Referer solely as a presentation hint.
A missing/foreign hint returns the approved complete dashboard. Queries still
POST to Grafana's native saved-panel endpoint. No user SQL, authentication
cookies, Authorization header, custom upstream URL or datasource credential is
forwarded by the adapter. Native full-dashboard links remain available.

PNG URLs retain `/render/d-solo/<uid>/<slug>?panelId=...`. The existing bounded
nginx cache calls the API adapter, which validates the fixed catalog, dimensions,
theme and time syntax and calls the upstream renderer with its existing Secret
key. No new identity is created. Two renders and eight metadata requests per API
process are allowed, with no waiting queue; disconnects retain occupied slots
until the worker really exits. The renderer also enforces its existing maximum
of four browser operations. PNGs are bounded at 4MiB, 1600px dimensions and 50s.
Renderer port 8081 admits API pods only and is never a public Traefik backend.

The file provider routes the cache ClusterIP; its sticky cookie binds that Service
URL. ClientIP cache affinity is per edge pod, so a browser can reach either of the
two pod-local caches. Warmup can be MISS/MISS/HIT. Verify a bounded repeated HIT
matches an observed cached PNG variant; do not assume the first two requests use
the same cache. The replica pair spreads per pod-template-hash across the two
eligible worker domains, excluding NoSchedule-tainted control-plane domains.

Migration 278 sets 15s statement/idle-transaction limits on the existing read-only
Grafana login. Recreate Grafana before claiming the default reaches its pool.
No base-table privileges or other runtime logins change.

Delivery is staged. First deploy the compatibility adapter, publisher and
migration while the existing public paths remain available. Verify saved-panel
interactive rendering and repeated PNG cache HIT behavior through the real
public hostname. Then set `GF_AUTH_ANONYMOUS_ENABLED=false`, restart Grafana via
GitOps, verify native shares still work, and prove unauthenticated `/api/ds/query`
and datasource-resource requests return 401. Verify all 21 metadata links and
at least the Home/Climate panels in the lab, including time selection and PNGs.
Keep the issue open until those live proofs and the private-field/query probes
are recorded. If compatibility fails before the access cutover, restore the prior API and
publisher pins plus route/cache config. Retain the current migrate pin: migration
278 is ledgered and must never be removed, edited or run through an older runner.
A timeout reversal, if needed, is a new forward migration. After cutover prefer
fixing the saved-share path; re-enabling anonymous org access reopens arbitrary
SQL access and requires a deliberate incident decision. Never prune the Grafana
claim or revoke existing admin SSO.
