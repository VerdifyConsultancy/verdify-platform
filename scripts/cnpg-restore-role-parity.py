"""Fail-closed password-free role custody for an isolated CNPG import.

Never change a CNPG management identity. The only possible source collision is
postgres, whose complete CREATE/ALTER statements must already match exactly.
Default membership comparison is strict. The explicit logical-target bootstrap
profile records raw grantor differences separately from typed equivalence; it
never normalizes owners, ACLs, member names or flags.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

MANAGEMENT = {"postgres", "streaming_replica", "rehearsal_bootstrap", "cnpg_metrics_exporter"}
DIRECT = re.compile(r"^(?:CREATE|ALTER) ROLE (?:\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*))(?:;| )")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(text):
    lines = [line for line in text.splitlines() if line and not line.startswith(("\\restrict ", "\\unrestrict "))]
    require(not any(" PASSWORD " in line for line in lines), "password-bearing role artifact refused")
    return lines


def role(line):
    match = DIRECT.match(line)
    return (match[1] or match[2]) if match else None


METRICS_PROFILE = {
    "CREATE ROLE cnpg_metrics_exporter;",
    "ALTER ROLE cnpg_metrics_exporter WITH NOSUPERUSER INHERIT NOCREATEROLE NOCREATEDB LOGIN NOREPLICATION NOBYPASSRLS;",
    "GRANT pg_monitor TO cnpg_metrics_exporter WITH INHERIT TRUE GRANTED BY postgres;",
}


def check_metrics(lines):
    actual = {line for line in lines if re.search(r"\bcnpg_metrics_exporter\b", line)}
    require(not actual or actual == METRICS_PROFILE, "CNPG metrics management posture/membership drift")


def legacy_prepare(source, current):
    source, current = canonical(source), canonical(current)
    check_metrics(current)
    require(not any(re.search(r"\bcnpg_metrics_exporter\b", line) for line in source), "management/source collision")
    existing = {role(line) for line in current if line.startswith("CREATE ROLE ")}
    require(existing <= MANAGEMENT and "postgres" in existing, "target contains non-management roles")
    require(not any(role(line) in MANAGEMENT - {"postgres"} for line in source), "management/source collision")
    if any(role(line) == "postgres" for line in source):
        require(
            [line for line in source if role(line) == "postgres"]
            == [line for line in current if role(line) == "postgres"],
            "postgres posture mismatch",
        )
    # Preserve the artifact verbatim except exactly proven identical management
    # CREATE/ALTER commands. Do not replay source privileges onto managed roles.
    return "\n".join(line for line in source if role(line) != "postgres") + "\n"


IDENTIFIER = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_]*)'
MEMBERSHIP = re.compile(
    rf"^GRANT ({IDENTIFIER}) TO ({IDENTIFIER}) WITH (?:ADMIN|INHERIT|SET) (?:TRUE|FALSE)(?:, (?:ADMIN|INHERIT|SET) (?:TRUE|FALSE))* GRANTED BY ({IDENTIFIER});$"
)


BOOTSTRAP_PROFILE = "cnpg-source-bootstrap-grantor-v1"


def bootstrap_mapping(source_witness, source_roles, target_identity):
    require(source_witness["roles"].get("10") == "verdify", "frozen source native bootstrap identity required")
    require(
        target_identity == {"oid": 10, "name": "postgres", "superuser": True},
        "native target bootstrap identity required",
    )
    require(
        any(role(line) == "verdify" and " WITH SUPERUSER " in line for line in canonical(source_roles)),
        "exact source bootstrap superuser posture required",
    )
    return {
        "profile": BOOTSTRAP_PROFILE,
        "source_oid": 10,
        "source_name": "verdify",
        "target_oid": 10,
        "target_name": "postgres",
    }


def translated_membership(line, mapping):
    match = MEMBERSHIP.fullmatch(line)
    require(match is not None, "unsupported source membership shape")
    require(
        mapping
        == {
            "profile": BOOTSTRAP_PROFILE,
            "source_oid": 10,
            "source_name": "verdify",
            "target_oid": 10,
            "target_name": "postgres",
        },
        "unsupported bootstrap translation",
    )
    require(match[3] == mapping["source_name"], "non-bootstrap source grantor refused")
    return line[: match.start(3)] + mapping["target_name"] + line[match.end(3) :]


def grant_context(source, *, memberships_only=False, mapping=None):
    lines = canonical(source)
    grants = [line for line in lines if line.startswith("GRANT ")]
    if not grants:
        require(not memberships_only, "source membership boundary required")
        return "\n".join(lines) + "\n"
    result = [] if memberships_only else [line for line in lines if not line.startswith("GRANT ")]
    result.append("BEGIN;")
    for line in grants:
        match = MEMBERSHIP.fullmatch(line)
        require(match is not None, "unsupported source membership shape")
        source_grantor = match[3]
        grantor = mapping["target_name"] if mapping else source_grantor
        require(
            any(
                role(entry) == source_grantor.strip('"')
                and entry.startswith("ALTER ROLE ")
                and " WITH SUPERUSER " in entry
                for entry in lines
            ),
            "exact source superuser grantor required",
        )
        statement = translated_membership(line, mapping) if mapping else line
        result.extend([f"SET SESSION AUTHORIZATION {grantor};", statement, "RESET SESSION AUTHORIZATION;"])
    result.append("COMMIT;")
    return "\n".join(result) + "\n"


def prepare(source, current, mapping=None):
    return grant_context(legacy_prepare(source, current), mapping=mapping)


def management_profile(text):
    return sorted(
        line
        for line in canonical(text)
        if role(line) in MANAGEMENT
        or re.search(r"\b(?:cnpg_metrics_exporter|streaming_replica|rehearsal_bootstrap)\b", line)
    )


def prefix_descriptor(raw):
    import json

    data = json.loads(raw)
    require(
        set(data)
        == {"stage_name", "manifest_sha256", "before_sha256", "replay_sha256", "error_sha256", "current_sha256"},
        "exact prefix custody fields required",
    )
    require(re.fullmatch(r"restore-custody-[a-z0-9]{8,32}", data["stage_name"]), "invalid partial custody path")
    require(
        all(re.fullmatch(r"[0-9a-f]{64}", value) for key, value in data.items() if key != "stage_name"),
        "invalid partial custody digest",
    )
    return data


def continue_prefix(source, current, management, descriptor, captured, before, replay, error, mapping=None):
    for key, raw in [
        ("current_sha256", captured),
        ("before_sha256", before),
        ("replay_sha256", replay),
        ("error_sha256", error),
    ]:
        require(hashlib.sha256(raw).hexdigest() == descriptor[key], "partial custody hash mismatch")
    require(canonical(captured.decode()) == canonical(current), "partial current role drift")
    require(canonical(before.decode()) == canonical(management), "original management boundary changed")
    require(management_profile(current) == management_profile(management), "partial management posture drift")
    original = legacy_prepare(source, before.decode())
    require(original.encode() == replay, "partial replay is not original source replay")
    first = next((i for i, line in enumerate(original.splitlines(), 1) if line.startswith("GRANT ")), None)
    require(first is not None, "original membership boundary absent")
    grantor = MEMBERSHIP.fullmatch(original.splitlines()[first - 1])
    require(grantor is not None, "unsupported failed membership")
    stage = "/var/lib/postgresql/data/" + descriptor["stage_name"]
    expected = f'psql:{stage}/work/roles.replay.sql:{first}: ERROR:  permission denied to grant privileges as role "{grantor[3]}"\nDETAIL:  The grantor must have the ADMIN option on role "{grantor[1]}".\n'
    require(error.decode() == expected, "unknown partial failure boundary")
    prefix = "\n".join(line for line in canonical(source) if not line.startswith("GRANT ")) + "\n"
    verify(prefix, current)  # Exact source attrs/settings; zero source memberships.
    return grant_context(source, memberships_only=True, mapping=mapping)


def verify(source, restored, mapping=None):
    source, restored = canonical(source), canonical(restored)
    check_metrics(restored)
    require(not any(re.search(r"\bcnpg_metrics_exporter\b", line) for line in source), "management/source collision")
    names = {role(line) for line in source if line.startswith("CREATE ROLE ")}
    extras = MANAGEMENT - names
    filtered = [
        line
        for line in restored
        if role(line) not in extras and not ("cnpg_metrics_exporter" in extras and line in METRICS_PROFILE)
    ]
    expected = [
        translated_membership(line, mapping) if mapping and line.startswith("GRANT ") else line for line in source
    ]
    require(sorted(expected) == sorted(filtered), "restored role posture/settings/membership drift")
    deltas = [
        {"source": line, "target": translated_membership(line, mapping)}
        for line in source
        if mapping and line.startswith("GRANT ")
    ]

    actual_names = {role(line) for line in restored if line.startswith("CREATE ROLE ")}
    return {
        "source_roles": len(names),
        "role_byte_parity": sorted(source) == sorted(filtered),
        "role_posture_settings_and_membership_options_equal": True,
        "bootstrap_grantor_translation": mapping,
        "raw_membership_differences": deltas,
        "separately_enumerated_management_roles": sorted(actual_names - names),
        "metrics_management_profile_verified": bool(METRICS_PROFILE <= set(restored)),
    }


def restore_summary(report):
    if report["role_byte_parity"]:
        return "[restore-pair] exact password-free role parity=true"
    require(
        report["role_posture_settings_and_membership_options_equal"]
        and report["bootstrap_grantor_translation"]
        and report["bootstrap_grantor_translation"]["profile"] == BOOTSTRAP_PROFILE
        and report["raw_membership_differences"],
        "unqualified non-byte-parity summary refused",
    )
    return (
        "[restore-pair] raw password-free role byte parity=false "
        "bootstrap-grantor-equivalence=true "
        f"retained_grantor_differences={len(report['raw_membership_differences'])}"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--management-before", type=Path)
    parser.add_argument("--management-before-sha256")
    parser.add_argument("--role-prefix-custody", type=Path)
    parser.add_argument("--role-prefix-current", type=Path)
    parser.add_argument("--restore-summary", action="store_true")
    parser.add_argument("--bootstrap-identity", type=Path)
    parser.add_argument("--source-witness", type=Path)
    parser.add_argument("--source-witness-sha256")
    args = parser.parse_args()
    source, current = args.source.read_text(), args.current.read_text()
    mapping = None
    if args.bootstrap_identity:
        import importlib.util
        import json

        require(args.source_witness and args.source_witness_sha256, "frozen source bootstrap custody required")
        spec = importlib.util.spec_from_file_location(
            "c0_witness", Path(__file__).with_name("cnpg-c0-restore-qualification.py")
        )
        c0 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(c0)
        witness, sha = c0.read_witness(args.source_witness)
        require(sha == args.source_witness_sha256, "frozen source bootstrap custody mismatch")
        c0.checked(witness, target=False)
        mapping = bootstrap_mapping(witness, source, json.loads(args.bootstrap_identity.read_text()))
    else:
        require(not args.source_witness and not args.source_witness_sha256, "unexpected bootstrap custody")
    if args.management_before:
        raw = args.management_before.read_bytes()
        require(
            hashlib.sha256(raw).hexdigest() == args.management_before_sha256, "management predecessor custody mismatch"
        )
        if not args.role_prefix_custody:
            require(canonical(raw.decode()) == canonical(current), "management predecessor semantic drift")
        check_metrics(canonical(current))
    else:
        require(not args.management_before_sha256, "incomplete management predecessor")
    if args.role_prefix_custody:
        require(args.replay and args.role_prefix_current and args.management_before, "incomplete partial continuation")
        descriptor = prefix_descriptor(args.role_prefix_custody.read_text())
        old = Path("/var/lib/postgresql/data") / descriptor["stage_name"]
        require(old.resolve() == old, "partial custody symlink refused")
        require(
            hashlib.sha256((old / "custody.sha256").read_bytes()).hexdigest() == descriptor["manifest_sha256"],
            "partial manifest mismatch",
        )
        result = continue_prefix(
            source,
            current,
            raw.decode(),
            descriptor,
            args.role_prefix_current.read_bytes(),
            (old / "work/roles.before.sql").read_bytes(),
            (old / "work/roles.replay.sql").read_bytes(),
            (old / "work/roles.stderr").read_bytes(),
            mapping=mapping,
        )
    else:
        require(not args.role_prefix_current, "unexpected partial role artifact")
        result = prepare(source, current, mapping=mapping) if args.replay else None
    if args.replay:
        require(not args.restore_summary, "summary requires completed verification")
        with args.replay.open("x") as stream:
            stream.write(result)
    else:
        report = verify(source, current, mapping=mapping)
        print(report)
        if args.restore_summary:
            print(restore_summary(report))


if __name__ == "__main__":
    main()
