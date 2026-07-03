# Vision / Observation Pipeline — Revival Runbook

**Status (2026-07-03):** the greenhouse "eyes" went dark **2026-06-07** and stayed
dark ~26 days undetected. Root cause + the (now small) remaining step are below.
A watchdog (`system.vision_pipeline` sensor_offline alert, `ingestor/tasks/alerts.py`)
now pages within 24 h if observations stop, so this can't recur silently.

## What the pipeline is

Two scripts, historically run by a **laptop/iris-VM cron** (never a k3s CronJob):

1. `scripts/frigate-snapshot.py` — pulls `greenhouse_1`/`greenhouse_2` frames from
   Frigate → writes a snapshot dir (was the Syncthing vault `/mnt/iris/...`).
2. `scripts/analyze-greenhouse-snapshot.py` — feeds each snapshot to the vision
   model (Gemini `gemini-3.1-pro-preview`, `config/ai.yaml`) with zone/crop/sensor
   context → writes `image_observations` + `observations` (health_score,
   stress_tags, root_condition, flowering_count, …).

## Why it died

The iris-VM was decommissioned, taking the snapshot vault path
(`/mnt/iris/verdify-vault/snapshots`) and the laptop cron with it. The scripts
also hard-coded the old operator-LAN Frigate host `192.168.30.142` (now offline —
100 % packet loss) and legacy `/srv/verdify` paths.

## What is already fixed / confirmed (2026-07-03)

- **Frigate runs in k3s now:** svc `frigate.frigate.svc.cluster.local:5000`
  (ns `frigate`, v0.17.1). Reachable from `verdify-prod` pods; `greenhouse_1`
  (center Vandas, ~298 KB) and `greenhouse_2` frames pull live. The old `.142`
  host is retired.
- **Vision libs are already in the ingestor image** (`google.genai`, `openai`).
- **Scripts are now k3s-portable** (env overrides, backward-compatible defaults):
  - `VERDIFY_FRIGATE_URL` (default the retired `.142`; set to the in-cluster svc)
  - `VERDIFY_GO2RTC_PUBLIC_BASE_URL`
  - `VERDIFY_SNAPSHOT_DIR` (point at an emptyDir/PVC shared by both steps)
  - `VERDIFY_ZONES_CONFIG` (repo `config/zones.yaml`)
  - `VERDIFY_DB_DSN` (in-cluster `verdify-db`)

## The one remaining blocker: the vision API key

`ai_config.api_key()` reads the key from a **file**
(`/mnt/agents/shared/credentials/gemini_api_key.txt`). That path is **not mounted**
in `verdify-prod` (only `verdify-ha-token` → `ha_token.txt` is). No Gemini/Google
key secret exists in the namespace. `verdify-app-secrets` has an `OPENAI_API_KEY`
but ai_config wants a file and vision is configured for Gemini.

**Pick one (operator / credential gate — Jason):**

- **(A, matches config) Provision the Gemini key.** Create a secret and mount it at
  the path ai_config expects:
  ```bash
  kubectl -n verdify-prod create secret generic verdify-vision-key \
    --from-file=gemini_api_key.txt=<path-to-gemini-key>
  ```
  Mount it at `/mnt/agents/shared/credentials/gemini_api_key.txt` in the CronJob.
- **(B, reuse existing creds) Switch vision to OpenAI.** Set `config/ai.yaml`
  `models.vision.provider: openai` + a vision-capable model, mount
  `verdify-app-secrets/OPENAI_API_KEY` as the `keys.openai.file` path. Needs a
  prompt/parse check (`templates/vision-analysis.j2`) — OpenAI structured output
  differs from Gemini's.

## Revival (once the key is provisioned)

Run capture→analyze on a schedule. Reuse the **ingestor image** (has the libs +
the scripts at `/app/scripts`, read-only) — no new image build required. CronJob
sketch (verify against `deploy/k8s/components/grafana/band-curve-refresh-cronjob.yaml`
for the current image pin / env / DB-DSN wiring before applying):

```yaml
apiVersion: batch/v1
kind: CronJob
metadata: { name: verdify-vision, namespace: verdify-prod }
spec:
  schedule: "0 6,11,15,19 * * *"   # 4x/day, MDT-ish (was the laptop cadence)
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: vision
              image: <ingestor image pin>            # has google.genai + scripts
              command: ["sh","-c"]
              args:
                - |
                  cd /app/ingestor &&
                  python3 /app/scripts/frigate-snapshot.py &&
                  python3 /app/scripts/analyze-greenhouse-snapshot.py
              env:
                - { name: VERDIFY_FRIGATE_URL, value: "http://frigate.frigate.svc.cluster.local:5000" }
                - { name: VERDIFY_GO2RTC_PUBLIC_BASE_URL, value: "http://frigate.frigate.svc.cluster.local:1984" }
                - { name: VERDIFY_SNAPSHOT_DIR, value: "/snap" }
                - { name: VERDIFY_ZONES_CONFIG, value: "/app/config/zones.yaml" }
                - { name: VERDIFY_DB_DSN, valueFrom: { secretKeyRef: { name: verdify-app-secrets, key: DATABASE_URL } } }
              volumeMounts:
                - { name: snap, mountPath: /snap }
                - { name: viskey, mountPath: /mnt/agents/shared/credentials, readOnly: true }
          volumes:
            - { name: snap, emptyDir: {} }
            - { name: viskey, secret: { secretName: verdify-vision-key } }
```

**Verify:** `SELECT max(ts) FROM image_observations;` advances; the
`system.vision_pipeline` watchdog alert auto-resolves; fresh Vanda health rows land.

Do **not** wire this into the ArgoCD-synced overlay until the key secret exists —
otherwise the CronJob pods crash-loop on the missing key. Apply it manually first,
confirm one green run, then commit into `overlays/prod`.
