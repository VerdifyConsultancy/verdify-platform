"""Credential-free private-CI outbox; live delivery requires an attended relay.

The default records two synthetic messages and never opens a network socket.
Only ROOT's separately authorized relay has access to the existing sender.
"""

import hashlib
import json
import socket
import subprocess
import time
from pathlib import Path

CHANNEL = "C0ANVVAPLD6"
EXPERIMENT_ID = "58763900-0000-4000-8000-000000000001"
PREFIX = "[NONPRODUCTION SYNTHETIC QUALIFICATION] #587 private fixture 2026-10-25: "


class FixtureDelivery:
    def __init__(self, root: Path):
        self.root = root
        self.directory = root / ".component-alert-delivery"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        authorization = self.directory / "authorization.json"
        self.authorization = json.loads(authorization.read_text()) if authorization.is_file() else None
        self.prefix = PREFIX
        self.messages = []
        self.receipts = []
        if self.authorization:
            assert self.authorization == {
                "source": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
                "pod_name": socket.gethostname(),
                "condition": "facility_emergency_hold",
                "channel": CHANNEL,
                "message_budget": 2,
                "approved": True,
            }, "live relay scope mismatch"

    def __call__(self, channel, text, *, thread_ts=None):
        ordinal = len(self.messages) + 1
        assert ordinal <= 2 and channel == CHANNEL and text.startswith(PREFIX)
        assert EXPERIMENT_ID in text and len(text.encode()) <= 8192
        if ordinal == 1:
            assert thread_ts is None and "facility emergency hold" in text and "*[CRITICAL]*" in text
        else:
            assert thread_ts == self.receipts[0] and "Resolved:" in text
        payload = {"channel": channel, "text": text, "thread_ts": thread_ts}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        request = {"ordinal": ordinal, "payload": payload, "payload_sha256": digest}
        self.messages.append(payload)
        (self.directory / f"request-{ordinal}.json").write_text(json.dumps(request, sort_keys=True) + "\n")
        print("COMPONENT_ALERT_DELIVERY_REQUEST " + json.dumps(request, sort_keys=True), flush=True)
        if self.authorization:
            deadline = time.monotonic() + 90
            reply_path = self.directory / f"reply-{ordinal}.json"
            while not reply_path.is_file():
                if time.monotonic() >= deadline:
                    raise TimeoutError("live delivery outcome unknown; do not resend")
                time.sleep(0.25)
            reply = json.loads(reply_path.read_text())
            assert reply["payload_sha256"] == digest and reply["confirmed"] is True
            receipt = reply["slack_ts"]
            assert isinstance(receipt, str) and receipt.replace(".", "", 1).isdigit()
        else:
            receipt = "0.587639"  # Explicit capture-only placeholder, never a live receipt.
        self.receipts.append(receipt)
        return receipt

    def report(self):
        return {"live": self.authorization is not None, "messages": self.messages, "receipts": self.receipts}
