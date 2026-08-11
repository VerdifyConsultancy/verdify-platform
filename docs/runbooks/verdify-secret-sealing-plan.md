# Verdify Secret Inventory + SOPS/age Sealing Plan (k3s cutover prep)

> **Obsolete under the 2026-06-19 single-environment model.** This prep plan
> targets the retired staging namespace and old secret-delivery flow. Do not use
> its `verdify-staging` namespace instructions for current prod operations.

**Status:** PREP / DESIGN ONLY. Nothing in this doc has been sealed or applied.
**Author:** firmware agent, 2026-05-30. Branch `firmware/cicd-golden-path` @ `f350bcd` (PR #55).
**Scope:** the complete Verdify secret inventory the k3s stack needs, cross-checked against the
live VM, with the pipe-only SOPS+age sealing procedure and the direct root execution safeguards.

> **Hard rule observed throughout:** this is a path/name/owner/mode inventory ONLY. No secret
> VALUE was read, echoed, logged, or sealed. Names/paths were gathered with `grep -oE "^[A-Z_]+="`
> (key names, no values). Do NOT run any `seal-secret.sh` step from this doc without the gate
> owner's confirmation named below.

Authoritative inputs:
- Handoff §2.4 (SOPS+age secret delivery) — `/mnt/agents/root/docs/verdify-cicd-refactor-handoff.md`.
- Registry secret-meta + sealing mechanism — `agent-fleet-control` (`/tmp/afc` on this VM):
  `.sops.yaml`, `scripts/seal-secret.sh`, `registry/secrets/verdify-*.yaml`.
- k3s manifests on this branch — `deploy/k8s/base/*` + `deploy/k8s/overlays/local-staging/*`.

---

## 1. Secret inventory

### 1.1 What the k3s stack actually consumes (the seal-critical subset)

Inspected via `grep -rn secretKeyRef deploy/k8s/`. Every workload references the Secret
**by name only** (`secretKeyRef.name: verdify-app-secrets`, plus a separate ESP32 PSK / GHCR pull
secret). The base kustomization does NOT define the Secrets; the overlay's
`secrets.placeholder.yaml` is `config.kubernetes.io/local-config:"true"` (lint-only, never applied).

The five k8s keys the live-staging manifests bind today:

| k8s Secret + key | Consumer workload(s) | env var in pod | VM source path (key name on VM) | Sealed? | Notes |
|---|---|---|---|---|---|
| `verdify-app-secrets` / `POSTGRES_PASSWORD` | `verdify-db` (StatefulSet), `verdify-api`, `verdify-mcp`, `verdify-ingestor`, migration Job | `POSTGRES_PASSWORD` + aliased to `DB_PASS`/`DB_PASSWORD` | `/srv/verdify/.env` → `POSTGRES_PASSWORD` ✅ present | NO | Clean source key match. Non-device. laptop-root can seal once registry PR merges. |
| `verdify-app-secrets` / `VERDIFY_WRITE_API_KEY` | `verdify-api` (write/admin guard, `api/main.py:266`) | `VERDIFY_WRITE_API_KEY` | `/srv/verdify/.env` → key is named **`API_WRITE_TOKEN`** ⚠️ NAME DRIFT | NO — BLOCKED | Source-key mismatch (see §1.3-A). Cannot seal until reconciled. Non-device. |
| `verdify-app-secrets` / `MQTT_USER` | `verdify-ingestor` | `MQTT_USER` | NOT in `/srv/verdify/.env`; lives in **`/srv/verdify/ingestor/.env`** ⚠️ PATH MISMATCH | NO — BLOCKED | Meta sources `/srv/verdify/.env`; key is in `ingestor/.env`. See §1.3-B. Non-device. |
| `verdify-app-secrets` / `MQTT_PASS` | `verdify-ingestor` | `MQTT_PASS` | NOT in `/srv/verdify/.env`; lives in **`/srv/verdify/ingestor/.env`** ⚠️ PATH MISMATCH | NO — BLOCKED | Same as MQTT_USER. Non-device. |
| `verdify-esp32-psk` / `ESP32_API_KEY` | `verdify-ingestor` (single persistent ESPHome native-API conn to `192.168.10.111:6053`) | `ESP32_API_KEY` | `/srv/verdify/ingestor/.env` → `ESP32_API_KEY` ✅ present | NO — GATED | **DEVICE-AFFECTING.** Held in a SEPARATE secret so a bulk re-seal can't touch it. See §3. Gated on the runtime preflight. |

> Note: the placeholder lists `ESP32_API_KEY` inside `verdify-app-secrets`, but the registry
> contract correctly splits it into the standalone **`verdify-esp32-psk`** secret-meta. The
> standalone meta is the source of truth; the placeholder is a lint-only superset. Final
> ingestor manifest should reference the ESP32 key from `verdify-esp32-psk` (a follow-up
> manifest edit, NOT in this doc's scope).

### 1.2 The full `verdify-app-secrets` registry meta (broader than the k3s stack uses today)

`registry/secrets/verdify-app-secrets.yaml` maps 13 k8s keys (owner: **james**), anticipating the
site/grafana/umami/contact-form/planner surfaces moving later. Cross-checked against the VM:

| k8s key (in meta) | Maps from source key | In `/srv/verdify/.env`? | Seal status / note |
|---|---|---|---|
| `POSTGRES_PASSWORD` | `POSTGRES_PASSWORD` | ✅ yes | sealable (non-device) |
| `DB_PASSWORD` | `POSTGRES_PASSWORD` | ✅ (alias of above) | sealable (non-device) |
| `VERDIFY_WRITE_API_KEY` | `VERDIFY_WRITE_API_KEY` | ❌ file has `API_WRITE_TOKEN` | BLOCKED — name drift (§1.3-A) |
| `MQTT_USER` | `MQTT_USER` | ❌ only in `ingestor/.env` | BLOCKED — path mismatch (§1.3-B) |
| `MQTT_PASS` | `MQTT_PASS` | ❌ only in `ingestor/.env` | BLOCKED — path mismatch (§1.3-B) |
| `HERMES_IRIS_API_KEY` | `HERMES_IRIS_API_KEY` | ❌ only in `ingestor/.env` | BLOCKED — path mismatch (§1.3-B) |
| `VERDIFY_CONTACT_SMTP_USERNAME` | `VERDIFY_CONTACT_SMTP_USERNAME` | ❌ not present (file has only SMTP_HOST/PORT/SSL/STARTTLS/TIMEOUT + NOTIFY_*) | BLOCKED — missing source key (§1.3-C) |
| `VERDIFY_CONTACT_SMTP_PASSWORD` | `VERDIFY_CONTACT_SMTP_PASSWORD` | ❌ not present | BLOCKED — missing source key (§1.3-C) |
| `VERDIFY_TURNSTILE_SECRET` | `VERDIFY_TURNSTILE_SECRET` | ❌ not present | BLOCKED — missing source key (§1.3-C) |
| `VERDIFY_CONTACT_HASH_SALT` | `VERDIFY_CONTACT_HASH_SALT` | ❌ not present | BLOCKED — missing source key (§1.3-C) |
| `GRAFANA_ADMIN_PASSWORD` | `GRAFANA_ADMIN_PASSWORD` | ✅ yes | sealable (non-device) |
| `UMAMI_APP_SECRET` | `UMAMI_APP_SECRET` | ✅ yes | sealable (non-device) |
| `UMAMI_DB_PASSWORD` | `UMAMI_DB_PASSWORD` | ✅ yes | sealable (non-device) |

So **5 of the 13 app-secret keys seal cleanly today** (`POSTGRES_PASSWORD`, `DB_PASSWORD`,
`GRAFANA_ADMIN_PASSWORD`, `UMAMI_APP_SECRET`, `UMAMI_DB_PASSWORD`); 8 are blocked on a source
reconciliation that James (the meta owner) must land before a clean `--all` seal will succeed.

`seal-secret.sh` runs `_render_secret.py` over the source dotenv driven by `target.keys[]`; a
`from_source_key` that is absent in the named `source.nas_path` will render an empty/missing
value (or fail the non-empty-skeleton assert) — i.e. these mismatches are **seal-time failures**,
not silent. Surfacing them now avoids a failed seal run during the gated window.

### 1.3 Source reconciliations needed BEFORE a clean app-secret seal (owner: James)

**A. `VERDIFY_WRITE_API_KEY` name drift.** k3s + meta expect `VERDIFY_WRITE_API_KEY`; the live
`/srv/verdify/.env` and `/srv/verdify/api/.env` both name it `API_WRITE_TOKEN`. Two clean fixes
(James decides, no value touched):
   - (i) rename the source key to `VERDIFY_WRITE_API_KEY` on the VM `.env`, OR
   - (ii) change the meta to `from_source_key: API_WRITE_TOKEN` (gravity does exactly this aliasing
     for `MCP_API_TOKEN`←`INTERNAL_SERVICE_TOKEN`; the schema supports a divergent
     `k8s_key`/`from_source_key`). (ii) is the lower-blast-radius, VM-non-touching option.

**B. MQTT_* and HERMES_IRIS_API_KEY live in a different file.** They are in
`/srv/verdify/ingestor/.env`, not the meta's `source.nas_path: /srv/verdify/.env`. `seal-secret.sh`
pulls ONE `nas_path` per secret-id, so these cannot be sealed from `/srv/verdify/.env`. Options
(James decides): split a second secret-meta `verdify-ingestor-secrets` sourcing `ingestor/.env`
for `MQTT_USER`/`MQTT_PASS`/`HERMES_IRIS_API_KEY`, OR consolidate these keys into `/srv/verdify/.env`.
The split mirrors how `verdify-esp32-psk` already correctly sources `ingestor/.env`.

**C. Contact-form / turnstile keys absent at source.** `VERDIFY_CONTACT_SMTP_USERNAME`,
`VERDIFY_CONTACT_SMTP_PASSWORD`, `VERDIFY_TURNSTILE_SECRET`, `VERDIFY_CONTACT_HASH_SALT` are not in
`/srv/verdify/.env` at all (only SMTP transport config + NOTIFY addressing are). Either these are
not actually in use (drop from the meta) or they live elsewhere; James confirms before they are
mapped. None of these are device-affecting or block the k3s-stack-critical 5-key subset.

### 1.4 GHCR image-pull secret (infra, laptop-root-sealable)

| k8s Secret | Type | Consumer | Source path | Sealed? | Notes |
|---|---|---|---|---|---|
| `verdify-ghcr-pull` | `kubernetes.io/dockerconfigjson` | kubelet pull for all `ghcr.io/<owner>/verdify-<comp>` images in `verdify-staging` | `/mnt/agents/root/secrets/ghcr_read_token.txt` (NAS, `format: opaque-token`) | NO | k8s Secrets don't cross namespaces; staging needs its own pull secret even though it reuses the fleet read-only GHCR token. Non-device, laptop-root-owned source. Referenced by name in each Deployment `imagePullSecrets`. |

---

## 2. The pipe-only `seal-secret.sh` procedure (EXACT command shape)

`scripts/seal-secret.sh` (in `agent-fleet-control`) reads the NON-secret `registry/secrets/<id>.yaml`,
pulls plaintext from `source.nas_path` over an **ssh stdin pipe**, pipes it straight into
`_render_secret.py` (base64 happens only inside python, into a `0600` temp), then `sops --encrypt`s
to `secrets/encrypted/<id>.enc.yaml`. The value never lands on the CLI, in a log, or on local disk
in cleartext; `set -o pipefail` + a `RETURN` trap clean the temps. Output is ciphertext only.

**Command shape (no value ever on the CLI):**

```bash
# Run from the agent-fleet-control repo root (the registry), NOT this worktree.
# The repo venv interpreter is used (.venv/bin/python with pyyaml).
# --remote names the live greenhouse VM that holds the source .env. Confirm the VM
# hostname is reachable FIRST (handoff: it is vm-docker-iris, NOT vm-verdify=NXDOMAIN).

# Seal ONE secret by id (the registry meta drives source path + key map):
scripts/seal-secret.sh verdify-app-secrets --remote jason@vm-docker-iris.servers.vallery.net

# The plaintext is read as:  ssh <remote> "cat -- '<nas_path>'" | _render_secret.py | sops --encrypt
# i.e. piped on STDIN end-to-end. There is NO positional/flag argument that takes a value.
# Do NOT pass a value, a file you cat yourself, or env-inject a credential. Let the script pipe.
```

Notes from the script body (verified):
- The default `--remote` is `jason@orbit.servers.vallery.net`; for Verdify you MUST override to the
  greenhouse VM `jason@vm-docker-iris.servers.vallery.net` (or set `SEAL_REMOTE`). `orbit` does NOT
  hold the Verdify `.env`.
- The script asserts the sops output contains `sops:` + `ENC[` and refuses to write otherwise —
  it never emits a half-written or cleartext artifact.
- `--all` seals every `registry/secrets/*.yaml`. **Do NOT use `--all` for Verdify** until the §1.3
  reconciliations land AND the `verdify-esp32-psk` execution safeguard is cleared — `--all` would attempt the
  device-affecting PSK and the blocked app keys in one shot. Seal by explicit id.

### 2.1 `.sops.yaml` / age recipient (already pinned, safe to commit)

`.sops.yaml` pins the fleet age PUBLIC recipient
`age1jd6c7lm7vhj56gve6dvj59mepwpukhnyyh8wyca9y7mrjfeyqs8qjvqd5k` and
`encrypted_regex: '^(data|stringData)$'` — so only the value-bearing block is encrypted; the
Secret skeleton (`apiVersion/kind/metadata/type`) stays readable for review. The age PRIVATE key
lives ONLY on the NAS (`/mnt/agents/root/secrets/age/keys.txt`) and the operator's
`~/.config/sops/age/keys.txt`. This doc neither reads nor needs the private key.

### 2.2 Namespace-match requirement (the silent-never-mounts gotcha)

Every Verdify secret-meta's `target.namespace` is `verdify-staging` and MUST stay **byte-identical**
to:
- the base `Namespace` object — `deploy/k8s/base/namespace.yaml` → `verdify-staging` ✅
- the ArgoCD Application `destination.namespace` (in `jvallery/agents`, when added) → must be `verdify-staging`
- the overlay namespace (`deploy/k8s/overlays/local-staging/kustomization.yaml`) → `verdify-staging`

Verified today: the three registry metas (`verdify-app-secrets`, `verdify-esp32-psk`,
`verdify-ghcr-pull`) all carry `target.namespace: verdify-staging`, matching the base Namespace.
A sealed Secret applied to a mismatched namespace silently never mounts (the gravity 3-way
mismatch). Pin one string per environment; do not rename one place without the others.

### 2.3 Apply order (secret delivered BEFORE the app reconciles)

The delivery is NOT `seal-secret.sh` — that only encrypts AT REST into git. Delivery into k3s is the
protected GitOps workflow `jvallery/agents/.github/workflows/local-k8s-secret-sync.yml`, which
SOPS-decrypts on the self-hosted runner and `kubectl apply`s the namespaced Secret. Order:

1. Registry PR merges the NON-secret `registry/secrets/verdify-*.yaml` metas (gates: `make validate`
   + `make verify-reproducible` exit 0). — laptop-root reviews; James owns the metas.
2. `seal-secret.sh <id>` runs (per §2 + the §1.3 reconciliations + the §3 execution safeguard for the PSK),
   committing only ciphertext `secrets/encrypted/<id>.enc.yaml`.
3. `local-k8s-secret-sync.yml` (protected runner) decrypts + `kubectl apply`s the Secrets into the
   `verdify-staging` namespace. Its `target` enum must be extended with a `verdify-staging` case arm
   (target → namespace + runtime_secret + image_pull_secret) — confirm with laptop-root before the
   first sync into a new namespace.
4. **Namespace must exist** and the Secrets must be present **before** ArgoCD reconciles the app
   (ArgoCD Application uses `CreateNamespace=false`; the app references Secrets by name and will not
   mount a Secret that isn't there yet). So: namespace create → secret-sync → ArgoCD sync.

---

## 3. ESP32_API_KEY callout (DEVICE-AFFECTING, protected by runtime safeguards)

**Secret:** `verdify-esp32-psk` / key `ESP32_API_KEY` (standalone, NOT folded into
`verdify-app-secrets` precisely so a bulk re-seal cannot touch the device credential).

**Canonical value = sha `127f85d0`** — evidence: the live ingestor is connected + healthy right now
(direct-pushed 9/9, climate fresh), so the `ESP32_API_KEY` its runtime is actually using to reach
`192.168.10.111:6053` is by definition the working/canonical Noise PSK. The esphome
`secrets.yaml` `api_encryption_key` (sha `df2784f9`) has **DRIFTED** and is the wrong source.

**Seal-source = the live ingestor runtime env**, NOT the esphome file and NOT a re-flash:
- The registry meta `verdify-esp32-psk.yaml` sources `nas_path: /srv/verdify/ingestor/.env`
  (`format: dotenv`), which DOES contain `ESP32_API_KEY`. That dotenv is the on-disk twin of the
  ingestor runtime env. **CAVEAT:** the meta itself notes the value is ALSO sourced from the DB
  `greenhouses` table at runtime, and **DB overrides .env**. So before sealing, the executor must validate
  the `ingestor/.env` `ESP32_API_KEY` equals the runtime-canonical `127f85d0` (i.e. the .env hasn't
  drifted from the DB row the way esphome's `secrets.yaml` did). If the .env and DB disagree, seal
  from whichever the running ingestor uses — that is the definition of canonical.
- **NEVER seal from `/srv/greenhouse/esphome/secrets.yaml` `api_encryption_key`** (the drifted
  `df2784f9`). Sealing the drifted value would hand the k3s ingestor a key the device will reject.
- **NEVER trigger a re-flash** as a side effect of sealing. Reconcile-at-source only. Sealing the
  PSK does not, and must not, touch firmware/OTA.

**GATE: verify the exact target and prerequisites canonical FIRST.** Per handoff §6 / P2 STOP-&-ask, the PSK seal is
device-affecting. Jason must:
  1. confirm `127f85d0` is canonical (the running ingestor's key),
  2. confirm rotate-at-seal vs carry-existing (a half-rotation across .env / firmware / DB row
     breaks the control loop — all three must agree),
  3. confirm no re-flash occurs.
Only then may `seal-secret.sh verdify-esp32-psk --remote jason@vm-docker-iris...` run.

**Mismatch reconciliation is a SEPARATE, later action** — the esphome `secrets.yaml` `df2784f9`
should be reconciled to `127f85d0` at source on the firmware side under the normal firmware PR
artifact + exact-target validation path; it is OUT OF SCOPE for the k3s seal and must not be done as a
side effect here. (Firmware freeze rules: no OTA, reconcile-at-source.)

---

## 4. runtime-safeguarded vs laptop-root-can-seal-now split

### 4.1 laptop-root can seal NOW (after the registry PR merges) — non-device, source-clean
- **`verdify-app-secrets`, the 5 clean keys only:** `POSTGRES_PASSWORD`, `DB_PASSWORD` (alias),
  `GRAFANA_ADMIN_PASSWORD`, `UMAMI_APP_SECRET`, `UMAMI_DB_PASSWORD`. These map cleanly from
  `/srv/verdify/.env`. NOTE: a single `seal-secret.sh verdify-app-secrets` run renders ALL 13
  mapped keys at once, so it will fail on the 8 blocked keys until §1.3 lands — laptop-root should
  seal `verdify-app-secrets` only AFTER James reconciles §1.3-A/B/C, otherwise the seal aborts.
- **`verdify-ghcr-pull`:** infra dockerconfigjson, NAS-sourced fleet token, fully laptop-root-owned.
  Sealable now.

### 4.2 Gated on James (VerdifyConsultancy source owner) — must land BEFORE the app-secret seal
- §1.3-A: reconcile `VERDIFY_WRITE_API_KEY` vs `API_WRITE_TOKEN` (prefer meta-side aliasing).
- §1.3-B: resolve MQTT_*/HERMES source path (split a `verdify-ingestor-secrets` meta on
  `ingestor/.env`, or consolidate into `/srv/verdify/.env`).
- §1.3-C: confirm/drop the absent contact-form/turnstile keys.
These are metadata/source reconciliations (PRs into the registry + possibly the VM `.env`), no
values exposed.

### 4.3 Gated on the runtime preflight (device-affecting) — confirm BEFORE any seal
- **`verdify-esp32-psk` / `ESP32_API_KEY`:** confirm `127f85d0` canonical, confirm rotate-vs-carry,
  confirm no re-flash, confirm `.env` == runtime/DB. Then laptop-root may run the single-id seal.
  (§3.)
- The first `local-k8s-secret-sync.yml` run into the NEW `verdify-staging` namespace (handoff P5
  STOP-&-ask) — confirm with root executor before the first sync.

### 4.4 Nothing in this doc is executed here
This is PREP. No secret was read/echoed/sealed. The exact commands above are for a human (laptop-root
to seal/sync; James to reconcile source; the executor to validate the device-affecting PSK). The next
concrete actions are, in order: (1) merge the registry secret-metas PR (gates green); (2) James lands
§1.3 reconciliations; (3) laptop-root seals `verdify-ghcr-pull` + the reconciled `verdify-app-secrets`;
(4) verify the exact target and prerequisites the ESP32 PSK gate, then laptop-root seals `verdify-esp32-psk`; (5) extend +
run `local-k8s-secret-sync.yml` into `verdify-staging` before ArgoCD reconciles.
