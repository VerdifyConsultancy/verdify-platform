# Verdify k3s Secret Contract (#30 / #66)

**Status:** WIRED in manifests; real values sealed out-of-band by laptop-root.
**Branch:** `coordinator/sprint2-unblocked` (off `live/platform-main` @ `a0478f6`).
**Scope:** the consumer-side contract for the cluster `verdify-app-secrets` Secret — the exact
keys the `deploy/k8s/**` workloads reference, where each value comes from, and the explicit list
laptop-root must seal into the canonical `agent-fleet-control` SOPS+age backend.

> **Hard rules observed here:** no plaintext secret value was read, echoed, logged, or committed.
> The repo carries only the `verdify-app-secrets` *key skeleton* (placeholder values) for
> `kustomize build` / `kubeconform` completeness; the real Secret is delivered SOPS-decrypted by
> the GitOps secret-sync step BEFORE the app reconciles. Companion prep doc (firmware agent, source
> reconciliations + sealing procedure): `docs/runbooks/verdify-secret-sealing-plan.md`.

---

## 1. Canonical `verdify-app-secrets` key contract

This is the authoritative list of keys the k3s manifests reference by name from the Secret
`verdify-app-secrets` (namespace `verdify-staging` / `verdify-prod`). Every key below appears as a
`secretKeyRef` (or projected-volume `items[].key`) in `deploy/k8s/**`, and every key is mirrored in
the overlays' `secrets.placeholder.yaml` so the render is a complete superset.

| Key | Consumer workload(s) | Pod env / mount | Required? | Device-affecting? | Source |
|---|---|---|---|---|---|
| `POSTGRES_PASSWORD` | db, api, mcp, ingestor, migrate-job | `POSTGRES_PASSWORD` (+ aliased `DB_PASS` / `DB_PASSWORD`; mcp builds `DB_DSN`) | **hard** | no | `/srv/verdify/.env` `POSTGRES_PASSWORD` |
| `VERDIFY_WRITE_API_KEY` | api | `VERDIFY_WRITE_API_KEY` (write guard, `api/main.py`) | **hard** | no | `/srv/verdify/.env` `API_WRITE_TOKEN` (NAME DRIFT — seal under new name) |
| `ESP32_API_KEY` | ingestor | `ESP32_API_KEY` (aioesphomeapi Noise PSK) | **hard** (prod) | **YES — must-not-lose** | live ingestor runtime env (canonical sha `127f85d0…`) |
| `MQTT_USER` | ingestor | `MQTT_USER` | **hard** (prod) | no | `/srv/verdify/ingestor/.env` `MQTT_USER` |
| `MQTT_PASS` | ingestor | `MQTT_PASS` | **hard** (prod) | no | `/srv/verdify/ingestor/.env` `MQTT_PASS` |
| `HERMES_IRIS_API_KEY` | ingestor (planner loop) | `HERMES_IRIS_API_KEY` | hard (planner) | no | `/srv/verdify/ingestor/.env` `HERMES_IRIS_API_KEY` |
| `OPENAI_API_KEY` | mcp, ingestor | `OPENAI_API_KEY` (`optional:true`) | soft | no | `/srv/verdify/ingestor/.env` `OPENAI_API_KEY` |
| `HA_TOKEN` | ingestor | projected file `/etc/verdify-secrets/ha_token`; `HA_TOKEN_FILE` repointed there (`optional:true` mount) | soft | no | ingestor's OWN HA long-lived token — file `ha_token.txt` (sha `ff9965e8…`) |
| `VERDIFY_CONTACT_SMTP_USERNAME` | api | `VERDIFY_CONTACT_SMTP_USERNAME` (`optional:true`) | soft | no | contact-form SMTP auth user |
| `VERDIFY_CONTACT_SMTP_PASSWORD` | api | `VERDIFY_CONTACT_SMTP_PASSWORD` (`optional:true`) | soft | no | contact-form SMTP auth password |
| `VERDIFY_TURNSTILE_SECRET` | api | `VERDIFY_TURNSTILE_SECRET` (`optional:true`) | soft | no | Cloudflare Turnstile server-side verify key |
| `VERDIFY_CONTACT_HASH_SALT` | api | `VERDIFY_CONTACT_HASH_SALT` (`optional:true`) | soft | no | submitter-IP hash salt (stable, rotation-independent) |

**Optionality semantics.** `optional:true` `secretKeyRef`s let the pod start when the key is
absent (the consuming code fails-closed at request time — e.g. the contact endpoint returns a
config error, the mcp embedding tools return a clear `requires OPENAI_API_KEY` error). The four
**hard** keys (`POSTGRES_PASSWORD`, `VERDIFY_WRITE_API_KEY`, plus `ESP32_API_KEY`/`MQTT_*` for the
prod ingestor) are NOT optional — a missing one blocks pod start. This is intentional: the device
writer must never run half-credentialed.

### Non-secret config (NOT in the Secret — lives in `verdify-config` ConfigMap)
Addresses / ports / bools only, pinned per-instance, never sealed:
`DB_HOST/PORT/NAME/USER`, `ESP32_HOST/PORT`, `HA_URL`, `MQTT_HOST/PORT`, `FRIGATE_URL`,
`MCP_HTTP_HOST/PORT`, `HA_TOKEN_FILE` (points at the projected token file),
`VERDIFY_CONTACT_SMTP_PORT/SSL/STARTTLS/TIMEOUT_S`, and the `VERDIFY_CONTACT_NOTIFY_*` addressing
keys. `VERDIFY_DEVICE_WRITE_ENABLED` is an overlay-pinned interlock (staging=0, prod=1).

---

## 2. ESP32 preservation — MUST NOT LOSE (highest priority)

Two distinct ESP32 credentials exist; do not conflate them:

| Credential | sha256(value) first16 | Where it lives | Goes into cluster Secret? |
|---|---|---|---|
| `ESP32_API_KEY` (ingestor → ESP32 native API connect key) | `127f85d0970df326` | live ingestor runtime / `ingestor/.env` | **YES** — `verdify-app-secrets` / `ESP32_API_KEY` |
| `api_encryption_key` == `esphome_api_key` (ESPHome firmware side) | `df2784f9b0923df4` | `/srv/greenhouse/esphome/secrets.yaml` | **NO** — stays firmware-side |
| `ota_password` (ESPHome) | `65d046f727d5eec9` | `/srv/greenhouse/esphome/secrets.yaml` | **NO** — stays firmware-side |

- The cluster only needs **`ESP32_API_KEY`** (the ingestor connect key, sha `127f85d0…`). It is
  the canonical Noise PSK the running ingestor uses to reach `192.168.10.111:6053` today.
- The three firmware-side keys (`api_encryption_key`/`esphome_api_key`, `ota_password`) stay in the
  firmware build host `secrets.yaml`. The OTA/flash path remains on the VM per the firmware freeze
  rules. Their value-hashes are recorded ABOVE so laptop-root can confirm none are lost during the
  management-VM drain.
- **`api_encryption_key` (df2784f9) has DRIFTED from `ESP32_API_KEY` (127f85d0).** Seal the cluster
  key from the live-ingestor canonical value, NOT from `esphome/secrets.yaml`. Reconciling the
  drift at the firmware source is a SEPARATE, later, firmware-PR-gated action — never a side effect
  of this seal, and never a re-flash. (Sealing/rotating the PSK is gated on explicit Jason
  confirmation: canonical value, carry-vs-rotate, no re-flash.)

---

## 3. Seal-list for laptop-root (agent-fleet-control `verdify-app-secrets`)

The canonical Secret is owned by laptop-root's `agent-fleet-control` repo (SOPS+age, recipient
`age1jd6c7lm7vhj56gve6dvj59mepwpukhnyyh8wyca9y7mrjfeyqs8qjvqd5k`). This repo NEVER holds real
values — only the placeholder skeleton + this contract. **Hand-off: seal the keys below into
`verdify-app-secrets` for BOTH `verdify-staging` and `verdify-prod` namespaces.**

### 3.1 Already sealed in `verdify-staging` (verified 2026-05-31, names only)
`POSTGRES_PASSWORD`, `VERDIFY_WRITE_API_KEY`, `MQTT_USER`, `MQTT_PASS` — re-applying these is a
no-op; do NOT churn them (DB-auth drift risk).

### 3.2 MISSING — laptop-root must seal these (in priority order)

| # | Key | Source (over ssh pipe, never on CLI) | Gate | Notes |
|---|---|---|---|---|
| 1 | **`ESP32_API_KEY`** | live ingestor runtime / `/srv/verdify/ingestor/.env` (canonical `127f85d0…`) | **Jason** (device-affecting) | **MUST-NOT-LOSE.** Confirm canonical = running ingestor's key; carry-not-rotate; no re-flash. **Currently NOT sealed in staging** despite prior comments — this is the top gap. |
| 2 | `HERMES_IRIS_API_KEY` | `/srv/verdify/ingestor/.env` | laptop-root | Path-mismatch from old meta (`source.nas_path` was `/srv/verdify/.env`); source from `ingestor/.env`. |
| 3 | `OPENAI_API_KEY` | `/srv/verdify/ingestor/.env` | laptop-root | Shared by mcp + ingestor; mcp has no own `.env` in-container. |
| 4 | `HA_TOKEN` | ingestor's own HA long-lived token file `ha_token.txt` (sha `ff9965e8…`) | laptop-root | DISTINCT from firmware `ha_token` / `ha_bearer_token`. Sealed as a VALUE (projected to a file in-pod), not a path. |
| 5 | `VERDIFY_CONTACT_SMTP_USERNAME` | contact-form source | laptop-root + James (source key absent from `/srv/verdify/.env` today — confirm/provide) | api contact surface; soft. |
| 6 | `VERDIFY_CONTACT_SMTP_PASSWORD` | contact-form source | laptop-root + James | api contact surface; soft. |
| 7 | `VERDIFY_TURNSTILE_SECRET` | contact-form source | laptop-root + James | api contact surface; soft. |
| 8 | `VERDIFY_CONTACT_HASH_SALT` | generate-once stable salt | laptop-root | If absent, `api/main.py` falls back to write-key/DB-pass for the IP hash — supply explicitly so the salt is rotation-independent. |

> **`VERDIFY_WRITE_API_KEY` name drift:** the source `.env` names it `API_WRITE_TOKEN`. Seal under
> the NEW name `VERDIFY_WRITE_API_KEY` with value = current `API_WRITE_TOKEN` (the registry meta can
> alias `from_source_key: API_WRITE_TOKEN`, the lower-blast-radius option that does not touch the VM).
> Already present in staging, so this is the contract for any future re-seal / prod seal.

### 3.3 Apply order (unchanged from the sealing plan)
1. Registry secret-meta PR merges (non-secret) — owner laptop-root/James.
2. Source reconciliations land (`API_WRITE_TOKEN`→`VERDIFY_WRITE_API_KEY` alias; `ingestor/.env`
   sourcing for MQTT/HERMES/OPENAI/HA_TOKEN; confirm/provide contact-form keys).
3. `seal-secret.sh <id>` per key/secret over the ssh pipe (`--remote jason@vm-docker-iris...`).
   Seal `ESP32_API_KEY` only after the Jason device gate clears; do NOT use `--all`.
4. `local-k8s-secret-sync.yml` decrypts + `kubectl apply`s into the namespace BEFORE ArgoCD
   reconciles (namespace exists → secret-sync → app sync). Namespace string must be byte-identical
   across the Namespace object, the ArgoCD `destination.namespace`, and the secret-meta `target`.

---

## 4. Manifest changes wired in this branch

- `deploy/k8s/base/api-deployment.yaml` — added the four contact-form `secretKeyRef`s
  (`VERDIFY_CONTACT_SMTP_USERNAME/PASSWORD`, `VERDIFY_TURNSTILE_SECRET`, `VERDIFY_CONTACT_HASH_SALT`),
  all `optional:true`.
- `deploy/k8s/base/mcp-deployment.yaml` — added `OPENAI_API_KEY` (`optional:true`); `DB_DSN`
  substitution unchanged.
- `deploy/k8s/base/ingestor-deployment.yaml` — added `HERMES_IRIS_API_KEY` (hard),
  `OPENAI_API_KEY` (`optional:true`), and an `optional` secret-backed volume `ha-token` projecting
  `HA_TOKEN` → `/etc/verdify-secrets/ha_token`.
- `deploy/k8s/base/configmap.yaml` — `HA_TOKEN_FILE=/etc/verdify-secrets/ha_token` (repoint the
  file loader off the non-existent NAS path) + the non-secret contact-form SMTP transport config.
- `deploy/k8s/overlays/{staging,prod}/secrets.placeholder.yaml` — placeholder key skeleton expanded
  to the full superset; **prod placeholder converted from a local-config LABEL to an ANNOTATION** so
  `kustomize build` excludes it (it was previously leaking a placeholder Secret into the prod
  render — a real GitOps apply would have clobbered the SOPS-delivered `verdify-app-secrets`).

### Validation (this branch)
- `kustomize build overlays/{staging,prod}` → OK; `kind: Secret` count = **0** in both renders
  (placeholders correctly excluded).
- `kubeconform -strict -ignore-missing-schemas` → 16/16 valid, 0 errors, both overlays.
- `kubectl apply --dry-run=server -n verdify-staging` → all workloads `configured/created`
  (only the cluster-scoped Namespace patch is `Forbidden`, expected for the scoped agent SA).
- `make lint` → clean.

---

## 5. `.sops.yaml` ruleset

`.sops.yaml` `creation_rules` already pin `path_regex: deploy/k8s/.*\.sops\.ya?ml$` with
`encrypted_regex: ^(data|stringData)$` and the fleet age recipient. The in-repo
`secrets.placeholder.yaml` files are NOT `.sops.yaml` and are NOT sealed — they carry the
`config.kubernetes.io/local-config: "true"` ANNOTATION so they exist only for CI render/lint and
never reach the cluster. The real sealed ciphertext lives in `agent-fleet-control`
(`secrets/encrypted/verdify-app-secrets.enc.yaml`); kustomize cannot decrypt SOPS, which is why the
real Secret is a GitOps out-of-band delivery, not a `resources:` entry.
