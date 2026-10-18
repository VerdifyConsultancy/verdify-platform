"""Offline, bounded source/ledger validator. This script makes no DB calls."""

import argparse
import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_source(contract):
    """Reproduce these 13 declaration hashes from owning source, not live output.

    This deliberately supports only the reviewed declaration shapes in this
    packet. An unsupported future declaration fails instead of being inferred.
    """
    for path, sha in contract["migrations"].items():
        require(digest((ROOT / path).read_bytes()) == sha, f"local owning source drift: {path}")
    for function in contract["functions"]:
        source = (ROOT / function["path"]).read_text()
        pattern = rf"CREATE OR REPLACE FUNCTION public\.{function['name']}\((.*?)\)\s*RETURNS\s+(.*?)\s+LANGUAGE\s+(\w+)\s+(.*?)AS\s+(\$\w*\$)(.*?)\5"
        matches = list(re.finditer(pattern, source, re.S | re.I))
        require(len(matches) == 1, f"source declaration is ambiguous: {function['name']}")
        args, result, language, flags, _, body = matches[0].groups()
        args = " ".join(args.split()).replace("timestamptz", "timestamp with time zone")
        args = re.sub(r"DEFAULT 'vallery'(?!::)", "DEFAULT 'vallery'::text", args)
        result = " ".join(result.split()).replace("timestamptz", "timestamp with time zone")
        result = re.sub(r"TABLE\(\s*", "TABLE(", result)
        result = re.sub(r"\s*\)$", ")", result)
        flags = " ".join(flags.split())
        require(bool(re.fullmatch(r"(STABLE|IMMUTABLE)( STRICT)?( ROWS 1)?", flags)), "unsupported function flags")
        definition = (
            f"CREATE OR REPLACE FUNCTION public.{function['name']}({args})\n"
            f" RETURNS {result}\n LANGUAGE {language}\n {flags}\nAS $function${body}$function$\n"
        )
        require(digest(body.encode()) == function["prosrc_sha256"], "source body hash mismatch")
        require(digest(definition.encode()) == function["definition_sha256"], "source definition hash mismatch")
    require(
        digest((ROOT / contract["native_reference_path"]).read_bytes()) == contract["native_reference_sha256"],
        "native reference drift",
    )


def verify(raw, contract):
    require(len(raw) <= 65536, "identity output exceeds 64 KiB")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    require(len(rows) == 22, "expected exactly 22 metadata rows")
    contexts = [row for row in rows if row.get("kind") == "context"]
    require(len(contexts) == 1, "expected one context")
    context = contexts[0]
    require(context["database"] == contract["expected_database"], "wrong database")
    require(context["current_user"] == context["session_user"] == "verdify", "unexpected probe identity")
    require(context["read_only"] == "on", "probe was not read-only")
    require(context["server_version_num"].startswith("16"), "unexpected PostgreSQL major")
    night = date.fromisoformat(context["denver_date"]) - timedelta(days=2)
    require(context["latest_complete_adjacent_day_night"] == night.isoformat(), "night selection drift")
    functions = [row for row in rows if row.get("kind") == "function"]
    require(len(functions) == 13, "expected exactly 13 function rows")
    require(len({row["requested_name"] for row in functions}) == 13, "duplicate function/overload")
    actual = {row["requested_name"]: row for row in functions}
    require(set(actual) == {row["name"] for row in contract["functions"]}, "function inventory mismatch")
    for expected in contract["functions"]:
        row = actual[expected["name"]]
        require(row["oid"] is not None and row["schema"] == "public", "missing public function")
        for field in (
            "name",
            "argument_types",
            "result",
            "language",
            "volatility",
            "security_definer",
            "owner",
            "config",
            "prosrc_sha256",
            "definition_sha256",
        ):
            require(row[field] == expected[field], f"{expected['name']}: {field} mismatch")
    require(
        actual["fn_realized_solar_night_dryout"]["definition_sha256"] == contract["native_dryout_definition_sha256"],
        "retained native definition mismatch",
    )
    migrations = [row for row in rows if row.get("kind") == "migration"]
    require(len(migrations) == 8, "expected eight migration rows")
    require(len({row["requested_filename"] for row in migrations}) == 8, "duplicate migration")
    require(
        {row["requested_filename"] for row in migrations} == set(contract["migrations"]), "ledger inventory mismatch"
    )
    for row in migrations:
        require(row["filename"] == row["requested_filename"], "missing migration")
        require(row["source"] == "db/migrations", "wrong migration source")
        require(row["sha256"] == contract["migrations"][row["filename"]], "migration hash mismatch")
        require(row["stamp_method"] in {"baseline", "runner"} and row["applied_at"], "missing ledger provenance")
    return {
        "status": "INSTALLED_SOURCE_AND_LEDGER_MATCH",
        "identity_sha256": digest(raw),
        "context": context,
        "function_count": len(functions),
        "migration_count": len(migrations),
        "migration_stamp_methods": sorted({row["stamp_method"] for row in migrations}),
        "preselected_pilot_night": night.isoformat(),
        "optional_prior_nights": [(night - timedelta(days=n)).isoformat() for n in (1, 2)],
        "optional_expansion_authorized_by_this_receipt": False,
        "episode_data_read": False,
        "whole_issue_acceptance": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("identity", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    contract_bytes = (HERE / "source-contract.json").read_bytes()
    contract = json.loads(contract_bytes)
    verify_source(contract)
    result = verify(args.identity.read_bytes(), contract)
    result["source_contract_sha256"] = digest(contract_bytes)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
