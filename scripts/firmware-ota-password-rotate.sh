#!/usr/bin/env bash
# Rotate the controller's OTA password (#1009) without ever printing it.
#
# Jason runs this: it reads and writes Secret material, which agent sessions
# must not. The Secret verdify-prod/verdify-firmware-ota stays out-of-band
# (its move to the agents KSOPS lane is agents#4470); this changes only its
# keys, through write-only patches from a private temp file.
#
#   stage    prompt for the new password (hidden, twice) or --generate one, and
#            add it as key ota_password_next. The device still runs ota_password.
#   promote  after the rotated firmware is flashed and pinned: ota_password takes
#            the ota_password_next value, the old value moves to
#            ota_password_previous (a rollback to a pre-rotation image needs it
#            for every later flash), and ota_password_next is removed, in one
#            JSON patch.
#   forget   once no pre-rotation image can run again (a rotated release is
#            last-good): remove ota_password_previous.
#   status   key names, and whether ota_password_next differs from ota_password.
#
# Between stage and promote the build uses ota_password_next and every flash
# Job uses the key the device runs now (docs/runbooks/firmware-signed-artifacts.md,
# "OTA password rotation"):
#   firmware-release-job.py build --ota-key ota_password_next ...
#   firmware-release-job.py flash ...                         # old image running
#   firmware-release-job.py flash --ota-key ota_password_next # rotated image running (rollback)
# After promote, a pre-rotation image (a rollback) answers to ota_password_previous.
#
# Usage: scripts/firmware-ota-password-rotate.sh stage [--generate] [--replace]
#        scripts/firmware-ota-password-rotate.sh promote
#        scripts/firmware-ota-password-rotate.sh forget
#        scripts/firmware-ota-password-rotate.sh status
set -euo pipefail

CONTEXT="${KUBE_CONTEXT:-vallery}"
NS=verdify-prod
SECRET=verdify-firmware-ota
k() { kubectl --context "$CONTEXT" -n "$NS" "$@"; }

mode="${1:-}"
shift || true
generate=0
replace=0
for arg in "$@"; do
    case "$arg" in
        --generate) generate=1 ;;
        --replace) replace=1 ;;
        *) echo "unknown option: $arg" >&2; exit 64 ;;
    esac
done

umask 077
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# Value-blind report of the live Secret: key names and comparisons only.
report() {
    k get secret "$SECRET" -o json > "$work/live.json"
    python3 - "$work/live.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1])).get("data", {})
out = {"keys": sorted(data)}
if "ota_password_next" in data:
    out["next_differs_from_current"] = data["ota_password_next"] != data.get("ota_password")
print(json.dumps(out))
PY
}

case "$mode" in
status)
    report
    ;;
stage)
    k get secret "$SECRET" -o json > "$work/live.json"
    if [[ "$replace" != 1 ]] && python3 -c 'import json,sys; sys.exit(0 if "ota_password_next" in json.load(open(sys.argv[1])).get("data", {}) else 1)' "$work/live.json"; then
        echo "ota_password_next already staged; promote it first, or re-run with --replace" >&2
        exit 1
    fi
    if [[ "$generate" == 1 ]]; then
        python3 -c 'import secrets; print(secrets.token_urlsafe(24), end="")' > "$work/new"
    else
        IFS= read -rs -p "New OTA password: " first; echo
        IFS= read -rs -p "Again: " second; echo
        [[ "$first" == "$second" ]] || { echo "the two entries differ" >&2; exit 1; }
        printf '%s' "$first" > "$work/new"
        unset first second
    fi
    python3 - "$work/live.json" "$work/new" "$work/patch.json" <<'PY'
import base64, json, sys
data = json.load(open(sys.argv[1])).get("data", {})
new = open(sys.argv[2], "rb").read()
if len(new) < 16 or any(b < 0x21 or b > 0x7E for b in new):
    sys.exit("refused: use at least 16 printable ASCII characters and no spaces")
if base64.b64encode(new).decode() == data.get("ota_password"):
    sys.exit("refused: the new password equals the current one")
json.dump({"data": {"ota_password_next": base64.b64encode(new).decode()}}, open(sys.argv[3], "w"))
PY
    k patch secret "$SECRET" --type=merge --patch-file "$work/patch.json" >/dev/null
    k get secret "$SECRET" -o json > "$work/after.json"
    python3 - "$work/after.json" "$work/new" <<'PY'
import base64, json, sys
data = json.load(open(sys.argv[1]))["data"]
ok = data.get("ota_password_next") == base64.b64encode(open(sys.argv[2], "rb").read()).decode()
print(json.dumps({"keys": sorted(data), "ota_password_next_reads_back": ok}))
sys.exit(0 if ok else 1)
PY
    ;;
promote)
    k get secret "$SECRET" -o json > "$work/live.json"
    python3 - "$work/live.json" "$work/patch.json" "$work/next.sha" <<'PY'
import hashlib, json, sys
data = json.load(open(sys.argv[1])).get("data", {})
nxt = data.get("ota_password_next")
if not nxt:
    sys.exit("refused: no ota_password_next to promote")
if nxt == data.get("ota_password"):
    sys.exit("refused: ota_password_next equals ota_password")
cur = data["ota_password"]
json.dump(
    [
        {"op": "test", "path": "/data/ota_password_next", "value": nxt},
        {"op": "test", "path": "/data/ota_password", "value": cur},
        {"op": "add", "path": "/data/ota_password_previous", "value": cur},
        {"op": "replace", "path": "/data/ota_password", "value": nxt},
        {"op": "remove", "path": "/data/ota_password_next"},
    ],
    open(sys.argv[2], "w"),
)
open(sys.argv[3], "w").write(hashlib.sha256(nxt.encode()).hexdigest() + " " + hashlib.sha256(cur.encode()).hexdigest())
PY
    k patch secret "$SECRET" --type=json --patch-file "$work/patch.json" >/dev/null
    k get secret "$SECRET" -o json > "$work/after.json"
    python3 - "$work/after.json" "$work/next.sha" <<'PY'
import hashlib, json, sys
data = json.load(open(sys.argv[1]))["data"]
nxt, cur = open(sys.argv[2]).read().split()
h = lambda key: hashlib.sha256(data.get(key, "").encode()).hexdigest()
ok = sorted(data) == ["ota_password", "ota_password_previous"] and h("ota_password") == nxt and h("ota_password_previous") == cur
print(json.dumps({"keys": sorted(data), "ota_password_is_the_promoted_value": ok}))
sys.exit(0 if ok else 1)
PY
    ;;
forget)
    k get secret "$SECRET" -o json > "$work/live.json"
    python3 - "$work/live.json" "$work/patch.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1])).get("data", {})
if "ota_password_previous" not in data:
    sys.exit("refused: no ota_password_previous to forget")
if "ota_password_next" in data:
    sys.exit("refused: a rotation is staged; promote it first")
json.dump([{"op": "remove", "path": "/data/ota_password_previous"}], open(sys.argv[2], "w"))
PY
    k patch secret "$SECRET" --type=json --patch-file "$work/patch.json" >/dev/null
    report
    ;;
*)
    sed -n '2,25p' "$0" >&2
    exit 64
    ;;
esac
