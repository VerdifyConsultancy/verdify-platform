#!/usr/bin/env python3
"""Run synthetic containment only in the existing admitted native Kubernetes CI.

PostgreSQL toolchain resolver reuses agents verdify_campaign_ci.py at 26c275536.
No ambient database credential/address is passed to pytest. No live device calls.
"""

import hashlib
import json
import os
import subprocess
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


def postgres_bin(workspace: Path, installed=Path("/usr/lib/postgresql/16/bin")) -> Path:
    if (installed / "initdb").is_file() and Path("/usr/share/postgresql/16/extension/pgcrypto.control").is_file():
        return installed
    # Migration 217 reads pg_auth_members.inherit_option, introduced in PG16.
    # Resolve signed Debian/PGDG packages in private state, download only, then
    # extract without running package installation scripts or starting services.
    package_root = workspace / "postgres-toolchain"
    package_root.mkdir()
    lists = package_root / "lists"
    (lists / "partial").mkdir(parents=True)
    archives = package_root / "archives"
    (archives / "partial").mkdir(parents=True)
    key = package_root / "pgdg.asc"
    raw = urllib.request.urlopen("https://www.postgresql.org/media/keys/ACCC4CF8.asc", timeout=30).read(65536)
    if hashlib.sha256(raw).hexdigest() != "0144068502a1eddd2a0280ede10ef607d1ec592ce819940991203941564e8e76":
        raise RuntimeError("PGDG signing key does not match the pinned authority")
    key.write_bytes(raw)
    sources = package_root / "pgdg.list"
    sources.write_text(f"deb [signed-by={key}] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main\n")
    apt = [
        "apt-get",
        "-o",
        f"Dir::State::lists={lists}",
        "-o",
        f"Dir::Cache::archives={archives}",
        "-o",
        f"Dir::Etc::sourcelist={sources}",
        "-o",
        "Debug::NoLocking=true",
        "-o",
        "Acquire::AllowInsecureRepositories=false",
    ]
    subprocess.run([*apt, "update"], check=True, cwd=package_root)
    subprocess.run(
        [
            *apt,
            "--download-only",
            "--reinstall",
            "--no-install-recommends",
            "-y",
            "install",
            "postgresql-16",
            "postgresql-client-16",
        ],
        check=True,
        cwd=package_root,
    )
    root = package_root / "root"
    for package in archives.glob("*.deb"):
        subprocess.run(["dpkg-deb", "-x", str(package), str(root)], check=True)
    binaries = root / "usr/lib/postgresql/16/bin"
    if (
        not (binaries / "initdb").is_file()
        or not (root / "usr/share/postgresql/16/extension/pgcrypto.control").is_file()
    ):
        raise RuntimeError("private PostgreSQL 16/pgcrypto toolchain is incomplete")
    libraries = sorted(str(p) for p in (root / "usr/lib").glob("*-linux-gnu") if p.is_dir())
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(libraries)
    subprocess.run([str(binaries / "postgres"), "--version"], check=True)
    return binaries


def main():
    if not os.environ.get("KUBERNETES_SERVICE_HOST") or os.uname().sysname != "Linux":
        raise RuntimeError("containment requires the admitted in-cluster CI path")
    report = {
        "schema": "verdify.private-component-containment/1",
        "source": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "synthetic_only": True,
        "production_acceptance": False,
        "historical_prefix_credit": False,
        "qualified": False,
    }
    selected = "tests/test_component_containment_postgres.py"
    report["fixture_sha256"] = hashlib.sha256(Path(selected).read_bytes()).hexdigest()
    report["migrations"] = {
        name: hashlib.sha256(Path(name).read_bytes()).hexdigest()
        for name in (
            "db/schema.sql",
            "db/migrations/tests/test-214-confirmed-component-experiment-v2.sql",
            "db/migrations/207-controlled-policy-experiment.sql",
            "db/migrations/214-confirmed-component-experiment-v2.sql",
        )
    }
    with tempfile.TemporaryDirectory(prefix="component-ci-") as temporary:
        work = Path(temporary)
        work.chmod(0o755)
        binaries = postgres_bin(work)
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("PG", "DB_", "POSTGRES_", "VERDIFY_")) and k != "DATABASE_URL"
        }
        env["SCORECARD_TEST_PG_BIN"] = str(binaries)
        env["PYTHONPATH"] = os.pathsep.join((str(Path.cwd()), str(Path.cwd() / "ingestor")))
        output = work / "results"
        output.mkdir()
        junit = output / "junit.xml"
        try:
            result = subprocess.run(
                [".venv/bin/python", "-m", "pytest", "-q", "-p", "no:cacheprovider", selected, f"--junitxml={junit}"],
                env=env,
                timeout=600,
            )
            cases = list(ET.parse(junit).iter("testcase")) if junit.is_file() else []  # noqa: S314 - private pytest output
            report["cases"] = [
                {
                    "name": case.attrib["name"],
                    "passed": not any(case.find(tag) is not None for tag in ("failure", "error", "skipped")),
                }
                for case in cases
            ]
            report["qualified"] = (
                result.returncode == 0 and len(cases) >= 4 and all(c["passed"] for c in report["cases"])
            )
        finally:
            Path("component-containment-ci-report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report), flush=True)
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
