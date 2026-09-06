"""Read-only, source-pinned explanation of ordinary-login attestation changes.

This tool does not approve a transition, refresh a receipt, connect to a DB, or
grant permissions. Execute emitted SQL only against an authorized target. The
result contains catalog entry hashes and object names, not definitions/GUCs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "db/migrations/217-runtime-role-boundary.sql"
SOURCE_SHA256 = "15369af1c28692addc2d0d758dcbc4efb25be549aade3ce3690a0f29d565522f"
VERSION = "ordinary-boundary-catalog-diff-v1"
LOGINS = ("verdify_api_runtime_login", "verdify_ingestor_runtime_login")
CATEGORIES = {
    "role",
    "member",
    "database",
    "schema",
    "relation",
    "sequence",
    "function",
    "internal-function",
    "trigger-function",
    "trigger",
    "rule",
    "policy",
    "default",
}


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def emit_sql(login, raw_source=None):
    require(login in LOGINS, "unsupported login")
    raw = SOURCE.read_bytes() if raw_source is None else raw_source
    require(sha256(raw) == SOURCE_SHA256, "unreviewed digest source")
    source = raw.decode()
    start = source.index("CREATE OR REPLACE FUNCTION public.fn_runtime_ordinary_boundary_digest(")
    stop = source.index("CREATE OR REPLACE FUNCTION public.fn_runtime_attest_ordinary_login()", start)
    function = source[start:stop]
    body = function.split("AS $body$", 1)[1].split("$body$;", 1)[0]
    declarations = function[: function.index("\nBEGIN\n")]
    cte = function[
        function.index("WITH security_entries(entry) AS (") : function.index(
            "\n    SELECT pg_catalog.string_agg(entry, E'\\n' ORDER BY entry)"
        )
    ]
    variables = {
        "p_login_name": f"'{login}'",
    }
    for name in ("v_protected_relations", "v_protected_sequences", "v_internal_callees", "v_invoker_helper_closure"):
        match = re.search(rf"\b{name} (?:text|regprocedure)\[\] := (ARRAY\[.*?\]);", declarations, re.S)
        require(match is not None, "missing source declaration")
        variables[name] = f"({match.group(1)})"
    # The entire source is pinned above; substitutions are fixed identifiers and
    # one of two literal login names, never arbitrary SQL or external file text.
    for name, literal in variables.items():
        cte, count = re.subn(rf"\b{name}\b", lambda _: literal, cte)
        require(count > 0, "missing source variable use")
    require(
        not re.search(r"\bv_(?:protected|internal|invoker|duty)\w*\b|\bp_login_name\b", cte),
        "unresolved source variable",
    )
    body_sha = sha256(body.encode())
    return f"""-- {VERSION}; exact migration-217 source SHA256 {SOURCE_SHA256}
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
-- Match the definer's path: PostgreSQL deparsers otherwise qualify names differently.
SET LOCAL search_path = pg_catalog, pg_temp;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '2s';
{cte}, projection AS (
    SELECT coalesce(string_agg(entry, E'\\n' ORDER BY entry), '') AS preimage,
           coalesce(jsonb_agg(jsonb_build_object(
               'category', split_part(entry, '|', 1),
               'object', split_part(entry, '|', 2),
               'sha256', encode(public.digest(entry, 'sha256'), 'hex'))
               ORDER BY entry), '[]'::jsonb) AS entries
    FROM security_entries
), installed AS (
    SELECT coalesce(bool_and(
        encode(public.digest(p.prosrc, 'sha256'), 'hex') = '{body_sha}'
        AND p.prosecdef AND l.lanname = 'plpgsql'
        AND p.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]
        AND p.proowner = (SELECT datdba FROM pg_database WHERE datname=current_database())
    ), false) AS verified
    FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
    WHERE p.oid='public.fn_runtime_ordinary_boundary_digest(text)'::regprocedure
), current_digest AS (
    SELECT CASE WHEN verified THEN public.fn_runtime_ordinary_boundary_digest('{login}') END AS value,
           verified FROM installed
)
SELECT jsonb_build_object(
    'contract_version', '{VERSION}', 'source_sha256', '{SOURCE_SHA256}',
    'login', '{login}', 'database', current_database(),
    'server_version_num', current_setting('server_version_num')::integer,
    'installed_source_verified', current_digest.verified,
    'projection_matches_installed_digest',
        coalesce(public.digest(projection.preimage, 'sha256')=current_digest.value, false),
    'current_digest', encode(current_digest.value, 'hex'),
    'stored_digest', (SELECT encode(boundary_sha256, 'hex')
        FROM public.runtime_ordinary_login_attestation_receipts WHERE login_name='{login}'),
    'entries', projection.entries)
FROM projection CROSS JOIN current_digest;
COMMIT;
"""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def read_snapshot(path):
    require(not path.is_symlink() and path.is_file(), "snapshot must be a regular non-symlink file")
    with path.open("rb") as stream:
        raw = stream.read(8_000_001)
    require(len(raw) <= 8_000_000, "snapshot exceeds bound")
    try:
        result = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid snapshot JSON") from None
    return result, sha256(raw)


def _valid_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate(snapshot):
    require(isinstance(snapshot, dict), "snapshot must be an object")
    require(
        snapshot.get("contract_version") == VERSION and snapshot.get("source_sha256") == SOURCE_SHA256,
        "wrong source contract",
    )
    require(snapshot.get("login") in LOGINS, "unsupported snapshot login")
    require(snapshot.get("installed_source_verified") is True, "installed digest source unverified")
    require(snapshot.get("projection_matches_installed_digest") is True, "catalog projection mismatch")
    for key in ("current_digest", "stored_digest"):
        require(_valid_hash(snapshot.get(key)), "missing or malformed attestation digest")
    require(isinstance(snapshot.get("database"), str) and snapshot["database"], "database identity missing")
    require(type(snapshot.get("server_version_num")) is int, "server version missing")
    entries = snapshot.get("entries")
    require(isinstance(entries, list) and 0 < len(entries) <= 100_000, "invalid catalog entry count")
    grouped = defaultdict(list)
    for entry in entries:
        require(isinstance(entry, dict) and set(entry) == {"category", "object", "sha256"}, "invalid catalog entry")
        require(isinstance(entry["category"], str) and entry["category"] in CATEGORIES, "unknown catalog category")
        require(
            isinstance(entry["object"], str) and 0 < len(entry["object"]) <= 1024, "invalid catalog object identity"
        )
        require(not any(ord(char) < 32 for char in entry["object"]), "control character in object identity")
        require(_valid_hash(entry["sha256"]), "invalid catalog entry hash")
        grouped[(entry["category"], entry["object"])].append(entry["sha256"])
    return {key: sorted(values) for key, values in grouped.items()}


def compare(before, after):
    left, right = validate(before), validate(after)
    require(before["login"] == after["login"], "cross-login comparison refused")
    require(before["database"] == after["database"], "cross-database-name comparison refused")
    require(before["server_version_num"] == after["server_version_num"], "server-version comparison refused")
    changes = [
        {"category": key[0], "object": key[1], "before": left.get(key, []), "after": right.get(key, [])}
        for key in sorted(left.keys() | right.keys())
        if left.get(key, []) != right.get(key, [])
    ]
    return {
        "contract_version": VERSION,
        "source_sha256": SOURCE_SHA256,
        "login": before["login"],
        "predecessor_receipt_matches": before["stored_digest"] == before["current_digest"],
        "successor_receipt_matches": after["stored_digest"] == after["current_digest"],
        "receipt_changed": before["stored_digest"] != after["stored_digest"],
        "changes": changes,
        "transition_authorized": False,
        "receipt_refresh_allowed": False,
        "limitations": [
            "Supplied snapshot hashes/booleans are not signatures or authenticated database provenance.",
            "A database name/server version is not proof of the same cluster or uninterrupted transaction.",
            "Changes include grant/owner/body differences; an object-name allowlist cannot authorize them.",
            "This report never grants authority to refresh stored attestation receipts.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    emit = commands.add_parser("emit-sql")
    emit.add_argument("--login", choices=LOGINS, required=True)
    diff = commands.add_parser("compare")
    diff.add_argument("--before", type=Path, required=True)
    diff.add_argument("--after", type=Path, required=True)
    diff.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "emit-sql":
            print(emit_sql(args.login), end="")
        else:
            before, before_sha = read_snapshot(args.before)
            after, after_sha = read_snapshot(args.after)
            result = compare(before, after)
            result["provenance"] = {
                "before_sha256": before_sha,
                "after_sha256": after_sha,
                "tool_sha256": sha256(Path(__file__).read_bytes()),
            }
            raw = (json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
            with args.output.open("xb") as stream:
                stream.write(raw)
            print(f"{len(result['changes'])} changed catalog keys; output sha256={sha256(raw)}; authorized=false")
    except (OSError, ValueError, TypeError, OverflowError):
        print(
            "Boundary report refused: invalid inputs or unavailable output; no input values disclosed.", file=sys.stderr
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
