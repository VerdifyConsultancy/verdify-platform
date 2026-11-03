# Dashboard runbook (dashboard.verdify.ai)

The dashboard's web tier, `verdify-dashboard-web` (the BFF), runs in namespace `verdify-dashboard` and ships
through its own Argo app `verdify-dashboard`: automated, self-heal, prune off, declared in jvallery/agents. It
never rides the `verdify-prod-dark` sync. The BFF calls the private operator API, `verdify-operator-api` in
`verdify-prod` (ClusterIP :8090, no route), which reads and writes `verdify-db`. Manifests:
`deploy/k8s/dashboard/`. Secret names and keys: `deploy/k8s/SECRETS.md`. Design:
`docs/design/dashboard/architecture.md` §2, §4, §5.7, §10 and §11.

Every command below runs from any kubectl host and prints no secret value, cookie or token. Read Secrets by key
name only (`kubectl describe secret` prints names and sizes); never `get secret -o yaml` or `-o json`. Change the
web only through Git: a `kubectl edit`, `set image` or `scale` on `verdify-dashboard-web` is reverted by self-heal.

## Status

```bash
kubectl -n argocd get application verdify-dashboard \
  -o jsonpath='{.status.sync.status} {.status.health.status} {.status.sync.revision}{"\n"}'
kubectl -n verdify-dashboard get deploy,pods,svc,configmap
kubectl -n verdify-dashboard exec deploy/verdify-dashboard-web -- node -e \
  'fetch("http://127.0.0.1:3000/readyz").then(r => r.text()).then(console.log)'
```

`/readyz` answers `{status, source_revision, operator_api, authentik}`. The last two read `reachable` or
`unreachable` from a background check every 30 s and never gate readiness, so a restart while Authentik is down
keeps existing sessions' reads and Stop. Superseded `verdify-dashboard-web-<hash>` ConfigMaps are expected: the app
has prune off, and their `IgnoreExtraneous` annotation keeps them out of the sync status.

The live check needs no sign-in, prints only check names, HTTP statuses and PASS or FAIL, and exits non-zero on any
FAIL. Its header lists the checks; it mints a viewer session in the web pod and deletes it at the end:

```bash
scripts/dashboard-smoke.sh --expected-web-sha <build commit of the pinned digest>
```

`API-01` prints what `writes_enabled` reads and passes when it equals the pod's `DASHBOARD_WRITES_ENABLED`, so the same
line records the first `true` after the flag merges and the `false` after a rollback.

## Live checks

`scripts/dashboard-smoke.sh` runs from any kubectl host and works inside the web pod through the BFF. It prints only
check names, statuses and PASS, FAIL or HELD, never a cookie, token or body, and it exits non-zero on any FAIL or HELD.
With no flag it runs the check above; a flag runs that one leg instead (`/readyz`, the leg and its session rows), so
nothing below runs unless it is named. All of them take `--expected-web-sha`.

| Run | As | What it writes | Needs | Release |
|---|---|---|---|---|
| `scripts/dashboard-smoke.sh --sweep` | viewer | one session row, deleted | | #1181, #1186 |
| `scripts/dashboard-smoke.sh --agent-write <name> [--param <setting>]` | agent `<name>` | one grid step, released at once; one Job, deleted | key `<name>` in Secret `verdify-mcp-agents` | #1144 |
| `scripts/dashboard-smoke.sh --role operator [--param <setting>]` | `dashboard-smoke` operator | one grid step, manual, released | `writes_enabled: true` | #1144 |
| `scripts/dashboard-smoke.sh --role operator --revert [--param <setting>]` | `dashboard-smoke` operator | three writes within one grid step, manual, released | `writes_enabled: true` | #1186 |
| `scripts/dashboard-smoke.sh --role operator --lights` | `dashboard-smoke` operator | **switches the main light once each way**; `sw_gl_main_auto_mode` 1, released | `writes_enabled: true` | #1186 |

`<setting>` defaults to `mister_engage_kpa`.

**The sweep** reads every served read through the BFF and expects 200, then compares MCP with the Water read. It only
reads, so it can run at any time.

- A failure prints the id and the status. A 500 on a row is usually a relation or function that
  `verdify_dashboard_runtime_login` was never granted, and the fix is a migration.
- `API-IRR-10` reads 2025-08-05 to 2025-09-03, the first 30 days of wet-relay `equipment_state` rows (`irrigation.md`
  §6.2), and each day must carry minutes. An earlier window reads `no_data` for good: 2024-11-17 is the first grow-light
  row, not the first water.
- Parity runs `irrigation_status` inside a `verdify-mcp` pod with the Iris token already in its environment and
  `/api/v1/irrigation` in the web pod, at the same second. Each pod prints one hash per field, and the host compares them
  over `lines` and `program`, ignoring `server_time`, `observed_at` and age fields. The line reads `equal`, or names the
  differing line ids and fields; it reads up to three times, because a run in progress can move between two reads.

**The write legs** write through the same paths as people and agents, so they run only when a release or an operator
asks. Each moves a setting by one grid step at most, and they keep these rules:

- Each refuses to start, having written nothing, unless the setting's newest desired row is absent or released. A
  crop-band setting is refused because a release leaves `crop_band_anchors` as the write set it, and an irrigation-site
  setting because it cannot confirm before the engine firmware. `--lights` also needs the main light's intent and Lutron
  state known and equal, and `sw_gl_main_auto_mode` reading 1.
- `--role operator`, `--revert` and `--lights` need `writes_enabled: true`, because the BFF answers 503 `writes_disabled`
  otherwise. `--agent-write` does not: the flag gates only the BFF.
- Before it releases, a leg re-reads the newest desired row. Another writer's row stays, and a FAIL says so.
- `FAIL` after 5 minutes means the setting did not read `confirmed`; the leg still released it. `HELD` means an armed
  experiment serves that field, so repeat the leg after disarm or with another `--param`.
- `--lights` runs Back to automatic even after a failed readback, so it ends with the light at the controller's intent,
  unless a person wrote `sw_gl_main_auto_mode` meanwhile; then it touches neither again. Its first call goes through `allow-setpoint-from-operator-api`, so when a sync adds that policy, hold the edge-write
  lock through this leg (#1186).
- `--agent-write` without the key fails with that message and creates nothing. Jason seals the key in jvallery/agents
  (jvallery/agents#6017), and MCP admits it within 3 minutes. The host reads the Secret's key names only, with
  `kubectl describe secret`.

**If a write leg did not finish** (the run prints "did not finish", or its host died): the program in the pod keeps
running on its own deadlines and releases what it wrote, within 25 minutes at most, unless the web pod itself restarted.
After that, read the setting on Settings or in API-09. If its newest desired row is still `dashboard-smoke`'s, use
Release to auto. For `--lights`, Back to automatic on `/equipment` returns the light to the controller's intent; then
Release to auto on `sw_gl_main_auto_mode`. An `--agent-write` Job releases on SIGTERM, and a Job left behind deletes
itself 10 minutes after it ends:

```bash
kubectl -n verdify-prod get jobs -l app.kubernetes.io/component=dashboard-smoke
```

## Sign-in failures

A refused sign-in is SCR-09's card at the URL that refused it, with the refusal's status and the request id, which
is the `request_id` of the web pod's log line for that request.

| What people see | Meaning | Look at |
|---|---|---|
| The "Sign-in is unavailable" card (503 at `/auth/login`) | The BFF cannot fetch the issuer's discovery document | `authentik` in `/readyz`; the discovery fetch below |
| The same card as a 503 at `/auth/callback` | Authentik's token endpoint did not answer (or answered 5xx), or the operator API refused or missed INT-01, for example a mismatched `operator-api-token` | `authentik` and `operator_api` in `/readyz`; the operator API's INT-01 line with the callback's `request_id` |
| The "Sign-in didn't complete" card (400 at `/auth/callback`) | State mismatch, a login cookie older than 10 minutes, an Authentik error or a failed code exchange | The web pod's `/auth/callback` log lines; the redirect URI and the client secret |
| An Authentik error page before the dashboard comes back | Authentik refused the authorize request, usually a redirect URI it does not list | The provider's redirect URIs |
| The "No dashboard access" card (403 at `/auth/callback`), or Authentik's own permission page | The account is in neither dashboard group | The person's groups |

The discovery URL fetched from the web pod must print 200 (this is also R1's live check):

```bash
kubectl -n verdify-dashboard exec deploy/verdify-dashboard-web -- node -e \
  'fetch("https://auth.vallery.net/application/o/verdify-dashboard/.well-known/openid-configuration")
     .then(r => console.log(r.status), e => { console.log(e.cause?.code ?? e.name); process.exitCode = 1 })'
```

- A 404 means Authentik has no provider or application with the slug `verdify-dashboard` (jvallery/agents
  identity lane). A timeout or `ENOTFOUND` is the network path: `auth.vallery.net` resolves to traefik-apps
  (192.168.7.10), which lies outside the management set that `egress-deny-management` refuses, and DNS goes through
  kube-dns. Check `kubectl -n verdify-dashboard get networkpolicy` against `deploy/k8s/dashboard/`.
- Redirect URI: Authentik matches it strictly (estate rule L8). The provider must list exactly
  `https://dashboard.verdify.ai/auth/callback`, and `https://dashboard.verdify.ai/` as the post-logout redirect.
  The BFF builds both from `PUBLIC_ORIGIN` in the generated ConfigMap, which must be exactly
  `https://dashboard.verdify.ai`.
- Client secret: a secret regenerated in Authentik but not re-sealed fails every code exchange. Confirm the key is
  present with `kubectl -n verdify-dashboard describe secret verdify-dashboard-web`; Jason re-seals it in the
  jvallery/agents KSOPS lane, and once that has synced, delete the web pod
  (`kubectl -n verdify-dashboard delete pod -l app.kubernetes.io/name=verdify-dashboard-web`) so it restarts with
  the new value. A new `cookie-secret` voids only sign-ins in progress; sessions survive.
- No access: membership of `verdify-dashboard-operators` (operator) or `verdify-dashboard-viewers` (viewer) in
  Authentik decides the role. A group change applies at the person's next sign-in, at most 12 hours later.
  Adding, changing and removing people: [access runbook](dashboard-access.md).
- Request logs are one JSON line per request with the route template and status, never cookies, tokens, bodies or
  query strings: `kubectl -n verdify-dashboard logs deploy/verdify-dashboard-web --since=15m | grep '/auth/'`.

## Writes-flag rollback

`DASHBOARD_WRITES_ENABLED` turns every dashboard write off without a release (architecture §5.7). It gates only the
BFF: MCP and Iris writes are unaffected.

1. Merge a one-line PR that sets `DASHBOARD_WRITES_ENABLED=false` in the `configMapGenerator` of
   `deploy/k8s/dashboard/kustomization.yaml`. No image pin and no `verdify-prod-dark` sync are involved.
2. `verdify-dashboard` auto-syncs at its next refresh. The ConfigMap gets a new hash-suffixed name, so the
   Deployment's `envFrom` changes and a new pod rolls out (`maxSurge` 1, `maxUnavailable` 0).
3. Within 5 minutes of the merge, `/api/v1/me` reads `writes_enabled: false`: a signed-in operator sees the banner
   "Changes are switched off; reading still works", and `scripts/dashboard-smoke.sh` reads it as its
   viewer session. The pod's own value:

   ```bash
   kubectl -n verdify-dashboard rollout status deploy/verdify-dashboard-web
   kubectl -n verdify-dashboard exec deploy/verdify-dashboard-web -- printenv DASHBOARD_WRITES_ENABLED
   ```

4. Reads and irrigation Stop keep working. Every other non-GET `/api/*` request gets 503 `writes_disabled`.
5. The previous ConfigMap stays in the namespace, unreferenced. Its `argocd.argoproj.io/compare-options:
   IgnoreExtraneous` annotation keeps it from holding `verdify-dashboard` OutOfSync, so leave it.

Turning writes back on is the same one-line PR with `true`.

## Operator API down

Every page shows "Dashboard API unavailable" after two failed polls and nothing green; `operator_api` in the BFF's
`/readyz` reads `unreachable`, and proxied calls answer 503 `unavailable`. The controller keeps running on its own.
The dashboard cannot read or stop water while this lasts; a named agent's MCP `irrigation_stop` is the other path.

```bash
kubectl -n verdify-prod get deploy verdify-operator-api
kubectl -n verdify-prod get pods -l app.kubernetes.io/component=operator-api
kubectl -n verdify-prod logs deploy/verdify-operator-api --since=15m
kubectl -n verdify-prod exec deploy/verdify-operator-api -- python -c \
  'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8090/readyz", timeout=5).read().decode())'
kubectl -n verdify-dashboard exec deploy/verdify-dashboard-web -- node -e \
  'fetch(process.env.OPERATOR_API_URL + "/healthz").then(r => console.log(r.status), e => console.log(e.cause?.code ?? e.name))'
```

- Its `/readyz` answers `{status, source_revision}` once its pool runs `SELECT 1` as
  `verdify_dashboard_runtime_login`, which attests itself on connect. Not ready points at `verdify-db`, the
  `allow-db-from-app` policy (it admits `operator-api`) or Secret `verdify-dashboard-runtime-db`. The pod logs one
  `{"event":"not_ready","error":...}` line each time the cause changes: `AttestationError` means the login connected
  but `verdify_dashboard_runtime.fn_attest_login()` was not true (its role settings, membership or grants drifted).
  `InvalidPasswordError` covers three causes, because `verdify-db` checks remote logins with `scram-sha-256` and
  answers them alike: the login does not exist yet (migration 282 has not run), it has no password yet (the role
  bootstrap has not set one from Secret `verdify-dashboard-runtime-db`), or the Secret holds another value. The
  `verdify-db-0` log names which one. `TooManyConnectionsError` means the login's `CONNECTION LIMIT 8` is used up.
  `TimeoutError` or a connection error points at `verdify-db` or the network path. A pod that exits at start with a
  `ConfigError` names the setting it refused.
- In a `verdify-prod-dark` sync this Deployment is the last wave (50). A pod that does not turn Ready ends that sync
  Failed once the Deployment passes its 10-minute progress deadline, with everything else applied, MCP and the ingestor
  included. During a rollout the old pod keeps serving (`maxUnavailable` 0).
- `/healthz` from the web pod timing out points at `allow-operator-api-from-dashboard-web` in `verdify-prod`. It
  admits TCP 8090 only from pods labelled `app.kubernetes.io/name: verdify-dashboard-web` in namespace
  `verdify-dashboard`, so a label change on either side breaks it.
- Both pods up while sign-in or proxied reads get 401 from the operator API means the two copies of
  `operator-api-token` differ. Read both back by key name only:

  ```bash
  kubectl -n verdify-dashboard describe secret verdify-dashboard-web
  kubectl -n verdify-prod describe secret verdify-operator-api
  ```

  A mismatch is not visible value-blind; `scripts/dashboard-smoke.sh` proves the shared token through
  INT-01. Jason re-seals both in one jvallery/agents PR; once both have synced, delete both pods
  (`kubectl -n verdify-dashboard delete pod -l app.kubernetes.io/name=verdify-dashboard-web` and
  `kubectl -n verdify-prod delete pod -l app.kubernetes.io/component=operator-api`).

## Pin revert

The web ships only through the digest pin in `deploy/k8s/dashboard/kustomization.yaml` (`images:`), so a bad web
release is undone by reverting its pin commit. For a behaviour problem that does not need another build, use the
writes flag instead.

1. Find the pin commit with `git log --oneline -- deploy/k8s/dashboard/kustomization.yaml`, then merge a PR that
   is `git revert <pin commit>`: a digest-only change.
2. `verdify-dashboard` auto-syncs at its next refresh. After one refresh the Deployment runs the previous digest,
   and `/readyz` reports the previous `source_revision`:

   ```bash
   kubectl -n verdify-dashboard get deploy verdify-dashboard-web -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
   kubectl -n verdify-dashboard get pods -l app.kubernetes.io/name=verdify-dashboard-web \
     -o jsonpath='{range .items[*]}{.status.containerStatuses[0].imageID}{"\n"}{end}'
   ```

3. Reverting R1's first pin (#1143) also returns the Deployment to 0 replicas, which takes the dashboard offline:
   traefik-apps answers 503 for the host while the Service has no endpoints.

`verdify-prod` (the operator API, migrations and the tier-1 route) rolls back separately, through a digest-only
`release-pins.yaml` commit to the promotion receipt's rollback pins and the gated full sync
(`docs/runbooks/prod-promotion.md`; AGENTS.md "Rollback" says what an API pin from before #1148 does to the operator
API).
