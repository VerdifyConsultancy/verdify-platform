"""Attended operator IPC only: invoke only after Jason approves this exact pair.

Usage: python attended-relay.py <40-char-source> <native-ci-pod-name> <verified-sender-uid>
No credentials leave the existing ingestor. Never rerun an uncertain send.
"""

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
SOURCE, POD, SENDER_UID = sys.argv[1:]
assert re.fullmatch("[0-9a-f]{40}", SOURCE)
assert re.fullmatch(r"[0-9a-f-]{36}", SENDER_UID)
K = ["kubectl", "--context", "vallery", "--request-timeout=15s"]
CHANNEL = "C0ANVVAPLD6"
EXPECTED = json.loads((OUT / "reviewable-alert-messages.json").read_text())["messages"]
STATE_PATH = OUT / "live-delivery-receipt.json"
assert not STATE_PATH.exists(), "Existing attempt: do not retry or resend"
STATE = {"source": SOURCE, "ci_pod": POD, "started_at": datetime.datetime.now(datetime.UTC).isoformat(), "events": []}


def call(args, body=None, timeout=20):
    return subprocess.check_output(K + args, input=body, text=True, timeout=timeout)


def pod(namespace, name):
    return json.loads(call(["-n", namespace, "get", "pod", name, "-o", "json"]))


def save():
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(STATE, indent=2) + "\n")
    os.replace(temporary, STATE_PATH)


ci = pod("agent-fleet-ci", POD)
STATE["ci_pod_uid"] = ci["metadata"]["uid"]
assert ci["status"]["phase"] == "Running"
assert (
    call(
        ["-n", "agent-fleet-ci", "exec", POD, "-c", "main", "--", "git", "-C", "/workspace/repo", "rev-parse", "HEAD"]
    ).strip()
    == SOURCE
)
pods = json.loads(
    call(["-n", "verdify-prod", "get", "pods", "-l", "app.kubernetes.io/component=ingestor", "-o", "json"])
)["items"]
assert len(pods) == 1
writer = pods[0]
assert writer["metadata"]["uid"] == SENDER_UID
STATE["sender_pod"] = writer["metadata"]["name"]
STATE["sender_uid"] = writer["metadata"]["uid"]
save()


def ci_exec(code, *args):
    assert pod("agent-fleet-ci", POD)["metadata"]["uid"] == STATE["ci_pod_uid"]
    return call(["-n", "agent-fleet-ci", "exec", "-i", POD, "-c", "main", "--", "python", "-c", code, *args])


def write_ci(name, value):
    assert re.fullmatch(r"(authorization|reply-[12])\.json", name)
    ci_exec(
        "import pathlib,json,sys,os; p=pathlib.Path('/workspace/repo/.component-alert-delivery');p.mkdir(mode=0o700,exist_ok=True); t=p/(sys.argv[1]+'.tmp');t.write_text(sys.argv[2]);os.replace(t,p/sys.argv[1])",
        name,
        json.dumps(value),
    )


write_ci(
    "authorization.json",
    {
        "source": SOURCE,
        "pod_name": POD,
        "condition": "facility_emergency_hold",
        "channel": CHANNEL,
        "message_budget": 2,
        "approved": True,
    },
)
# This small subprocess imports only the existing sender module. It never starts
# ingestor.py or constructs an ESPHome client; it returns only safe result fields.
SENDER = """import sys,json
from tasks._common import _post_slack,_load_token,SLACK_TOKEN_FILE,SLACK_CHANNEL
p=json.loads(sys.argv[1]); assert p['channel']==SLACK_CHANNEL=='C0ANVVAPLD6'
try:
 t=_post_slack(_load_token(SLACK_TOKEN_FILE),p['channel'],p['text'],thread_ts=p['thread_ts'])
 print(json.dumps({'confirmed':bool(t),'slack_ts':t}))
except Exception as e: print(json.dumps({'confirmed':False,'failure_type':type(e).__name__}))
"""
try:
    deadline = time.monotonic() + 540
    for ordinal in [1, 2]:
        request = None
        while request is None and time.monotonic() < deadline:
            raw = ci_exec(
                "import pathlib,sys; p=pathlib.Path('/workspace/repo/.component-alert-delivery')/sys.argv[1];print(p.read_text()if p.is_file()else'null')",
                f"request-{ordinal}.json",
            )
            request = json.loads(raw)
            if request is None:
                time.sleep(3)
        assert request is not None, "No request before deadline; do not retry"
        payload = request["payload"]
        expected = dict(EXPECTED[ordinal - 1])
        if ordinal == 2:
            expected["thread_ts"] = STATE["events"][0]["slack_ts"]
        assert request["ordinal"] == ordinal and payload == expected
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert digest == request["payload_sha256"]
        assert pod("verdify-prod", STATE["sender_pod"])["metadata"]["uid"] == STATE["sender_uid"]
        event = {"ordinal": ordinal, "payload": payload, "payload_sha256": digest, "outcome": "attempting"}
        STATE["events"].append(event)
        save()  # Durable intent BEFORE the one and only call.
        result = json.loads(
            call(
                [
                    "-n",
                    "verdify-prod",
                    "exec",
                    STATE["sender_pod"],
                    "-c",
                    "ingestor",
                    "--",
                    "python",
                    "-c",
                    SENDER,
                    json.dumps(payload),
                ]
            )
        )
        event.update(result)
        event["outcome"] = "confirmed" if result.get("confirmed") else "uncertain_do_not_resend"
        save()
        assert result.get("confirmed") and re.fullmatch(r"\d+\.\d+", result["slack_ts"])
        write_ci(f"reply-{ordinal}.json", {"confirmed": True, "payload_sha256": digest, "slack_ts": result["slack_ts"]})
        event["reply_delivered_at"] = datetime.datetime.now(datetime.UTC).isoformat()
        save()
        print(json.dumps({"ordinal": ordinal, "confirmed": True, "reply_delivered": True}), flush=True)
    STATE["bridge_complete"] = True
except BaseException as error:
    STATE["failure_type"] = type(error).__name__
    STATE["automatic_retry_permitted"] = False
    raise
finally:
    STATE["finished_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    save()
