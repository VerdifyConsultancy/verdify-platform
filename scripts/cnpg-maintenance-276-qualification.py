"""Emit computed-digest projection for one already admitted CNPG275 clone.

No connection/execution API. Raw production migration/guards and canonical
receipt rows stay unchanged. Projection is native qualification, not production
profile or authenticated LOGIN qualification.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLUSTER = "verdify-cnpg-s2-integration"
CATALOG_SHA = "4355a2c83fbb260c3a6a5c483c46369dd115a24258bbdeb260d59f49557112b5"
ADMISSION_SHA = "7ad77910e380c467b6045ebf0a3df1f62b64b95c0fc0321f8f91bea8d73244ac"
SOURCE_SHA = {
    "db/migrations/276-ordinary-maintenance-runtime-boundaries.sql": "7509d3f84e64c92ef0539be15c3a64c4fb60f9f84bb2e053b4246d9ae4331764",
    "scripts/bootstrap-six-runtime-roles.py": "40b795ddf5432249dd2ad0f9d1f91426bf13f68de967c9c2d0ec00575346c651",
    "scripts/bootstrap-maintenance-runtime-roles.py": "440db6f600b5a62d8d2ed446637ee57d0e8239a8c86d31f89451a136b950f4e9",
}
LOGINS = ("verdify_api_runtime_login", "verdify_ingestor_runtime_login", "verdify_mcp_runtime_login")
CANONICAL = (
    "5091627a4dfd40c679ed4e2c0729cde507f705ab9c45c1e2eb82b42d29634bba",
    "8690d5fc25a742cdd4abbbb940b0f000f708ef567e5ff301fbfd3d27f449a743",
    "7083e4d43044e7f2b0150b58fa3cc8cc45c599a68b1f3cafaf9242e0f0f79e68",
)
NATIVE = (
    "1fc0d802b94b0e87b28a46662c17c80203075ae4caf3c13c6c73cb5f4256430c",
    "6bae416d783bd87bf9ebf1cea3c9feecc392bdb14644acd78abc6961405b0480",
    "ff0b3b40487e50e5a9b398ee6243a66ba7decb7e7f76b637657d733bfbca1f5e",
)
CALL = re.compile(r"(?<![\w'\"])public\.fn_(?:runtime_ordinary_boundary_digest|mcp_runtime_boundary_digest)\([^()]*\)")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require_source():
    for name, expected in SOURCE_SHA.items():
        if sha((ROOT / name).read_bytes()) != expected:
            raise ValueError("exact production source required")


def remap_computed(sql):
    """Only exact computed calls map native to canonical; unknown yields NULL.

    Existing canonical receipt/ledger predicates remain byte-for-byte. A native
    mismatch maps to NULL, so both equality and IS DISTINCT FROM guards refuse.
    The separate fixed-target admission guard is mandatory before using output.
    """
    changes = []

    def replace(match):
        original = match[0]
        argument = original.split("(", 1)[1][:-1]
        mcp = original.startswith("public.fn_mcp_")
        if mcp:
            if argument:
                raise ValueError("MCP computed source shape changed")
            pairs = [(None, NATIVE[2], CANONICAL[2])]
        elif argument in ("r.login_name", "s.login_name"):
            pairs = list(zip(LOGINS[:2], NATIVE[:2], CANONICAL[:2], strict=True))
        elif argument in (repr(LOGINS[0]), repr(LOGINS[1])):
            index = LOGINS.index(argument[1:-1])
            pairs = [(None, NATIVE[index], CANONICAL[index])]
        else:
            raise ValueError("ordinary computed source shape changed")
        conditions = []
        for login, native, canonical in pairs:
            scope = "" if login is None else f"{argument}='{login}' AND "
            conditions.append(f"WHEN {scope}{original}=decode('{native}','hex') THEN decode('{canonical}','hex')")
        projected = "(CASE " + " ".join(conditions) + " ELSE NULL::bytea END)"
        changes.append({"start": match.start(), "end": match.end(), "original": original, "projected": projected})
        return projected

    result = CALL.sub(replace, sql)
    if not changes:
        raise ValueError("no computed-digest comparisons to project")
    return result, changes


def migration():
    require_source()
    return remap_computed((ROOT / next(iter(SOURCE_SHA))).read_text())


def admission_guard(catalog_sha):
    """Owner must first capture the exact full accepted catalog, not a label."""
    if catalog_sha != CATALOG_SHA:
        raise ValueError("exact accepted CNPG275 catalog required")
    spec = importlib.util.spec_from_file_location(
        "maintenance276_private_existing_projection", ROOT / "scripts/cnpg-s2-integration-current275-transition.py"
    )
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    target = bridge.transition.configured(CLUSTER)
    shape = target.target_receipt_shape(CLUSTER)
    checks = [
        "current_database()='verdify_rehearsal'",
        f"current_setting('cluster_name')='{CLUSTER}'",
        "current_setting('server_version_num')='160013'",
        "NOT pg_is_in_recovery()",
        "inet_server_addr() IS NULL",
        "current_user='postgres' AND session_user='postgres'",
        "(" + shape + ")",
        "(SELECT count(*)=3 FROM public.cnpg_s2_275_runtime_receipts)",
    ]
    for index, login in enumerate(LOGINS):
        computed = (
            "public.fn_mcp_runtime_boundary_digest()"
            if index == 2
            else f"public.fn_runtime_ordinary_boundary_digest('{login}')"
        )
        checks.append(
            "EXISTS(SELECT 1 FROM public.cnpg_s2_275_runtime_receipts WHERE login_name='"
            + login
            + "' AND qualification_sha256='"
            + ADMISSION_SHA
            + "' AND boundary_sha256=decode('"
            + NATIVE[index]
            + "','hex') AND boundary_sha256="
            + computed
            + ")"
        )
    return "(" + " AND ".join(checks) + ")"
