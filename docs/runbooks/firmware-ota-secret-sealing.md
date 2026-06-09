# Firmware OTA Secret Sealing + the k3s Firmware Landing Zone

**Status:** PREP / DESIGN ONLY. Nothing here has been sealed, applied, flashed, or
pushed to a device. This is a path/name/owner/consumer inventory + the operator
runbook for sealing the ESPHome **OTA password** into the k3s era. No secret value
is read, echoed, or sealed by authoring this doc.
**Author:** firmware agent (sandboxed agent pod, RBAC-less, no device/DB/firmware
secrets — read-and-author only).
**Scope owner of the artifacts below:** `deploy/k8s/**` + `docs/runbooks/**` are
SHARED TERRITORY (coordinator/Jason). This doc + the companion
`deploy/k8s/overlays/prod/firmware-ota-secret.placeholder.yaml` are PROPOSALS the
coordinator reviews; the device-affecting seal itself is **Jason-gated**.

> **Hard rule observed throughout:** OTA is NEVER part of the k3s cutover sequence
> (`k3s-cutover-sequence.md` §0 / DoD #7). The control loop, single-writer, and
> setpoint paths move to k3s; the **OTA flash path stays an operator-host, human-gated
> action** (`make firmware-deploy`). This doc re-homes the *credential* and the
> *toolchain* off the dead `.150` VM — it does NOT move flashing into a pod.

---

## 0. TL;DR — what "sealing the OTA password into k3s" actually means

The OTA password (`ota_password`, the ESPHome `ota:` platform password,
`firmware/greenhouse.yaml:205-207`) is **not consumed by any in-cluster workload**.
It is read by the operator's **tooling host** when running:

- `make firmware-deploy` → `scripts/firmware-esphome-worktree.sh … upload` (compile + flash)
- `scripts/firmware-rollback.sh` (auto-rollback on post-OTA sensor-health failure;
  reads `OTA_PW` env, else parses the legacy `secrets.yaml`)

So "sealing it into k3s" is **NOT** the same shape as `ESP32_API_KEY` /
`POSTGRES_PASSWORD` (which a pod mounts via `secretKeyRef`). There is **no pod that
mounts `ota_password`** today and the cutover design never adds one. The seal exists
so the operator's OTA tooling can **fetch** the credential from one trusted place
(the cluster) instead of the powered-off `.150` `secrets.yaml`. Concretely it is:

1. a SOPS+age-sealed **k8s Secret `verdify-firmware-ota`** (key `ota_password`) in the
   `verdify-prod` namespace, delivered by the same fleet GitOps secret-sync the other
   Verdify secrets use; **and**
2. an operator step that, on the OTA tooling host (greenhouse-LAN + `kubectl`),
   reads that Secret into the `OTA_PW` env right before `make firmware-deploy`.

The Secret is the **at-rest home** of the value; the env feed is the **runtime
delivery** to the espota2 transport. The two pieces are deliberately split so the
credential is never on a process CLI or in a committed file.

---

## 1. Where the OTA / ESPHome secrets live, and who consumes them

ESPHome's `secrets.yaml` (formerly `/srv/greenhouse/esphome/secrets.yaml` on `.150`)
holds several keys, but only TWO are real actuation credentials
(`docs/design/firmware-digital-twin.md:258`):

| ESPHome secret key | Real actuation path | k3s home today | Consumer of the k3s copy |
|---|---|---|---|
| `api_encryption_key` (Noise PSK) | aioesphomeapi native-API `:6053` (setpoint/occupancy push + telemetry) | `verdify-app-secrets/ESP32_API_KEY` (`deploy/k8s/SECRETS.md:70`), DRIFTED at the esphome source — sha `df2784f9` vs runtime-canonical `127f85d0` | **ingestor pod** (`secretKeyRef`), prod only |
| `ota_password` | ESPHome OTA `:3232` (`esphome upload` / `espota2.run_ota`) | **NOT sealed anywhere** (this doc) | **operator tooling host** (`OTA_PW` env), NOT a pod |
| `wifi_ssid` / `wifi_password` | compile-time only (baked into the build) | not needed in k3s — build-time, lives in the OTA toolchain's `secrets.yaml` | esphome compile on the tooling host |
| `cloud_mqtt_*`, `ha_bearer_token`, `shelly_em50_url` | not on the device actuation path | sealed elsewhere / ingestor env | n/a for OTA |

The full template is `firmware/secrets.example.yaml` (CI placeholders only).

**Key distinction (and the reason this is its own doc):** the
`verdify-secret-sealing-plan.md` + `deploy/k8s/SECRETS.md` cover `ESP32_API_KEY` and
the app secrets — they do **NOT** cover `ota_password`. The `#254` re-home wired the
`firmware-rollback.sh` `OTA_PW` env hook but explicitly notes "The ota_password is
NOT yet in any k3s secret; sealing it is device-affecting and GATED on Jason"
(commit `87f5610`, `scripts/firmware-rollback.sh:19-25`). This doc closes that gap.

---

## 2. What the `firmware-deploy` preflight kube backend needs

`make firmware-deploy` runs `scripts/firmware-deploy-preflight.sh` with
`VERDIFY_DB_BACKEND=kube` (Makefile default `FIRMWARE_DB_BACKEND ?= kube`,
`Makefile:38`). The preflight sources `scripts/lib/psql-verdify.sh` and, in
`kube-exec` mode, runs every DB guard against the live prod DB via:

```
<VERDIFY_KUBECTL> exec -n verdify-prod verdify-db-0 -c postgres -- \
    env PGOPTIONS=-c statement_timeout=5000 psql -U verdify -d verdify -t -A -F '|' -c "<guard SQL>"
```

Knobs (defaults in `psql-verdify.sh:102-105`):

| Env | Default | Meaning |
|---|---|---|
| `VERDIFY_KUBECTL` | `kubectl` | the binary OR a multi-token remote driver, word-split. The proven driver for a tooling host without a local kubeconfig is `ssh jason@192.168.30.32 sudo k3s kubectl` (a node with `k3s kubectl`). |
| `VERDIFY_DB_NAMESPACE` | `verdify-prod` | DB namespace. |
| `VERDIFY_DB_POD` | `verdify-db-0` | DB pod (StatefulSet). |
| `VERDIFY_DB_PGCONTAINER` | `postgres` | container with `psql`. |

The guard SQL it runs (read-only, `statement_timeout=5s`): unresolved
critical/legacy-high alerts (`alert_log`), climate freshness (`climate`),
`climate_action_log` freshness + decision-proof completeness, 24h forecast max
(stress-window warning), the `last-good.ota.bin` 48-hour bake mtime check, and the
weekly-OTA limit (first-seen `diagnostics.firmware_version` this MDT week). All are
SELECTs — the preflight never writes.

**Reachability the preflight needs:** the tooling host must be able to run
`VERDIFY_KUBECTL exec` against the in-cluster headless `verdify-db` Service (it is
ClusterIP/in-cluster only — `psql-verdify.sh:37`). With the `ssh … sudo k3s kubectl`
driver, the only network requirement is SSH to the k3s node; the node-local kubectl
reaches the pod. **This was already proven read-only against `verdify-prod`/
`verdify-db` in `#254`** — the re-homed preflight evaluated every guard and honored
`statement_timeout=5s` (commit `87f5610`).

---

## 3. What network reachability the actual OTA flash needs (greenhouse-LAN → ESP32)

OTA is the **espota2** TCP transport to the ESP32, NOT the native-API path:

- target: `ESP32_HOST=192.168.10.111`, `ESP32_OTA_PORT=3232`
  (`scripts/firmware-rollback.sh:17-18`; deploy uses `--device 192.168.10.111`,
  `Makefile:393` / `ESP32_DEVICE ?= 192.168.10.111`).
- This is **port 3232**, distinct from the ingestor's native-API **port 6053**.
- The OTA flash is **NOT issued from a k3s pod.** The
  `allow-ingestor-device-egress` NetworkPolicy (prod) and the commented `gated-§3.4`
  egress placeholder only concern the **ingestor's** `:6053` native-API egress
  (`deploy/k8s/base/networkpolicy.yaml:130-181`,
  `deploy/k8s/overlays/prod/allow-ingestor-device-egress.yaml`). **No k8s
  NetworkPolicy governs OTA** because the flash runs from the operator's tooling
  host, not a pod.

**So OTA reachability is a HOST-network fact, not a cluster fact:** the OTA tooling
host must sit on (or be routed to) the greenhouse device VLAN `192.168.10.0/24` and
reach `192.168.10.111:3232`. The cross-VLAN firewall allow that already exists for
the k3s node → ESP32 `:6053` (per `allow-ingestor-device-egress.yaml` header +
`docs/design/verdify-final-migration-2026-05-31.md:210`) does **not** automatically
cover `:3232` from an arbitrary tooling host — the operator runs OTA from a host with
device-LAN reachability (historically `.150`, now a re-homed toolchain host).

---

## 4. The sealed-Secret shape (in-repo artifact to author)

Mirror the existing `verdify-ha-token` device-affecting Secret pattern
(`deploy/k8s/overlays/prod/ha-token.placeholder.yaml`) and the `#30` two invariants
(`deploy/k8s/SECRETS.md`):

- **New secret name:** `verdify-firmware-ota`, key `ota_password`,
  `target.namespace: verdify-prod`. Kept **standalone** (NOT folded into
  `verdify-app-secrets`) for the same reason `verdify-esp32-psk` is standalone — so a
  bulk app-secret re-seal can never touch a device-affecting OTA credential.
- **prod-ONLY.** dev/staging are device-dark; they never flash. No dev/staging
  placeholder.
- **`config.kubernetes.io/local-config` as an ANNOTATION** (never a label) so
  `kustomize build` renders ZERO `kind: Secret` — the `#30` invariant that caught a
  real prod leak (`deploy/k8s/SECRETS.md:124-131`).
- The in-repo file is a **placeholder for `kustomize build`/`kubeconform` only**; the
  real ciphertext lives in the fleet registry
  (`agent-fleet-control/secrets/encrypted/verdify-firmware-ota.enc.yaml`) and is
  applied out-of-band by the GitOps `local-k8s-secret-sync` step.

Companion file authored alongside this doc:
`deploy/k8s/overlays/prod/firmware-ota-secret.placeholder.yaml` (lint-only stub).

The matching **fleet registry secret-meta** (authored in `agent-fleet-control`, NOT
this repo) should be:

```yaml
# agent-fleet-control/registry/secrets/verdify-firmware-ota.yaml  (NO VALUES)
id: verdify-firmware-ota
owner: jason                 # device-affecting; Jason confirms canonical value
target:
  namespace: verdify-prod    # MUST byte-match the overlay + Namespace + ArgoCD app
  name: verdify-firmware-ota
  keys:
    - k8s_key: ota_password
      from_source_key: ota_password
source:
  # The canonical OTA password source. Jason confirms which is authoritative:
  #   - the re-homed esphome secrets.yaml on the OTA toolchain host, OR
  #   - a value Jason supplies directly at seal time.
  # NEVER seal from a drifted copy (cf. the api_encryption_key df2784f9 drift).
  nas_path: <jason-confirmed source path>
  format: dotenv             # or yaml — matches the chosen source file
```

> **NOTE — `ota_password` is NOT in any dotenv today.** Unlike `ESP32_API_KEY`
> (which lives in `/srv/verdify/ingestor/.env`), the OTA password lived only in the
> esphome `secrets.yaml` on the now-powered-off `.150`. So the seal source is a
> **Jason decision** at seal time (re-homed `secrets.yaml` vs operator-supplied
> value), not an existing dotenv key. This is a SEAL-TIME input, not a repo change.

---

## 5. Operator runbook — sealing the OTA password (Jason-gated, device-affecting)

Each step is tagged with the ONLY owner who can perform it. Nothing here is run by
the agent. This is the small, reviewed sequence the in-repo artifacts make turnkey.

### Preconditions (already in-repo or upstream-merged)
- `deploy/k8s/overlays/prod/firmware-ota-secret.placeholder.yaml` merged (this PR) so
  `kustomize build overlays/prod` stays complete + lint-able (renders ZERO Secrets).
- The fleet registry secret-meta `verdify-firmware-ota.yaml` merged in
  `agent-fleet-control` (James/laptop-root review; NO value).
- The `local-k8s-secret-sync` workflow `target` enum extended with a
  `verdify-prod`/`verdify-firmware-ota` arm (laptop-root — same arm work the cutover
  doc §2.3 calls out for the new namespace).

### Step A — Jason confirms the canonical OTA password `[GATE: Jason]`
The OTA password value lived only on `.150`'s esphome `secrets.yaml`. Jason confirms
the authoritative value and its source (re-homed `secrets.yaml` or a value he
supplies). **Do NOT seal a drifted copy** — the `api_encryption_key` drift (sha
`df2784f9` vs canonical `127f85d0`) is the cautionary precedent.
Confirm: rotate-at-seal vs carry-existing. **A rotation requires a re-flash**
(the device's `ota:` password is compiled in), which is a full firmware-deploy under
the freeze rules — so the default is **carry-existing** (seal the value the live
device already accepts; no re-flash).

### Step B — laptop-root seals the value `[GATE: laptop-root]`
From the `agent-fleet-control` repo root, pipe-only (value never on the CLI), same
shape as `verdify-secret-sealing-plan.md §2`:

```bash
scripts/seal-secret.sh verdify-firmware-ota \
    --remote jason@vm-docker-iris.servers.vallery.net   # or the re-homed source host
# -> commits ONLY ciphertext secrets/encrypted/verdify-firmware-ota.enc.yaml
```
Seal by **explicit id** — never `--all` (it would attempt the device-affecting
credential alongside blocked app keys).

### Step C — laptop-root syncs the Secret into the cluster `[GATE: laptop-root]`
Run the protected `local-k8s-secret-sync` workflow for the `verdify-prod` /
`verdify-firmware-ota` arm. It SOPS-decrypts on the self-hosted runner and
`kubectl apply`s the namespaced Secret. Verify:
`kubectl -n verdify-prod get secret verdify-firmware-ota -o jsonpath='{.data.ota_password}' | base64 -d | wc -c`
returns a non-zero length (length only — never echo the value).

### Step D — operator feeds `OTA_PW` at deploy time `[GATE: Jason / operator]`
On the OTA tooling host (greenhouse-LAN + `kubectl` reach), right before a deploy:

```bash
export OTA_PW="$(<VERDIFY_KUBECTL> -n verdify-prod get secret verdify-firmware-ota \
    -o jsonpath='{.data.ota_password}' | base64 -d)"
# <VERDIFY_KUBECTL> may be `kubectl` or `ssh jason@192.168.30.32 sudo k3s kubectl`
make firmware-deploy        # preflight (kube DB backend) + compile + upload
#   firmware-rollback.sh consumes the same OTA_PW on auto-rollback
```
`firmware-rollback.sh` prefers `OTA_PW` and only falls back to parsing a local
`secrets.yaml` (`scripts/firmware-rollback.sh:24,45-60`) — so once `OTA_PW` is fed,
the dead `.150` `secrets.yaml` is no longer on the critical path for rollback.

### Step E — re-home the OTA toolchain (separate gate, see §6)
`make firmware-deploy` ALSO needs the ESPHome toolchain (`ESPHOME_BIN`,
`SECRETS_SRC` for the *build-time* wifi/api keys) — that is a host re-home, distinct
from the OTA-password seal. See §6.

---

## 6. The OTA toolchain re-home (the OTHER half of the landing zone)

Sealing `ota_password` is necessary but NOT sufficient to deploy firmware. The
`.150` VM also held the ESPHome toolchain + build-time secrets. Two host
dependencies, both gated on a re-homed tooling host:

| Dependency | Hard-coded default | Re-home env knob | Used by |
|---|---|---|---|
| `esphome` binary | `/srv/greenhouse/.venv/bin/esphome` | `ESPHOME_BIN` (`firmware-esphome-worktree.sh:15`, `stage-rollback-floor-refresh.sh:41`) | compile + `upload` |
| esphome `secrets.yaml` (build-time `wifi_*`, `api_encryption_key`) | `/srv/greenhouse/esphome/secrets.yaml` | `SECRETS_SRC` (`firmware-esphome-worktree.sh:16`) | compile (symlinked to `firmware/secrets.yaml`) |
| esphome-capable python (espota2) | `/srv/greenhouse/.venv/bin/python` → `python3` | `FIRMWARE_PYTHON` (`firmware-rollback.sh:70-77`) | OTA rollback flash |
| OTA password | legacy `secrets.yaml` `ota_password` | `OTA_PW` (this doc, §5) | OTA flash + rollback |

The rollback-floor itself is stale and `.150`-bound — `#256` staged the refresh
(`scripts/stage-rollback-floor-refresh.sh`), but its `PROMOTE=1` path is GATED on
exactly this toolchain re-home (it needs `ESPHOME_BIN` + `SECRETS_SRC` to recompile
the floor build; it dry-runs and reports both gates otherwise).

**Re-home options (Jason / laptop-root decision — upstream of this repo):**
1. **A re-homed tooling VM/host** with device-LAN reachability + the esphome venv +
   `secrets.yaml`, run `make firmware-deploy` there with `OTA_PW` fed from the
   sealed Secret (§5). Lowest-change: the existing scripts work unchanged via the
   `ESPHOME_BIN`/`SECRETS_SRC`/`FIRMWARE_PYTHON`/`OTA_PW` env knobs.
2. **A one-shot in-cluster OTA Job** scheduled onto a node with device-LAN
   reachability, mounting `verdify-firmware-ota` + a build-time esphome secret, with
   an egress NetworkPolicy to `192.168.10.111:3232`. This is a BIGGER change (new
   image with the esphome toolchain, a new egress allow, an OTA-from-pod posture the
   cutover doc deliberately avoided) and should NOT be the first move — it is a
   future option, explicitly out of scope here. The freeze rule "CI never
   flashes/OTAs" still holds (a Job is not CI, but it is the same "no automated
   flash" spirit — it stays human-triggered + Jason-gated).

---

## 7. Self-serviceable vs upstream-required (classification)

**Self-serviceable (in-repo, this agent can author + push):**
- This runbook (`docs/runbooks/firmware-ota-secret-sealing.md`) — SHARED TERRITORY,
  coordinator-reviewed, but authorable.
- The placeholder Secret stub
  `deploy/k8s/overlays/prod/firmware-ota-secret.placeholder.yaml` + its
  `kustomization.yaml` `resources:` line — SHARED TERRITORY, coordinator-reviewed.
- An update to `deploy/k8s/SECRETS.md` adding the `verdify-firmware-ota` row.

**Upstream-required (human / cluster-admin / Jason grant — cannot self-serve):**
- The **actual OTA password value** + the canonical-source decision + rotate-vs-carry
  + the confirmation that no re-flash happens — **Jason** (device-affecting).
- Running `seal-secret.sh` + `local-k8s-secret-sync` on the protected runner —
  **laptop-root**.
- The fleet registry secret-meta `verdify-firmware-ota.yaml` — **James/laptop-root**
  (it lives in `agent-fleet-control`, a different repo).
- The OTA **toolchain re-home host** (esphome venv + build-time `secrets.yaml` +
  device-LAN reachability) — **Jason/laptop-root**.
- The `local-k8s-secret-sync` `target` enum arm for `verdify-prod` — **laptop-root**.

---

## 8. What this doc does NOT do
- No secret value read/echoed/sealed. No `seal-secret.sh` / `kubectl` / sync run.
- No device touch: no OTA, no flash, no re-flash, no setpoint, no native-API session.
- No firewall/router/VLAN change. No NetworkPolicy edit.
- No firmware OTA path added to CI or to the k3s cutover. OTA stays the operator-host,
  human-gated `make firmware-deploy` path (freeze rule #7 / DoD #7).
