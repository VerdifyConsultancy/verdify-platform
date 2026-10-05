#!/usr/bin/env python3
"""Restore the existing Viewer probe identity into an OFFLINE Grafana12.4 SQLite copy.

This recovers a lost account; it never creates a new credential. Run against a
private backup/copy before seeding verdify-grafana-data. No secret is printed.
Hash algorithm: Grafana v12.4.5 pkg/components/satokengen/tokengen.go and
pkg/util/encoding.go. Preserve the original database separately for rollback.
"""

import argparse
import base64
import hashlib
import json
import sqlite3
import struct
import subprocess
import zlib


def token_hash(token):
    prefix, secret, checksum = token.split("_")
    if prefix != "glsa" or len(secret) != 32:
        raise ValueError("Unsupported existing service-account token")
    expected = struct.pack("<I", zlib.crc32((prefix + "_" + secret).encode())).hex()
    if checksum != expected:
        raise ValueError("Existing token checksum mismatch")
    return hashlib.pbkdf2_hmac("sha256", secret.encode(), checksum.encode(), 10000, 50).hex()


def restore(database, profile):
    identity = profile["identity"]
    if identity["role"] != "Viewer" or identity["org_id"] != 1:
        raise ValueError("Unexpected monitoring privilege scope")
    auth = profile["headers"]["Authorization"]
    if not auth.startswith("Bearer "):
        raise ValueError("Expected existing bearer identity")
    hashed = token_hash(auth.removeprefix("Bearer "))
    account_id, token_id = identity["account_id"], identity["token_id"]
    with sqlite3.connect(database) as db:
        if db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Invalid database copy")
        existing = db.execute(
            "SELECT is_service_account,is_admin,org_id FROM user WHERE id=?", (account_id,)
        ).fetchone()
        if existing and existing != (1, 0, 1):
            raise ValueError("Existing user ID has a different authority")
        if not existing:
            db.execute(
                "INSERT INTO user (id,version,login,email,name,org_id,is_admin,created,updated,is_service_account,uid) VALUES (?,0,?,'',?,1,0,datetime('now'),datetime('now'),1,?)",
                (account_id, "sa-endpoint-monitor", "Endpoint monitor", "endpoint-monitor"),
            )
        membership = db.execute("SELECT role FROM org_user WHERE org_id=1 AND user_id=?", (account_id,)).fetchone()
        if membership and membership != ("Viewer",):
            raise ValueError("Existing account is broader than Viewer")
        if not membership:
            db.execute(
                "INSERT INTO org_user (org_id,user_id,role,created,updated) VALUES (1,?,'Viewer',datetime('now'),datetime('now'))",
                (account_id,),
            )
        existing_token = db.execute(
            "SELECT key,service_account_id,role,org_id,is_revoked FROM api_key WHERE id=?", (token_id,)
        ).fetchone()
        expected_token = (hashed, account_id, "Viewer", 1, 0)
        if existing_token and existing_token != expected_token:
            raise ValueError("Existing token ID differs; refusing replacement")
        if not existing_token:
            db.execute(
                "INSERT INTO api_key (id,org_id,name,key,role,created,updated,service_account_id,is_revoked) VALUES (?,1,'endpoint-monitor',?,'Viewer',datetime('now'),datetime('now'),?,0)",
                (token_id, hashed, account_id),
            )
    return {"account_id": account_id, "token_id": token_id, "role": "Viewer", "credential_preserved": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Offline private SQLite copy")
    args = parser.parse_args()
    raw = subprocess.check_output(
        [
            "kubectl",
            "--context",
            "vallery",
            "-n",
            "observability",
            "get",
            "secret",
            "endpoint-prober-auth",
            "-o",
            "json",
        ]
    )
    secret = json.loads(raw)
    profiles = json.loads(base64.b64decode(secret["data"]["auth-profiles.json"]))
    result = restore(args.database, profiles["profiles"]["service-verdify-grafana"])
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
